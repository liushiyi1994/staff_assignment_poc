#!/usr/bin/env python3
"""Build the all-project TAWOS slice report without restoring MySQL.

The official archive expands to a 4+ GB MySQL dump. This script streams selected
``INSERT`` statements directly from the zip, retaining aggregate statistics plus
only the assignee/resolution/project/key transitions needed for temporal safety.
It is a reproducible fallback for environments where Docker/MySQL is unavailable;
Stage 0 itself still uses the relational schema and produces the normalized export.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml


TABLES = ("Issue", "Project", "Repository")
DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
EXPECTED_ARCHIVE_SIZE_BYTES = 637_550_449
EXPECTED_ARCHIVE_SHA256 = (
    "278984f788008c58d338e1f4aa195eae8e5b15b4153e51c247659ef8465917f7"
)


@dataclass(frozen=True)
class ReportParameters:
    cutoff: str
    min_tickets: int
    min_brief_chars: int
    recommended_projects: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveMetadata:
    filename: str
    size_bytes: int
    sha256: str


def validate_report_parameters(parameters: ReportParameters) -> ReportParameters:
    if not parameters.cutoff.strip():
        raise ValueError("dataset.holdout_cutoff must not be empty")
    if parameters.min_tickets < 1:
        raise ValueError("dataset.min_tickets_per_person must be at least 1")
    if parameters.min_brief_chars < 0:
        raise ValueError("eval.min_brief_chars must be non-negative")
    if not parameters.recommended_projects:
        raise ValueError("dataset.projects must contain at least one project")
    if len(set(parameters.recommended_projects)) != len(parameters.recommended_projects):
        raise ValueError("dataset.projects must not contain duplicates")
    return parameters


def load_report_parameters(settings_path: Path) -> ReportParameters:
    """Load report defaults from the pipeline's canonical settings file."""
    config = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    try:
        parameters = ReportParameters(
            cutoff=str(config["dataset"]["holdout_cutoff"]),
            min_tickets=int(config["dataset"]["min_tickets_per_person"]),
            min_brief_chars=int(config["eval"]["min_brief_chars"]),
            recommended_projects=tuple(map(str, config["dataset"]["projects"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid slice-report settings in {settings_path}: {exc}") from exc
    return validate_report_parameters(parameters)


def resolve_report_parameters(args: argparse.Namespace) -> ReportParameters:
    """Apply explicit CLI overrides to settings-backed report parameters."""
    configured = load_report_parameters(args.settings)
    return validate_report_parameters(
        ReportParameters(
            cutoff=args.cutoff if args.cutoff is not None else configured.cutoff,
            min_tickets=(
                args.min_tickets if args.min_tickets is not None else configured.min_tickets
            ),
            min_brief_chars=(
                args.min_brief_chars
                if args.min_brief_chars is not None
                else configured.min_brief_chars
            ),
            recommended_projects=configured.recommended_projects,
        )
    )


def verify_archive(
    archive: Path,
    *,
    expected_size_bytes: int = EXPECTED_ARCHIVE_SIZE_BYTES,
    expected_sha256: str = EXPECTED_ARCHIVE_SHA256,
) -> ArchiveMetadata:
    """Verify the pinned source artifact before any canonical report is replaced."""
    size_bytes = archive.stat().st_size
    if size_bytes != expected_size_bytes:
        raise ValueError(
            f"archive size mismatch for {archive}: expected {expected_size_bytes:,} bytes, "
            f"found {size_bytes:,}"
        )
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    if sha256 != expected_sha256.casefold():
        raise ValueError(
            f"archive SHA-256 mismatch for {archive}: expected {expected_sha256}, found {sha256}"
        )
    return ArchiveMetadata(archive.name, size_bytes, sha256)


@dataclass(frozen=True)
class SqlField:
    raw: bytes
    quoted: bool


def _mysql_unescape(raw: bytes) -> str:
    replacements = {
        ord("0"): "\0",
        ord("b"): "\b",
        ord("n"): "\n",
        ord("r"): "\r",
        ord("t"): "\t",
        ord("Z"): "\x1a",
        ord("\\"): "\\",
        ord("'"): "'",
        ord('"'): '"',
    }
    out: list[str] = []
    start = 0
    i = 0
    while i < len(raw):
        if raw[i] != 92:  # backslash
            i += 1
            continue
        if start < i:
            out.append(raw[start:i].decode("utf-8", errors="replace"))
        i += 1
        if i >= len(raw):
            out.append("\\")
            start = i
            break
        out.append(replacements.get(raw[i], chr(raw[i])))
        i += 1
        start = i
    if start < len(raw):
        out.append(raw[start:].decode("utf-8", errors="replace"))
    return "".join(out)


def field_text(value: SqlField) -> str | None:
    if not value.quoted and value.raw.strip().upper() == b"NULL":
        return None
    raw = value.raw if value.quoted else value.raw.strip()
    return _mysql_unescape(raw)


def field_int(value: SqlField) -> int | None:
    text = field_text(value)
    return int(text) if text not in (None, "") else None


class InsertParser:
    """Incrementally parse one ``VALUES (...),(...);`` byte stream."""

    def __init__(self, callback: Callable[[list[SqlField]], None]) -> None:
        self.callback = callback
        self.row: list[SqlField] | None = None
        self.value = bytearray()
        self.quoted_value = False
        self.in_string = False
        self.escaped = False

    def _finish_value(self) -> None:
        assert self.row is not None
        self.row.append(SqlField(bytes(self.value), self.quoted_value))
        self.value.clear()
        self.quoted_value = False

    def feed(self, data: bytes) -> tuple[int, bool]:
        for index, byte in enumerate(data):
            if self.in_string:
                if self.escaped:
                    self.value.append(byte)
                    self.escaped = False
                elif byte == 92:  # preserve escape for field_text
                    self.value.append(byte)
                    self.escaped = True
                elif byte == 39:  # closing single quote
                    self.in_string = False
                else:
                    self.value.append(byte)
                continue

            if self.row is None:
                if byte == 40:  # (
                    self.row = []
                elif byte == 59:  # ;
                    return index + 1, True
                continue

            if byte == 39:  # opening single quote
                self.in_string = True
                self.quoted_value = True
            elif byte == 44:  # ,
                self._finish_value()
            elif byte == 41:  # )
                self._finish_value()
                row = self.row
                self.row = None
                self.callback(row)
            else:
                self.value.append(byte)
        return len(data), False


def stream_inserts(
    archive: Path,
    callbacks: dict[str, Callable[[list[SqlField]], None]],
    chunk_size: int = 8 * 1024 * 1024,
) -> None:
    markers = {name: f"INSERT INTO `{name}` VALUES ".encode() for name in callbacks}
    tail_size = max(map(len, markers.values())) - 1
    active: InsertParser | None = None
    active_name = ""
    announced: set[str] = set()
    search_tail = b""

    with zipfile.ZipFile(archive) as zf:
        members = zf.namelist()
        if len(members) != 1:
            raise ValueError(f"expected one SQL member, found {members}")
        with zf.open(members[0]) as source:
            while chunk := source.read(chunk_size):
                data = search_tail + chunk
                search_tail = b""
                while data:
                    if active is not None:
                        consumed, done = active.feed(data)
                        data = data[consumed:]
                        if done:
                            active = None
                            active_name = ""
                            continue
                        data = b""
                        continue

                    hits = [
                        (position, name, marker)
                        for name, marker in markers.items()
                        if (position := data.find(marker)) >= 0
                    ]
                    if not hits:
                        search_tail = data[-tail_size:]
                        data = b""
                        continue
                    position, active_name, marker = min(hits, key=lambda hit: hit[0])
                    if active_name not in announced:
                        print(f"parsing {active_name}", file=sys.stderr)
                        announced.add(active_name)
                    active = InsertParser(callbacks[active_name])
                    data = data[position + len(marker):]

    if active is not None:
        raise ValueError(f"unterminated INSERT for {active_name}")


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


_TAG_RE = re.compile(r"<[^>]+>")
_MACRO_RE = re.compile(r"\{[^}\n]{0,80}\}")
_SPACE_RE = re.compile(r"\s+")


def cleaned_text_length(title: str | None, description: str | None) -> int:
    combined = " ".join(part for part in (title, description) if part)
    combined = html.unescape(_TAG_RE.sub(" ", combined))
    combined = _MACRO_RE.sub(" ", combined)
    return len(_SPACE_RE.sub(" ", combined).strip())


@dataclass
class ProjectStats:
    total: int = 0
    resolved: int = 0
    assigned: int = 0
    summary: int = 0
    description: int = 0
    summary_or_description: int = 0
    assignees: set[int] = field(default_factory=set)
    first_created: str | None = None
    last_created: str | None = None
    pre_created: int = 0
    post_created: int = 0
    pre_resolved_by_person: Counter[int] = field(default_factory=Counter)
    heldout_candidates: list[tuple[int, int]] = field(default_factory=list)


class ReportBuilder:
    def __init__(self, cutoff: str, min_tickets: int, min_brief_chars: int) -> None:
        self.cutoff = cutoff
        self.min_tickets = min_tickets
        self.min_brief_chars = min_brief_chars
        self.stats: defaultdict[int, ProjectStats] = defaultdict(ProjectStats)
        self.projects: dict[int, tuple[str, str, int | None]] = {}
        self.repositories: dict[int, str] = {}
        self.assignee_changes: defaultdict[
            int, list[tuple[str | None, int, int | None, int | None]]
        ] = defaultdict(list)
        self.moved_issue_ids: set[int] = set()
        self.resolution_date_changed_issue_ids: set[int] = set()
        self.undated_resolution_issue_ids: set[int] = set()
        self.latest_resolution_change: dict[int, tuple[str, int, bool]] = {}
        self.assignee_change_count = 0
        self.issue_count = 0

    @staticmethod
    def _user_id(*values: SqlField) -> int | None:
        for value in values:
            text = field_text(value)
            if text is not None and re.fullmatch(r"[0-9]+", text.strip()):
                return int(text)
        return None

    def change_log(self, row: list[SqlField]) -> None:
        if len(row) != 10:
            raise ValueError(f"Change_Log row has {len(row)} fields, expected 10")
        # The official dump places Change_Log before Issue. Enforce that ordering
        # because it lets us discard each issue's transitions as Issue is streamed.
        if self.issue_count:
            raise ValueError("official dump unexpectedly placed Change_Log after Issue")
        field_name = (field_text(row[1]) or "").strip().casefold()
        issue_id = field_int(row[9])
        if issue_id is None:
            return
        changed_at = field_text(row[7])
        if field_name in {"project", "key"}:
            self.moved_issue_ids.add(issue_id)
            return
        if field_name == "resolutiondate":
            self.resolution_date_changed_issue_ids.add(issue_id)
            return
        if field_name == "resolution":
            if changed_at is None:
                self.undated_resolution_issue_ids.add(issue_id)
            else:
                change_id = field_int(row[0]) or -1
                to_sets_resolution = any(
                    bool((field_text(row[index]) or "").strip()) for index in (3, 5)
                )
                candidate = (changed_at, change_id, to_sets_resolution)
                current = self.latest_resolution_change.get(issue_id)
                if current is None or candidate[:2] > current[:2]:
                    self.latest_resolution_change[issue_id] = candidate
            return
        if field_name != "assignee":
            return
        self.assignee_change_count += 1
        self.assignee_changes[issue_id].append(
            (
                changed_at,
                field_int(row[0]) or -1,
                self._user_id(row[2], row[4]),
                self._user_id(row[3], row[5]),
            )
        )

    def _resolution_owner(
        self,
        issue_id: int | None,
        resolution_date: str | None,
        final_assignee_id: int | None,
    ) -> int | None:
        if issue_id is None:
            return None
        changes = self.assignee_changes.pop(issue_id, [])
        if resolution_date is None:
            return None
        if not changes:
            return final_assignee_id
        # An undated mutation makes the historical state unknowable.
        if any(changed_at is None for changed_at, _, _, _ in changes):
            return None
        changes.sort(key=lambda change: (change[0] or "", change[1]))
        at_or_before = [change for change in changes if change[0] <= resolution_date]
        if at_or_before:
            return at_or_before[-1][3]
        after = [change for change in changes if change[0] > resolution_date]
        if after:
            return after[0][2]
        return final_assignee_id

    def _resolution_boundary(
        self, issue_id: int | None, snapshot_resolution_date: str | None
    ) -> tuple[str | None, bool]:
        if issue_id is None:
            return None, True
        moved = issue_id in self.moved_issue_ids
        resolution_date_changed = issue_id in self.resolution_date_changed_issue_ids
        undated_resolution = issue_id in self.undated_resolution_issue_ids
        self.moved_issue_ids.discard(issue_id)
        self.resolution_date_changed_issue_ids.discard(issue_id)
        self.undated_resolution_issue_ids.discard(issue_id)
        latest_transition = self.latest_resolution_change.pop(issue_id, None)
        unsafe = moved or resolution_date_changed or undated_resolution
        if latest_transition is not None and not latest_transition[2]:
            unsafe = True
        if snapshot_resolution_date is None or unsafe:
            return None, unsafe
        if latest_transition is not None:
            return max(snapshot_resolution_date, latest_transition[0]), False
        return snapshot_resolution_date, False

    def issue(self, row: list[SqlField]) -> None:
        if len(row) != 30:
            raise ValueError(f"Issue row has {len(row)} fields, expected 30")
        self.issue_count += 1
        if self.issue_count % 50_000 == 0:
            print(f"  {self.issue_count:,} issues", file=sys.stderr)

        issue_id = field_int(row[0])
        title = field_text(row[4])
        raw_description = field_text(row[5])
        text_description = field_text(row[6])
        description = text_description if _present(text_description) else raw_description
        resolution_date = field_text(row[14])
        creation_date = field_text(row[12])
        assignee_id = field_int(row[27])
        evidence_resolution_date, _ = self._resolution_boundary(
            issue_id, resolution_date
        )
        resolution_owner_id = self._resolution_owner(
            issue_id, evidence_resolution_date, assignee_id
        )
        project_id = field_int(row[28])
        if project_id is None:
            return

        stats = self.stats[project_id]
        stats.total += 1
        if resolution_date is not None:
            stats.resolved += 1
        if assignee_id is not None:
            stats.assigned += 1
            stats.assignees.add(assignee_id)
        if _present(title):
            stats.summary += 1
        if _present(description):
            stats.description += 1
        if _present(title) or _present(description):
            stats.summary_or_description += 1

        if creation_date is not None:
            stats.first_created = min(stats.first_created or creation_date, creation_date)
            stats.last_created = max(stats.last_created or creation_date, creation_date)
            if creation_date < self.cutoff:
                stats.pre_created += 1
            else:
                stats.post_created += 1

        # Eligibility uses only tickets that were actually resolved before cutoff.
        if (
            resolution_owner_id is not None
            and creation_date is not None
            and creation_date < self.cutoff
            and evidence_resolution_date is not None
            and evidence_resolution_date < self.cutoff
        ):
            stats.pre_resolved_by_person[resolution_owner_id] += 1

        # A plausible held-out brief starts after cutoff, has a resolved assignee as
        # truth, and enough issue-authored text. Roster eligibility is applied later.
        if (
            resolution_owner_id is not None
            and creation_date is not None
            and creation_date >= self.cutoff
            and evidence_resolution_date is not None
        ):
            stats.heldout_candidates.append(
                (resolution_owner_id, cleaned_text_length(title, description))
            )

    def project(self, row: list[SqlField]) -> None:
        if len(row) != 9:
            raise ValueError(f"Project row has {len(row)} fields, expected 9")
        project_id = field_int(row[0])
        if project_id is not None:
            self.projects[project_id] = (
                field_text(row[1]) or f"PROJECT-{project_id}",
                field_text(row[2]) or "",
                field_int(row[8]),
            )

    def repository(self, row: list[SqlField]) -> None:
        if len(row) != 4:
            raise ValueError(f"Repository row has {len(row)} fields, expected 4")
        repository_id = field_int(row[0])
        if repository_id is not None:
            self.repositories[repository_id] = field_text(row[1]) or ""

    @staticmethod
    def _pct(numerator: int, denominator: int) -> str:
        return f"{100 * numerator / denominator:.2f}" if denominator else "0.00"

    def rows(self) -> list[dict[str, str | int]]:
        output: list[dict[str, str | int]] = []
        for project_id, stats in self.stats.items():
            key, name, repository_id = self.projects.get(
                project_id, (f"PROJECT-{project_id}", "", None)
            )
            ticket_threshold_people = {
                person_id
                for person_id, count in stats.pre_resolved_by_person.items()
                if count >= self.min_tickets
            }
            plausible = sum(
                person_id in ticket_threshold_people
                and text_length >= self.min_brief_chars
                for person_id, text_length in stats.heldout_candidates
            )
            output.append(
                {
                    "project_key": key,
                    "project_name": name,
                    "repository": self.repositories.get(repository_id or -1, ""),
                    "total_tickets": stats.total,
                    "resolved_tickets": stats.resolved,
                    "assignee_tickets": stats.assigned,
                    "assignee_coverage_pct": self._pct(stats.assigned, stats.total),
                    "nonempty_summary_tickets": stats.summary,
                    "summary_coverage_pct": self._pct(stats.summary, stats.total),
                    "nonempty_description_tickets": stats.description,
                    "description_coverage_pct": self._pct(stats.description, stats.total),
                    "nonempty_summary_or_description_tickets": stats.summary_or_description,
                    "text_coverage_pct": self._pct(stats.summary_or_description, stats.total),
                    "distinct_assignees": len(stats.assignees),
                    "first_created_at": stats.first_created or "",
                    "last_created_at": stats.last_created or "",
                    "pre_cutoff_created_tickets": stats.pre_created,
                    "post_cutoff_created_tickets": stats.post_created,
                    "people_meeting_minimum_pre_cutoff_resolved_tickets": len(
                        ticket_threshold_people
                    ),
                    "plausible_heldout_briefs": plausible,
                }
            )
        return sorted(output, key=lambda row: str(row["project_key"]))


def write_csv(path: Path, rows: list[dict[str, str | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path,
    rows: list[dict[str, str | int]],
    parameters: ReportParameters,
    archive_metadata: ArchiveMetadata,
) -> None:
    selected = [
        row for row in rows if row["project_key"] in parameters.recommended_projects
    ]
    selected_totals = {
        "issues": sum(int(row["total_tickets"]) for row in selected),
        "pre_cutoff": sum(int(row["pre_cutoff_created_tickets"]) for row in selected),
        "people": sum(
            int(row["people_meeting_minimum_pre_cutoff_resolved_tickets"])
            for row in selected
        ),
        "briefs": sum(int(row["plausible_heldout_briefs"]) for row in selected),
    }
    selected_display = ", ".join(
        f"`{project}`" for project in parameters.recommended_projects
    )
    columns = [
        ("project_key", "Project"),
        ("total_tickets", "Total"),
        ("resolved_tickets", "Resolved"),
        ("assignee_coverage_pct", "Assigned %"),
        ("summary_coverage_pct", "Summary %"),
        ("description_coverage_pct", "Description %"),
        ("distinct_assignees", "Assignees"),
        ("first_created_at", "First created"),
        ("last_created_at", "Last created"),
        ("pre_cutoff_created_tickets", "Pre-cutoff"),
        ("post_cutoff_created_tickets", "Post-cutoff"),
        (
            "people_meeting_minimum_pre_cutoff_resolved_tickets",
            f"People ≥{parameters.min_tickets}",
        ),
        ("plausible_heldout_briefs", "Plausible briefs"),
    ]
    lines = [
        "# TAWOS v1.1 project slice report",
        "",
        "## Reproduction metadata",
        "",
        f"- Source archive: `{archive_metadata.filename}`",
        f"- Archive bytes: `{archive_metadata.size_bytes}`",
        f"- Archive SHA-256: `{archive_metadata.sha256}`",
        f"- Effective cutoff: `{parameters.cutoff}`",
        f"- Effective minimum resolved tickets per person: `{parameters.min_tickets}`",
        f"- Effective minimum brief characters: `{parameters.min_brief_chars}`",
        "- Effective recommended projects: `"
        + ",".join(parameters.recommended_projects)
        + "`",
        "- Machine-readable metadata: `slice_report.metadata.json`",
        "",
        f"Cutoff: `{parameters.cutoff}` (UTC-naive timestamps as stored by TAWOS).",
        "Pre/post counts use issue creation time. The preliminary ticket-threshold screen "
        "uses only tickets created and resolved before the cutoff, reconstructs the assignee "
        f"at resolution from Change_Log, and requires at least {parameters.min_tickets} such "
        "tickets in the same "
        "project. Project/key moves, explicit resolution-date mutations, and latest "
        "resolution clears are excluded; other dated resolution transitions can only "
        "move the safe boundary later. A plausible "
        "held-out brief is a resolved issue created on or after the "
        f"cutoff, owned at resolution by a person meeting that threshold, with at least "
        f"{parameters.min_brief_chars} "
        "characters of cleaned snapshot title plus description. This is an upper-bound "
        "estimate: final roster eligibility also requires a retained/indexable Stage 1 "
        "profile, and the manifest reconstructs creation-time text and may exclude edited "
        "fields. Comments are excluded because they are created after query time.",
        "",
        "## Recommended PoC slice",
        "",
        f"The effective project selection ({selected_display}) balances domain "
        "diversity, pre-cutoff roster depth, text/assignment coverage, and post-cutoff "
        "benchmark headroom. Together they contain "
        f"{selected_totals['issues']:,} source issues, {selected_totals['pre_cutoff']:,} "
        f"created before cutoff, {selected_totals['people']:,} project-qualified people "
        "meeting the pre-cutoff ticket threshold, and "
        f"{selected_totals['briefs']:,} upper-bound plausible held-out briefs before "
        "retained-profile, creation-text, deterministic sampling, and other exclusions.",
        "",
        "| " + " | ".join(label for _, label in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        values = [str(row[key]).replace("|", "\\|") for key, _ in columns]
        lines.append("| " + " | ".join(values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_metadata(
    path: Path,
    parameters: ReportParameters,
    archive_metadata: ArchiveMetadata,
) -> None:
    metadata = {
        "report_metadata_version": 1,
        "source_archive": {
            "filename": archive_metadata.filename,
            "size_bytes": archive_metadata.size_bytes,
            "sha256": archive_metadata.sha256,
        },
        "effective_parameters": {
            "cutoff": parameters.cutoff,
            "min_tickets_per_person": parameters.min_tickets,
            "min_brief_chars": parameters.min_brief_chars,
            "recommended_projects": list(parameters.recommended_projects),
        },
    }
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_report(
    archive: Path,
    output_dir: Path,
    cutoff: str,
    min_tickets: int,
    min_brief_chars: int,
    recommended_projects: tuple[str, ...] | None = None,
) -> list[dict[str, str | int]]:
    cutoff_sql = cutoff if " " in cutoff else f"{cutoff} 00:00:00"
    if recommended_projects is None:
        recommended_projects = load_report_parameters(
            DEFAULT_SETTINGS_PATH
        ).recommended_projects
    parameters = validate_report_parameters(
        ReportParameters(
            cutoff=cutoff_sql,
            min_tickets=min_tickets,
            min_brief_chars=min_brief_chars,
            recommended_projects=recommended_projects,
        )
    )
    archive_metadata = verify_archive(archive)
    builder = ReportBuilder(cutoff_sql, min_tickets, min_brief_chars)
    stream_inserts(
        archive,
        {
            "Change_Log": builder.change_log,
            "Issue": builder.issue,
            "Project": builder.project,
            "Repository": builder.repository,
        },
    )
    if builder.issue_count != 458_232:
        raise ValueError(
            f"expected 458,232 TAWOS v1.1 issues, parsed {builder.issue_count:,}"
        )
    rows = builder.rows()
    if len(rows) != 39:
        raise ValueError(f"expected 39 TAWOS v1.1 projects, parsed {len(rows)}")
    if len(builder.repositories) != 12:
        raise ValueError(
            f"expected 12 TAWOS v1.1 repositories, parsed {len(builder.repositories)}"
        )
    if builder.assignee_changes:
        raise ValueError(
            f"{len(builder.assignee_changes):,} issues retained unmatched assignee changes"
        )
    unmatched_temporal = (
        len(builder.moved_issue_ids)
        + len(builder.resolution_date_changed_issue_ids)
        + len(builder.undated_resolution_issue_ids)
        + len(builder.latest_resolution_change)
    )
    if unmatched_temporal:
        raise ValueError(f"{unmatched_temporal:,} temporal issue states were unmatched")
    write_csv(output_dir / "slice_report.csv", rows)
    write_markdown(output_dir / "slice_report.md", rows, parameters, archive_metadata)
    write_metadata(
        output_dir / "slice_report.metadata.json", parameters, archive_metadata
    )
    return rows


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=Path("data/raw/TAWOS.sql.zip"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/parquet"))
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH)
    parser.add_argument("--cutoff")
    parser.add_argument("--min-tickets", type=int)
    parser.add_argument("--min-brief-chars", type=int)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    parameters = resolve_report_parameters(args)
    rows = build_report(
        args.archive,
        args.output_dir,
        parameters.cutoff,
        parameters.min_tickets,
        parameters.min_brief_chars,
        parameters.recommended_projects,
    )
    print(
        f"Wrote {len(rows)} projects to "
        f"{args.output_dir / 'slice_report.csv'}, {args.output_dir / 'slice_report.md'}, and "
        f"{args.output_dir / 'slice_report.metadata.json'}"
    )


if __name__ == "__main__":
    main()
