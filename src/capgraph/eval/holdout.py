"""Build a deterministic, auditable temporal benchmark manifest.

The benchmark treats an issue as a query at its creation time.  TAWOS v1.1 does
not expose assignment-history timestamps through the normalized Stage 0 contract,
so resolution time is deliberately *not* used as query time.  Candidate eligibility
and all profile evidence are frozen strictly before both the query time and the
configured graph cutoff.

``build_briefs`` writes two files:

* ``benchmark_manifest.v1.jsonl`` contains every post-cutoff candidate considered,
  including exclusions and the frozen eligible roster.
* ``briefs.jsonl`` is a compatibility view containing only selected validation/test
  cases in the existing :class:`capgraph.models.EvalBrief` shape.  It carries the
  same as-of time and roster; ``resolved_at`` is outcome metadata only.

Stage 0 reconstructs creation-time summary/description from the real TAWOS
``Change_Log`` when edits are recorded and omits fields whose timing cannot be made
safe. ``query_time_source`` and the field-provenance columns keep that assumption
explicit and auditable; later comments are never substituted into query text.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from ..models import EvalBrief
from ..privacy import LeakageSanitizer, roster_identifiers
from ..pipeline.stage1_bucket import (
    profile_eligible_person_ids,
    validate_profile_evidence,
)
from ..settings import DATA_DIR, settings

MANIFEST_VERSION = str(settings.get("eval.manifest_version", "tawos-v1.1-benchmark-v1"))
DEFAULT_SEED = 20260713
MANIFEST_PATH = DATA_DIR / "eval" / "benchmark_manifest.v1.jsonl"
BRIEFS_PATH = DATA_DIR / "eval" / "briefs.jsonl"

class BenchmarkManifestEntry(BaseModel):
    """One auditable benchmark decision, selected or excluded."""

    manifest_version: str = MANIFEST_VERSION
    seed: int
    issue_id: str                    # stable TAWOS Issue.ID
    issue_key: str = ""              # final Jira key, audit/display only
    query_text: str
    as_of_time: datetime | None
    query_time_source: Literal["created_at"] = "created_at"
    project_key: str
    eligible_roster: list[str] = Field(default_factory=list)
    truth_person_ids: list[str] = Field(default_factory=list)
    split: Literal["validation", "test", "excluded"] = "excluded"
    exclusion_reason: str | None = None
    resolved_at: datetime | None = None


def _utc_naive(value: object) -> datetime | None:
    """Normalize a scalar timestamp for stable comparison and JSON serialization."""
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.to_pydatetime()


def _cutoff_datetime(value: str | date | datetime | pd.Timestamp) -> datetime:
    cutoff = _utc_naive(value)
    if cutoff is None:
        raise ValueError("holdout cutoff must be a valid date or datetime")
    return cutoff


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def filter_history_as_of(
    tickets: pd.DataFrame,
    *,
    query_time: str | date | datetime | pd.Timestamp,
    cutoff: str | date | datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Return resolved evidence strictly before ``min(query_time, cutoff)``.

    Both creation and resolution must precede the boundary.  Checking creation as
    well as resolution makes the no-future-evidence rule explicit even for malformed
    source rows.
    """
    boundary = min(_cutoff_datetime(query_time), _cutoff_datetime(cutoff))
    missing = {"created_at", "resolved_at"}.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing temporal columns: {sorted(missing)}")
    created = pd.to_datetime(tickets["created_at"], errors="coerce", utc=True).dt.tz_localize(
        None
    )
    resolved = pd.to_datetime(tickets["resolved_at"], errors="coerce", utc=True).dt.tz_localize(
        None
    )
    mask = created.notna() & resolved.notna() & (created < boundary) & (resolved < boundary)
    if "temporal_exclusion_reason" in tickets:
        mask &= tickets["temporal_exclusion_reason"].isna()
    history = tickets.loc[mask].copy()

    # Return an evidence view, not the audit snapshot. This prevents future
    # baselines from accidentally consuming final assignment/status metadata.
    for column in (
        "person_id",
        "person_name",
        "type",
        "resolution",
        "snapshot_resolved_at",
        "assigned_at",
    ):
        if column in history:
            history[column] = None
    if "components" in history:
        history["components"] = [[] for _ in range(len(history))]
    if "components_provenance" in history:
        history["components_provenance"] = "redacted_unversioned_component_name"
    if "assignee_provenance" in history:
        history["assignee_provenance"] = "final_outcome_redacted_from_evidence_view"
    return history


def freeze_eligible_roster(
    tickets: pd.DataFrame,
    *,
    query_time: str | date | datetime | pd.Timestamp,
    cutoff: str | date | datetime | pd.Timestamp,
    min_resolved_tickets: int,
    min_profile_bucket_tickets: int | None = None,
    max_profile_bucket_tickets: int | None = None,
    project_key: str | None = None,
    profile_person_ids: set[str] | None = None,
) -> list[str]:
    """Freeze one project's candidate roster from strictly prior history.

    Resolution-time evidence ownership is authoritative. Legacy normalized files
    without this explicit field are rejected rather than silently using final state.
    Every candidate must also have a Stage 1 bucket retained from that same frozen
    history, and an optional Stage 0 people roster can further constrain the result.
    """
    if min_resolved_tickets < 1:
        raise ValueError("min_resolved_tickets must be at least 1")
    min_bucket = int(
        min_profile_bucket_tickets
        if min_profile_bucket_tickets is not None
        else settings["bucketing.min_tickets_per_bucket"]
    )
    max_bucket = int(
        max_profile_bucket_tickets
        if max_profile_bucket_tickets is not None
        else settings["bucketing.max_tickets_per_bucket"]
    )
    boundary = min(_cutoff_datetime(query_time), _cutoff_datetime(cutoff))
    eligible = profile_eligible_person_ids(
        tickets,
        cutoff=boundary,
        min_resolved_tickets=min_resolved_tickets,
        min_tickets_per_bucket=min_bucket,
        max_tickets_per_bucket=max_bucket,
        project_key=project_key,
    )
    if profile_person_ids is not None:
        eligible &= profile_person_ids
    return sorted(eligible)


def _truth_person_id(row: pd.Series) -> str:
    """Return the assignee reconstructed at resolution."""
    return _text(row.get("evidence_person_id"))


def _query_text_provenance_is_safe(row: pd.Series) -> bool:
    """Reject normalized rows explicitly marked as carrying mutable final text."""
    for text_column, provenance_column in (
        ("summary", "summary_provenance"),
        ("description", "description_provenance"),
    ):
        if not _text(row.get(text_column)) or provenance_column not in row.index:
            continue
        provenance = _text(row.get(provenance_column))
        if not (
            provenance.startswith("change_log_from_")
            or provenance == "snapshot_no_recorded_change"
        ):
            return False
    return True


def _stable_score(seed: int, project_key: str, issue_id: str, purpose: str) -> bytes:
    value = f"{MANIFEST_VERSION}\0{seed}\0{purpose}\0{project_key}\0{issue_id}"
    return hashlib.sha256(value.encode("utf-8")).digest()


def _select_stratified(
    eligible_indexes: list[int],
    entries: list[BenchmarkManifestEntry],
    *,
    n_briefs: int,
    seed: int,
) -> set[int]:
    """Select a deterministic, project-balanced sample by round robin."""
    if n_briefs < 0:
        raise ValueError("n_briefs cannot be negative")
    by_project: dict[str, list[int]] = defaultdict(list)
    for index in eligible_indexes:
        by_project[entries[index].project_key].append(index)
    for project_key, indexes in by_project.items():
        indexes.sort(
            key=lambda index: _stable_score(
                seed, project_key, entries[index].issue_id, "sample"
            )
        )

    selected: set[int] = set()
    projects = sorted(by_project)
    while len(selected) < min(n_briefs, len(eligible_indexes)):
        made_progress = False
        for project_key in projects:
            indexes = by_project[project_key]
            if indexes and len(selected) < n_briefs:
                selected.add(indexes.pop(0))
                made_progress = True
        if not made_progress:
            break
    return selected


def _assign_splits(
    selected: set[int],
    entries: list[BenchmarkManifestEntry],
    *,
    seed: int,
    validation_fraction: float,
) -> None:
    """Assign deterministic, project-stratified validation/test splits in place."""
    if not 0 <= validation_fraction < 1:
        raise ValueError("validation_fraction must be in [0, 1)")
    by_project: dict[str, list[int]] = defaultdict(list)
    for index in selected:
        by_project[entries[index].project_key].append(index)
    for project_key, indexes in sorted(by_project.items()):
        indexes.sort(
            key=lambda index: _stable_score(
                seed, project_key, entries[index].issue_id, "split"
            )
        )
        n_validation = int(len(indexes) * validation_fraction)
        if validation_fraction and len(indexes) >= 5:
            n_validation = max(1, n_validation)
        validation = set(indexes[:n_validation])
        for index in indexes:
            entries[index].split = "validation" if index in validation else "test"


def _candidate_rows(tickets: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    """Include new issues plus old issues that the obsolete resolved-time rule selected."""
    missing = {"created_at", "resolved_at"}.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing temporal columns: {sorted(missing)}")
    created = pd.to_datetime(tickets["created_at"], errors="coerce", utc=True).dt.tz_localize(
        None
    )
    resolved = pd.to_datetime(tickets["resolved_at"], errors="coerce", utc=True).dt.tz_localize(
        None
    )
    return tickets.loc[(created >= cutoff) | (resolved >= cutoff)].copy()


def build_manifest(
    tickets: pd.DataFrame,
    people: pd.DataFrame | None = None,
    *,
    cutoff: str | date | datetime | pd.Timestamp,
    min_resolved_tickets: int,
    min_brief_chars: int,
    n_briefs: int,
    seed: int = DEFAULT_SEED,
    validation_fraction: float = 0.2,
    min_profile_bucket_tickets: int | None = None,
    max_profile_bucket_tickets: int | None = None,
) -> list[BenchmarkManifestEntry]:
    """Build deterministic selected/excluded entries from normalized Stage 0 rows."""
    validate_profile_evidence(tickets, cutoff=cutoff)
    required = {
        "source_issue_id",
        "key",
        "project_key",
        "evidence_person_id",
        "summary",
        "summary_provenance",
        "description",
        "description_provenance",
        "created_at",
        "resolved_at",
        "resolved_at_provenance",
        "query_time_source",
        "temporal_exclusion_reason",
    }
    missing = required.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing hardened Stage 0 columns: {sorted(missing)}")
    cutoff_dt = _cutoff_datetime(cutoff)
    min_bucket = int(
        min_profile_bucket_tickets
        if min_profile_bucket_tickets is not None
        else settings["bucketing.min_tickets_per_bucket"]
    )
    max_bucket = int(
        max_profile_bucket_tickets
        if max_profile_bucket_tickets is not None
        else settings["bucketing.max_tickets_per_bucket"]
    )
    identifier_source = people if people is not None else tickets
    identifiers = roster_identifiers(identifier_source)
    sanitizer = LeakageSanitizer(identifiers)
    profile_person_ids: set[str] | None = None
    if people is not None:
        if "person_id" not in people:
            raise ValueError("people are missing Stage 0 person_id")
        profile_person_ids = {
            str(person_id).strip()
            for person_id in people["person_id"].dropna()
            if str(person_id).strip()
        }
        expected_profile_person_ids = profile_eligible_person_ids(
            tickets,
            cutoff=cutoff_dt,
            min_resolved_tickets=min_resolved_tickets,
            min_tickets_per_bucket=min_bucket,
            max_tickets_per_bucket=max_bucket,
        )
        if profile_person_ids != expected_profile_person_ids:
            missing_profiles = sorted(expected_profile_person_ids - profile_person_ids)
            unexpected_profiles = sorted(profile_person_ids - expected_profile_person_ids)
            raise ValueError(
                "people roster is stale or inconsistent with retained-profile eligibility: "
                f"missing={missing_profiles}, unexpected={unexpected_profiles}"
            )

    candidates = _candidate_rows(tickets, cutoff_dt)
    sort_columns = [
        column
        for column in (
            "project_key",
            "key",
            "created_at",
            "resolved_at",
            "source_issue_id",
        )
        if column in candidates
    ]
    if sort_columns:
        candidates = candidates.sort_values(sort_columns, kind="stable", na_position="last")

    entries: list[BenchmarkManifestEntry] = []
    eligible_indexes: list[int] = []
    rosters_by_project: dict[str, list[str]] = {}
    for ordinal, (_, row) in enumerate(candidates.iterrows()):
        issue_id = _text(row.get("source_issue_id"))
        issue_key = _text(row.get("key"))
        project_key = _text(row.get("project_key"))
        created_at = _utc_naive(row.get("created_at"))
        resolved_at = _utc_naive(row.get("resolved_at"))
        person_id = _truth_person_id(row)
        truth = [person_id] if person_id else []
        if project_key not in rosters_by_project:
            rosters_by_project[project_key] = freeze_eligible_roster(
                tickets,
                query_time=cutoff_dt,
                cutoff=cutoff_dt,
                min_resolved_tickets=min_resolved_tickets,
                min_profile_bucket_tickets=min_bucket,
                max_profile_bucket_tickets=max_bucket,
                project_key=project_key,
                profile_person_ids=profile_person_ids,
            )
        roster = rosters_by_project[project_key]
        roster_set = set(roster)
        raw_text = "\n\n".join(
            part for part in (_text(row.get("summary")), _text(row.get("description"))) if part
        )
        query_text = sanitizer.strip(raw_text)

        # Missing stable IDs are retained with a deterministic content fingerprint.
        if not issue_id:
            fingerprint = hashlib.sha256(
                f"{project_key}\0{created_at}\0{raw_text}".encode("utf-8")
            ).hexdigest()[:16]
            issue_id = f"missing-source-id:{fingerprint}"

        exclusion_reason: str | None = None
        temporal_exclusion = _text(row.get("temporal_exclusion_reason"))
        if temporal_exclusion:
            exclusion_reason = f"stage0_temporal:{temporal_exclusion}"
        elif not _text(row.get("source_issue_id")):
            exclusion_reason = "missing_source_issue_id"
        elif created_at is None:
            exclusion_reason = "missing_query_time"
        elif created_at < cutoff_dt:
            exclusion_reason = "query_not_post_cutoff"
        elif not project_key:
            exclusion_reason = "missing_project_key"
        elif _text(row.get("query_time_source")) != "created_at":
            exclusion_reason = "unsupported_query_time_source"
        elif resolved_at is None:
            exclusion_reason = "unresolved_at_manifest_build"
        elif _text(row.get("resolved_at_provenance")) not in {
            "snapshot_no_recorded_resolution_change",
            "resolution_change_log_boundary",
        }:
            exclusion_reason = "unsafe_resolution_time_provenance"
        elif resolved_at < created_at:
            exclusion_reason = "resolved_before_created"
        elif not truth:
            exclusion_reason = "missing_truth_assignee"
        elif any(person_id not in roster_set for person_id in truth):
            exclusion_reason = "truth_not_eligible"
        elif not _query_text_provenance_is_safe(row):
            exclusion_reason = "unsafe_query_text_provenance"
        elif len(query_text) < min_brief_chars:
            exclusion_reason = "brief_too_short"
        elif sanitizer.contains(query_text):
            exclusion_reason = "leakage_guard_failed"

        entry = BenchmarkManifestEntry(
            seed=seed,
            issue_id=issue_id,
            issue_key=issue_key,
            query_text=query_text,
            as_of_time=created_at,
            project_key=project_key,
            eligible_roster=roster,
            truth_person_ids=truth,
            exclusion_reason=exclusion_reason,
            resolved_at=resolved_at,
        )
        entries.append(entry)
        if exclusion_reason is None:
            eligible_indexes.append(ordinal)

    selected = _select_stratified(
        eligible_indexes, entries, n_briefs=n_briefs, seed=seed
    )
    for index in eligible_indexes:
        if index not in selected:
            entries[index].exclusion_reason = "sampled_out"
    _assign_splits(
        selected,
        entries,
        seed=seed,
        validation_fraction=validation_fraction,
    )
    return entries


def write_manifest(
    entries: list[BenchmarkManifestEntry],
    *,
    manifest_path: Path = MANIFEST_PATH,
    briefs_path: Path = BRIEFS_PATH,
) -> None:
    """Write the full manifest and its backwards-compatible selected-case view."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(entry.model_dump_json() + "\n")

    briefs_path.parent.mkdir(parents=True, exist_ok=True)
    with briefs_path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            if entry.split == "excluded":
                continue
            if entry.resolved_at is None:
                raise ValueError(f"selected issue {entry.issue_id} has no resolution time")
            brief = EvalBrief(
                brief_id=entry.issue_id,
                text=entry.query_text,
                project_key=entry.project_key,
                as_of_time=entry.as_of_time,
                resolved_at=entry.resolved_at,
                eligible_roster=entry.eligible_roster,
                true_person_ids=entry.truth_person_ids,
            )
            handle.write(brief.model_dump_json() + "\n")


def build_briefs() -> list[BenchmarkManifestEntry]:
    """Build and persist the benchmark from normalized Stage 0 parquet files."""
    tickets_path = DATA_DIR / "parquet" / "tickets.parquet"
    people_path = DATA_DIR / "parquet" / "people.parquet"
    if not people_path.is_file():
        raise FileNotFoundError(
            f"missing Stage 0 people roster: {people_path}; run Stage 0 export first"
        )
    tickets = pd.read_parquet(tickets_path)
    people = pd.read_parquet(people_path)
    entries = build_manifest(
        tickets,
        people,
        cutoff=settings["dataset.holdout_cutoff"],
        min_resolved_tickets=settings["dataset.min_tickets_per_person"],
        min_brief_chars=settings["eval.min_brief_chars"],
        n_briefs=settings["eval.n_briefs"],
        seed=int(settings.get("eval.seed", DEFAULT_SEED)),
        validation_fraction=float(settings.get("eval.validation_fraction", 0.2)),
        min_profile_bucket_tickets=int(settings["bucketing.min_tickets_per_bucket"]),
        max_profile_bucket_tickets=int(settings["bucketing.max_tickets_per_bucket"]),
    )
    write_manifest(entries)
    selected = sum(entry.split != "excluded" for entry in entries)
    excluded = len(entries) - selected
    print(
        f"Benchmark manifest {MANIFEST_VERSION}: {selected} selected, {excluded} excluded "
        f"-> {MANIFEST_PATH}"
    )
    return entries


if __name__ == "__main__":
    build_briefs()
