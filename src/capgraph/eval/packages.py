"""Benchmark v4: build the work-package manifest.

    uv run python -m capgraph.eval.packages            # offline, no model call

A v1 case is one issue, asked at its creation time, whose truth is the one person who
resolved it.  A v4 case is one **work package** — a sprint — asked at the package's
recorded start date, whose brief is written from the issues that were planned into it
*before* it started and whose truth is **everyone** who resolved any of its issues from
that moment on.  That is the question the product actually asks, and it is the reason
``Recall@K`` stops being a synonym for ``Hit@K``.

Three rules carry over from v1 unchanged (``docs/work-orders/stage7-benchmark.md``) and
one is new:

* **Nothing after the as-of time reaches the brief.**  Brief material is the
  creation-time text of package issues created *and* joined to the package strictly
  before the as-of time.  Issues that joined later contribute to truth and to nothing
  else.
* **Membership needs a timestamp.**  ``Issue.Sprint_ID`` is the dump's final pointer
  with no recorded timing, so it can never say what was planned.  Planned membership
  comes only from dated sprint transitions in the change log; the final snapshot is
  used for truth, where timing is not what is being claimed.
* **Truth is reconstructed at the safe resolution boundary**, must be eligible in the
  roster frozen at the holdout cutoff, and must own a retained Stage 1 profile bucket.
  A package whose truth set empties out is excluded with a reason rather than rescued.
* **New: the brief is a frozen rewrite.**  ``query_text`` is the cheap-model rewrite of
  the raw package text, checkpointed by :mod:`capgraph.eval.rewrite` and frozen into
  this manifest, so every later run is deterministic and free.  The raw text stays in
  the manifest as ``brief_raw`` so the rewrite's own effect can be measured rather than
  assumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import Field

from ..models import EvalBrief
from ..pipeline.stage1_bucket import profile_eligible_person_ids, validate_profile_evidence
from ..privacy import LeakageSanitizer, roster_identifiers
from ..settings import DATA_DIR, settings
from .holdout import (
    BenchmarkManifestEntry,
    _assign_splits,
    _query_text_provenance_is_safe,
    _select_stratified,
    _text,
    _utc_naive,
    freeze_eligible_roster,
)

PACKAGE_MANIFEST_VERSION = str(
    settings.get("eval.v4.manifest_version", "tawos-v1.1-benchmark-v4")
)
DEFAULT_SEED = int(settings.get("eval.v4.seed", 20260814))
MANIFEST_PATH = DATA_DIR / "eval" / "benchmark_manifest.v4.jsonl"
BRIEFS_PATH = DATA_DIR / "eval" / "v4" / "briefs.jsonl"

CHANGE_LOG_JOIN = "change_log_sprint_join"
SNAPSHOT_JOIN = "final_snapshot_join_time_unknown"
SAFE_RESOLUTION_PROVENANCE = frozenset(
    {"snapshot_no_recorded_resolution_change", "resolution_change_log_boundary"}
)

REWRITTEN = "rewritten"
RAW = "raw"
BRIEF_VARIANTS = (REWRITTEN, RAW)


class PackageManifestEntry(BenchmarkManifestEntry):
    """One work-package case, selected or excluded, with its whole audit trail."""

    manifest_version: str = PACKAGE_MANIFEST_VERSION
    query_time_source: Literal["created_at", "sprint_start"] = "sprint_start"
    package_kind: Literal["sprint"] = "sprint"
    package_key: str = ""                    # "<project>:sprint:<jira id>"
    package_name: str = ""                   # sanitized sprint name, audit only
    package_ended_at: datetime | None = None
    # The brief in both variants. ``query_text`` mirrors whichever variant a run uses;
    # the manifest freezes the rewritten one as the benchmark's brief.
    brief_raw: str = ""
    brief_rewritten: str = ""
    brief_variant: Literal["rewritten", "raw"] = "rewritten"
    rewrite_model: str = ""
    rewrite_prompt_digest: str = ""
    # Hash of exactly the text the rewriter was shown. A rewrite whose input digest no
    # longer matches the raw brief is stale and is not used.
    rewrite_input_digest: str = ""
    # What the brief was built from, and what the truth was reconstructed from.
    brief_issue_keys: list[str] = Field(default_factory=list)
    brief_issue_count: int = 0
    brief_issues_omitted: int = 0
    package_issue_count: int = 0
    truth_issue_count: int = 0
    # Multi-person truth accounting: how many people worked the package at all, and how
    # many survived roster eligibility. The gap is this benchmark's survivorship, and it
    # is measured per case instead of silently dropping the case as v1 did.
    truth_person_count_all: int = 0
    truth_dropped_ineligible: int = 0


# ---------- brief construction ----------

def _issue_block(row: Mapping[str, object], sanitizer: LeakageSanitizer) -> str:
    """One issue's creation-time text, sanitized, as a paragraph of the raw brief."""
    summary = sanitizer.strip(_text(row.get("summary")))
    description = sanitizer.strip(_text(row.get("description")))
    return "\n".join(part for part in (summary, description) if part)


def brief_digest(text: str) -> str:
    """Stable fingerprint of the exact text a rewrite was produced from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------- package assembly ----------

def _package_rows(
    membership: pd.DataFrame, sprints: pd.DataFrame
) -> dict[tuple[str, int], dict[str, object]]:
    """Group memberships by (project, sprint jira id) with the sprint's calendar."""
    required = {"source_issue_id", "project_key", "sprint_jira_id", "added_at", "provenance"}
    missing = required.difference(membership.columns)
    if missing:
        raise ValueError(f"sprint membership is missing columns: {sorted(missing)}")
    missing = {"project_key", "sprint_jira_id", "start_date"}.difference(sprints.columns)
    if missing:
        raise ValueError(f"sprints are missing columns: {sorted(missing)}")

    calendar: dict[tuple[str, int], pd.Series] = {}
    for row in sprints.itertuples(index=False):
        if pd.isna(row.sprint_jira_id):
            continue
        calendar[(str(row.project_key), int(row.sprint_jira_id))] = row

    packages: dict[tuple[str, int], dict[str, object]] = {}
    for row in membership.itertuples(index=False):
        if pd.isna(row.sprint_jira_id):
            continue
        key = (str(row.project_key), int(row.sprint_jira_id))
        sprint = calendar.get(key)
        if sprint is None:
            # A sprint from a board outside the configured slice. It cannot be dated,
            # so it cannot be a package; membership in it is simply not a v4 fact.
            continue
        package = packages.setdefault(
            key,
            {"sprint": sprint, "members": set(), "joins": {}},
        )
        issue_id = str(row.source_issue_id)
        package["members"].add(issue_id)
        if str(row.provenance) == CHANGE_LOG_JOIN and not pd.isna(row.added_at):
            joined_at = pd.Timestamp(row.added_at)
            earliest = package["joins"].get(issue_id)
            if earliest is None or joined_at < earliest:
                package["joins"][issue_id] = joined_at
    return packages


def build_package_manifest(
    tickets: pd.DataFrame,
    sprints: pd.DataFrame,
    membership: pd.DataFrame,
    people: pd.DataFrame | None = None,
    *,
    cutoff: str | date | datetime | pd.Timestamp,
    min_resolved_tickets: int,
    min_brief_issues: int,
    max_brief_issues: int,
    max_brief_chars: int,
    min_brief_chars: int,
    min_rewritten_chars: int,
    n_packages: int,
    seed: int = DEFAULT_SEED,
    validation_fraction: float = 0.2,
    rewrites: Mapping[str, Mapping[str, object]] | None = None,
    min_profile_bucket_tickets: int | None = None,
    max_profile_bucket_tickets: int | None = None,
) -> list[PackageManifestEntry]:
    """Build every candidate package, selected or excluded, deterministically."""
    cutoff_dt = _utc_naive(cutoff)
    if cutoff_dt is None:
        raise ValueError("holdout cutoff must be a valid date or datetime")
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
    required = {
        "source_issue_id", "key", "project_key", "evidence_person_id", "summary",
        "summary_provenance", "description", "description_provenance", "created_at",
        "resolved_at", "resolved_at_provenance", "temporal_exclusion_reason",
    }
    missing = required.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing hardened Stage 0 columns: {sorted(missing)}")
    validate_profile_evidence(tickets, cutoff=cutoff_dt)

    sanitizer = LeakageSanitizer(
        roster_identifiers(people if people is not None else tickets)
    )
    profile_person_ids: set[str] | None = None
    if people is not None:
        if "person_id" not in people:
            raise ValueError("people are missing Stage 0 person_id")
        profile_person_ids = {
            str(person_id).strip()
            for person_id in people["person_id"].dropna()
            if str(person_id).strip()
        }
        # The roster is the benchmark's whole notion of who could be answered with, so
        # a stale people.parquet would quietly narrow every case's candidate set and
        # every truth set with it. Same check the v1 builder makes, same reason.
        expected = profile_eligible_person_ids(
            tickets,
            cutoff=cutoff_dt,
            min_resolved_tickets=min_resolved_tickets,
            min_tickets_per_bucket=min_bucket,
            max_tickets_per_bucket=max_bucket,
        )
        if profile_person_ids != expected:
            raise ValueError(
                "people roster is stale or inconsistent with retained-profile "
                f"eligibility: missing={sorted(expected - profile_person_ids)}, "
                f"unexpected={sorted(profile_person_ids - expected)}"
            )

    by_id = tickets.set_index(tickets["source_issue_id"].astype(str), drop=False)
    by_id = by_id[~by_id.index.duplicated(keep="first")]
    created = pd.to_datetime(by_id["created_at"], errors="coerce")
    resolved = pd.to_datetime(by_id["resolved_at"], errors="coerce")

    packages = _package_rows(membership, sprints)
    rosters: dict[str, list[str]] = {}
    rewrites = dict(rewrites or {})

    entries: list[PackageManifestEntry] = []
    eligible_indexes: list[int] = []
    for (project_key, sprint_jira_id), package in sorted(packages.items()):
        sprint = package["sprint"]
        as_of = _utc_naive(sprint.start_date)
        package_key = f"{project_key}:sprint:{sprint_jira_id}"
        package_name = sanitizer.strip(_text(getattr(sprint, "name", "")))
        members = sorted(str(value) for value in package["members"] if str(value) in by_id.index)

        if project_key not in rosters:
            rosters[project_key] = freeze_eligible_roster(
                tickets,
                query_time=cutoff_dt,
                cutoff=cutoff_dt,
                min_resolved_tickets=min_resolved_tickets,
                min_profile_bucket_tickets=min_bucket,
                max_profile_bucket_tickets=max_bucket,
                project_key=project_key,
                profile_person_ids=profile_person_ids,
            )
        roster = rosters[project_key]
        roster_set = set(roster)

        # ---- brief material: planned before the as-of time, created before it too ----
        planned: list[str] = []
        if as_of is not None:
            planned = sorted(
                issue_id
                for issue_id, joined_at in package["joins"].items()
                if issue_id in by_id.index and joined_at < as_of
            )
        usable: list[str] = []
        for issue_id in planned:
            row = by_id.loc[issue_id]
            if _text(row.get("temporal_exclusion_reason")):
                continue
            created_at = created.get(issue_id)
            if created_at is None or pd.isna(created_at) or created_at >= as_of:
                continue
            if not _query_text_provenance_is_safe(row):
                continue
            if not _issue_block(row, sanitizer):
                continue
            usable.append(issue_id)
        usable.sort(key=lambda issue_id: (created[issue_id], issue_id))

        blocks: list[str] = []
        brief_issue_ids: list[str] = []
        for issue_id in usable:
            block = _issue_block(by_id.loc[issue_id], sanitizer)
            if len(brief_issue_ids) >= max_brief_issues:
                break
            if blocks and sum(len(part) + 2 for part in blocks) + len(block) > max_brief_chars:
                break
            blocks.append(block)
            brief_issue_ids.append(issue_id)
        brief_raw = "\n\n".join(blocks)

        # ---- truth: everyone who resolved a package issue from the as-of time on ----
        truth_all: set[str] = set()
        truth_issue_count = 0
        if as_of is not None:
            for issue_id in members:
                row = by_id.loc[issue_id]
                if _text(row.get("temporal_exclusion_reason")):
                    continue
                resolved_at = resolved.get(issue_id)
                if resolved_at is None or pd.isna(resolved_at) or resolved_at < as_of:
                    continue
                if _text(row.get("resolved_at_provenance")) not in SAFE_RESOLUTION_PROVENANCE:
                    continue
                person_id = _text(row.get("evidence_person_id"))
                if not person_id:
                    continue
                truth_all.add(person_id)
                truth_issue_count += 1
        truth = sorted(truth_all & roster_set)

        # ---- the frozen rewrite ----
        rewrite = rewrites.get(package_key) or {}
        rewritten = _text(rewrite.get("brief"))
        input_digest = _text(rewrite.get("input_digest"))
        if input_digest and input_digest != brief_digest(brief_raw):
            # The package text moved under a checkpointed rewrite. Treat it as absent
            # rather than pairing a brief with material it was not written from.
            rewrite, rewritten, input_digest = {}, "", ""

        exclusion_reason: str | None = None
        if as_of is None:
            exclusion_reason = "sprint_start_missing"
        elif as_of < cutoff_dt:
            exclusion_reason = "sprint_start_not_post_cutoff"
        elif not package["joins"]:
            exclusion_reason = "no_dated_membership"
        elif not planned:
            exclusion_reason = "nothing_planned_before_start"
        elif len(brief_issue_ids) < min_brief_issues:
            exclusion_reason = "too_few_brief_issues"
        elif len(brief_raw) < min_brief_chars:
            exclusion_reason = "brief_too_short"
        elif sanitizer.contains(brief_raw):
            exclusion_reason = "leakage_guard_failed"
        elif not truth_all:
            exclusion_reason = "no_truth_resolver"
        elif not truth:
            exclusion_reason = "truth_not_eligible"
        elif not rewritten:
            exclusion_reason = "rewrite_pending"
        elif len(rewritten) < min_rewritten_chars:
            exclusion_reason = "rewrite_too_short"
        elif sanitizer.contains(rewritten):
            exclusion_reason = "rewrite_leakage_guard_failed"

        entries.append(
            PackageManifestEntry(
                seed=seed,
                issue_id=package_key,
                issue_key=package_name or package_key,
                package_key=package_key,
                package_name=package_name,
                query_text=rewritten,
                brief_raw=brief_raw,
                brief_rewritten=rewritten,
                rewrite_model=_text(rewrite.get("model")),
                rewrite_prompt_digest=_text(rewrite.get("prompt_digest")),
                rewrite_input_digest=input_digest,
                as_of_time=as_of,
                package_ended_at=_utc_naive(getattr(sprint, "complete_date", None))
                or _utc_naive(getattr(sprint, "end_date", None)),
                project_key=project_key,
                eligible_roster=roster,
                truth_person_ids=truth,
                brief_issue_keys=[_text(by_id.loc[i, "key"]) for i in brief_issue_ids],
                brief_issue_count=len(brief_issue_ids),
                brief_issues_omitted=len(usable) - len(brief_issue_ids),
                package_issue_count=len(members),
                truth_issue_count=truth_issue_count,
                truth_person_count_all=len(truth_all),
                truth_dropped_ineligible=len(truth_all - roster_set),
                exclusion_reason=exclusion_reason,
            )
        )
        if exclusion_reason is None:
            eligible_indexes.append(len(entries) - 1)

    selected = _select_stratified(
        eligible_indexes,
        entries,
        n_briefs=n_packages,
        seed=seed,
        version=PACKAGE_MANIFEST_VERSION,
    )
    for index in eligible_indexes:
        if index not in selected:
            entries[index].exclusion_reason = "sampled_out"
    _assign_splits(
        selected,
        entries,
        seed=seed,
        validation_fraction=validation_fraction,
        version=PACKAGE_MANIFEST_VERSION,
    )
    return entries


# ---------- persistence ----------

def write_package_manifest(
    entries: Sequence[PackageManifestEntry],
    *,
    manifest_path: Path = MANIFEST_PATH,
    briefs_path: Path = BRIEFS_PATH,
) -> None:
    """Write the full manifest plus the selected-case view, in a stable order."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(entry.model_dump_json() + "\n")

    briefs_path.parent.mkdir(parents=True, exist_ok=True)
    with briefs_path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            if entry.split == "excluded":
                continue
            handle.write(
                EvalBrief(
                    brief_id=entry.package_key,
                    text=entry.query_text,
                    project_key=entry.project_key,
                    as_of_time=entry.as_of_time,
                    resolved_at=entry.package_ended_at,
                    eligible_roster=entry.eligible_roster,
                    true_person_ids=entry.truth_person_ids,
                ).model_dump_json()
                + "\n"
            )


def load_package_manifest(
    path: Path = MANIFEST_PATH,
    *,
    splits: tuple[str, ...] | None = ("validation", "test"),
    brief_variant: str = REWRITTEN,
) -> list[PackageManifestEntry]:
    """Load the v4 manifest, optionally swapping in the un-rewritten brief.

    The raw variant is a *different instrument on the same cases*: same as-of time,
    same roster, same truth, different words. Swapping it in here — rather than at
    build time — is what keeps the two comparable.
    """
    if brief_variant not in BRIEF_VARIANTS:
        raise ValueError(f"unknown brief variant: {brief_variant}")
    entries: list[PackageManifestEntry] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = PackageManifestEntry.model_validate_json(line)
            if splits is not None and entry.split not in splits:
                continue
            if brief_variant == RAW:
                entry = entry.model_copy(
                    update={"query_text": entry.brief_raw, "brief_variant": RAW}
                )
            entries.append(entry)
    return entries


def load_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read the Stage 0 exports the package manifest is built from."""
    parquet = DATA_DIR / "parquet"
    missing = [
        name
        for name in ("tickets.parquet", "people.parquet", "sprints.parquet",
                     "sprint_membership.parquet")
        if not (parquet / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"missing Stage 0 exports: {', '.join(missing)} — run `make stage0` first"
        )
    return (
        pd.read_parquet(parquet / "tickets.parquet"),
        pd.read_parquet(parquet / "people.parquet"),
        pd.read_parquet(parquet / "sprints.parquet"),
        pd.read_parquet(parquet / "sprint_membership.parquet"),
    )


def load_rewrites(path: Path | None = None) -> dict[str, dict[str, object]]:
    """Read the rewrite checkpoint; a later record for a package supersedes."""
    path = rewrites_path() if path is None else path
    records: dict[str, dict[str, object]] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            records[str(record["package_key"])] = record
    return records


def rewrites_path() -> Path:
    return DATA_DIR / "eval" / str(settings["eval.v4.rewrites_path"])


def build_packages(rewrites: Mapping[str, Mapping[str, object]] | None = None) -> list[
    PackageManifestEntry
]:
    """Build and persist the v4 manifest from the Stage 0 exports. Offline."""
    tickets, people, sprints, membership = load_sources()
    entries = build_package_manifest(
        tickets,
        sprints,
        membership,
        people,
        cutoff=settings["dataset.holdout_cutoff"],
        min_resolved_tickets=int(settings["dataset.min_tickets_per_person"]),
        min_brief_issues=int(settings["eval.v4.min_brief_issues"]),
        max_brief_issues=int(settings["eval.v4.max_brief_issues"]),
        max_brief_chars=int(settings["eval.v4.max_brief_chars"]),
        min_brief_chars=int(settings["eval.v4.min_brief_chars"]),
        min_rewritten_chars=int(settings["eval.v4.min_rewritten_chars"]),
        n_packages=int(settings["eval.v4.n_packages"]),
        seed=int(settings["eval.v4.seed"]),
        validation_fraction=float(settings["eval.v4.validation_fraction"]),
        rewrites=load_rewrites() if rewrites is None else rewrites,
        min_profile_bucket_tickets=int(settings["bucketing.min_tickets_per_bucket"]),
        max_profile_bucket_tickets=int(settings["bucketing.max_tickets_per_bucket"]),
    )
    write_package_manifest(entries)
    return entries


# ---------- reporting ----------

def manifest_summary(entries: Sequence[PackageManifestEntry]) -> dict[str, object]:
    """Reconcile the manifest: what was selected, what was excluded, and why."""
    selected = [entry for entry in entries if entry.split != "excluded"]
    exclusions: dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry.exclusion_reason:
            exclusions[entry.exclusion_reason] += 1
    splits: dict[str, int] = defaultdict(int)
    projects: dict[str, int] = defaultdict(int)
    truth_sizes: dict[int, int] = defaultdict(int)
    for entry in selected:
        splits[entry.split] += 1
        projects[entry.project_key] += 1
        truth_sizes[len(entry.truth_person_ids)] += 1
    return {
        "manifest": str(MANIFEST_PATH),
        "version": PACKAGE_MANIFEST_VERSION,
        "candidates": len(entries),
        "selected": len(selected),
        "splits": dict(sorted(splits.items())),
        "projects": dict(sorted(projects.items())),
        "excluded": len(entries) - len(selected),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "truth_set_sizes": dict(sorted(truth_sizes.items())),
        "truth_people_total": sum(len(entry.truth_person_ids) for entry in selected),
        "truth_people_dropped_ineligible": sum(
            entry.truth_dropped_ineligible for entry in selected
        ),
        "brief_issue_counts": {
            "min": min((entry.brief_issue_count for entry in selected), default=0),
            "median": sorted(entry.brief_issue_count for entry in selected)[
                len(selected) // 2
            ] if selected else 0,
            "max": max((entry.brief_issue_count for entry in selected), default=0),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the benchmark v4 manifest")
    parser.add_argument(
        "--summary-only", action="store_true",
        help="reconcile the manifest already on disk without rebuilding it",
    )
    args = parser.parse_args(argv)
    entries = (
        load_package_manifest(splits=None) if args.summary_only else build_packages()
    )
    print(json.dumps(manifest_summary(entries), indent=2))
    if not args.summary_only:
        print(f"\nwrote {MANIFEST_PATH} and {BRIEFS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
