"""Near-miss quality study: when the top pick is not truth, is it a plausible substitute?

    uv run python -m capgraph.eval.nearmiss --structure        # offline, $0
    uv run python -m capgraph.eval.nearmiss --rewrite          # SPENDS: nearmiss_rewrite
    uv run python -m capgraph.eval.nearmiss --run              # SPENDS: nearmiss_val
    uv run python -m capgraph.eval.nearmiss --study            # offline, $0
    uv run python -m capgraph.eval.nearmiss --report           # offline, writes the doc

The report claim this exists to settle is currently an interpretation: that the graph
system's top-1 misses are mostly *plausible substitutes* — people from the same
capability neighbourhood as someone who really did the work — rather than errors. This
module turns it into a measured number, in whichever direction it falls.

**It is a descriptive study, not a hypothesis test.** n(misses) on 28 validation cases is
of order fifteen, cases inside a project are correlated (consecutive sprints share a mean
truth-set Jaccard of 0.34), and the intervals below are bootstrap intervals over that
handful of cases. Nothing here is powered to reject anything.

Three constraints from the 2026-08-16 data loss shape the code
(``docs/incident-2026-08-16-data-loss.md``):

* The frozen v4 manifest is gone, so the manifest *structure* is rebuilt offline — it is
  seed-deterministic from the same Stage 0 parquet — and verified case-for-case against
  the published record (``docs/benchmark-v4-manifest.md`` §6.2) before anything is paid
  for. Drift is an escalation, not a warning.
* The rewrite text cannot be recovered, so the 28 validation briefs are re-generated.
  That makes this manifest a **sibling** of the frozen one and never the frozen one, and
  the file name and the version string both say so.
* Nothing symlinks a checkout's ``data/``. This module addresses the real data directory
  by absolute path (see :func:`data_root`).

Every metric is computed inside this study's own run. No number is carried over from a
lost v4 checkpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..llm import CostControlError, estimate_call_cost_usd
from ..pipeline.stage4_project import decay
from ..query.retrieve import _as_date
from ..settings import DATA_DIR, PROMPTS_DIR, REPO_ROOT, settings
from .costs import spend_by_purpose, spend_by_stage
from .metrics import hit_at_k, mrr, recall_at_k
from .packages import (
    PACKAGE_MANIFEST_VERSION,
    REWRITTEN,
    PackageManifestEntry,
    brief_digest,
    build_package_manifest,
)
from .paired import paired_bootstrap
from .rewrite import (
    prompt_digest,
    prompt_name,
    render_prompt,
    rewrite_model,
    rewrite_one,
)
from .run_eval import config_digest, load_checkpoint, run_config, run_split
from .run_v4 import engine_overrides
from .systems import CAPGRAPH_FULL, GRAPH_SYSTEMS

STUDY = "nearmiss"

# The three pre-specified similarity definitions, in the work order's own lettering.
SPECIALIZATION_JACCARD = "a_specialization_jaccard"
SKILL_JACCARD = "b_top_skill_jaccard"
EMBEDDING_COSINE = "c_contribution_embedding_cosine"
DEFINITIONS = (SPECIALIZATION_JACCARD, SKILL_JACCARD, EMBEDDING_COSINE)
DEFINITION_LABELS = {
    SPECIALIZATION_JACCARD: "(a) Jaccard over specialization sets",
    SKILL_JACCARD: "(b) Jaccard over top-10 recency-weighted skills",
    EMBEDDING_COSINE: "(c) Cosine between mean contribution embeddings",
}

# Stand-in text used only while the *structure* is derived offline. The v4 builder applies
# its rewrite guards before selection, so with no rewrite on file every candidate would be
# excluded as `rewrite_pending` and there would be no split to verify. Handing every
# candidate a placeholder that clears those guards reproduces the eligible set the frozen
# build selected from — 157 candidates, none of which failed a rewrite guard
# (docs/benchmark-v4-manifest.md §6.1) — without a model call. It never reaches a prompt:
# the final manifest carries the paid rewrite on validation rows and empty text elsewhere.
STRUCTURE_PLACEHOLDER = (
    "Structure placeholder. This manifest row exists so the offline package selection "
    "and split assignment can be reproduced without a model call. It is replaced by the "
    "purchased rewrite on validation rows and blanked on every other row before the "
    "manifest is written, and it is never sent to a model or read by a system."
)


class NearMissDriftError(RuntimeError):
    """The rebuilt validation split does not match the published v4 record."""


class NearMissBudgetError(RuntimeError):
    """The study's own ceiling, checked before either paid stage starts."""


# ---------- configuration ----------

def study_setting(key: str, default: object = None) -> object:
    return settings.get(f"eval.{STUDY}.{key}", default)


def manifest_version() -> str:
    return str(study_setting("manifest_version"))


def rewrite_stage() -> str:
    return str(study_setting("rewrite_stage"))


def validation_stage() -> str:
    return str(study_setting("validation_stage"))


def study_stages() -> list[str]:
    return [rewrite_stage(), validation_stage()]


def split_name() -> str:
    return str(study_setting("split"))


def engine_name() -> str:
    return str(study_setting("engine"))


# ---------- where the data is ----------

def data_root() -> Path:
    """The real data directory this study reads and writes, by absolute path.

    A worker session runs in a git worktree whose own ``data/`` is empty, and the
    2026-08-16 incident's standing rule forbids symlinking a checkout's ``data/`` at the
    real one. ``CAPGRAPH_DATA_ROOT`` is the absolute-path alternative that rule leaves
    open; unset, this is the repository's own ``data/`` and nothing changes.
    """
    configured = str(os.environ.get("CAPGRAPH_DATA_ROOT", "")).strip()
    return Path(configured).expanduser().resolve() if configured else DATA_DIR


def bind_data_root() -> Path:
    """Point the two globally-rooted paths this study depends on at :func:`data_root`.

    Both are module constants derived from ``settings.DATA_DIR`` at import time, and
    neither has a parameter a caller can pass instead:

    * ``llm._COST_LOG`` — the spend ledger. It survived the incident and is the study's
      audit record, so this study's calls must land in *it* rather than in a second copy
      that a deleted worktree would take with it.
    * ``evidence.BUCKETS_PATH`` — the Stage 1 corpus the v3 lexical retrieval arm's BM25
      index is built from. ``lexical.default_person_index()`` reads it with no argument.

    Called once at the start of every entry point, and a no-op when the root already is
    the repository's ``data/``.
    """
    root = data_root()
    if root == DATA_DIR:
        return root
    from .. import evidence, llm

    llm._COST_LOG = root / "llm_costs.jsonl"
    evidence.BUCKETS_PATH = root / "buckets" / "buckets.jsonl"
    evidence.CACHE_DIR = root / "eval" / "cache"
    evidence.DOCS_CACHE = evidence.CACHE_DIR / "evidence_person_docs.json"
    evidence.EMBEDDINGS_CACHE = evidence.CACHE_DIR / "evidence_ticket_embeddings.npz"
    return root


def study_dir() -> Path:
    return data_root() / "eval" / str(study_setting("root_subdir"))


def manifest_path() -> Path:
    return study_dir() / str(study_setting("manifest_filename"))


def manifest_meta_path() -> Path:
    return study_dir() / "manifest_meta.json"


def rewrites_path() -> Path:
    return study_dir() / str(study_setting("rewrites_filename"))


def runs_dir() -> Path:
    return (
        data_root() / "eval" / str(study_setting("runs_subdir"))
        / engine_name() / REWRITTEN
    )


def study_json_path() -> Path:
    return study_dir() / "study.json"


def display_path(path: Path) -> str:
    """``data/eval/nearmiss/...`` rather than an absolute path through someone's home.

    The report is a tracked document read by people on other machines, so it quotes the
    repository-relative location whenever the path is under the data root.
    """
    try:
        return str(path.relative_to(data_root().parent))
    except ValueError:
        return str(path)


REPORT_PATH = REPO_ROOT / "docs" / "nearmiss-study.md"


# ---------- stage 1: the manifest structure, offline ----------

def load_sources(root: Path | None = None) -> tuple[pd.DataFrame, ...]:
    """Read the Stage 0 exports, from :func:`data_root` rather than the checkout."""
    parquet = (data_root() if root is None else root) / "parquet"
    names = ("tickets.parquet", "people.parquet", "sprints.parquet",
             "sprint_membership.parquet")
    missing = [name for name in names if not (parquet / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"missing Stage 0 exports in {parquet}: {', '.join(missing)}"
        )
    return tuple(pd.read_parquet(parquet / name) for name in names)


def _build(rewrites: Mapping[str, Mapping[str, object]]) -> list[PackageManifestEntry]:
    """One offline pass of the v4 package builder. No model call, no file written."""
    tickets, people, sprints, membership = load_sources()
    return build_package_manifest(
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
        rewrites=rewrites,
        min_profile_bucket_tickets=int(settings["bucketing.min_tickets_per_bucket"]),
        max_profile_bucket_tickets=int(settings["bucketing.max_tickets_per_bucket"]),
    )


def _placeholder_rewrites(
    entries: Sequence[PackageManifestEntry],
) -> dict[str, dict[str, object]]:
    """A guard-clearing stand-in per candidate, keyed to that candidate's own raw text."""
    return {
        entry.package_key: {
            "brief": STRUCTURE_PLACEHOLDER,
            "input_digest": brief_digest(entry.brief_raw),
            "model": "",
            "prompt_digest": "",
        }
        for entry in entries
    }


def build_structure() -> list[PackageManifestEntry]:
    """Rebuild the sibling manifest: structure for every package, rewrite for none.

    Two offline passes. The first has no rewrites at all and exists only to read each
    candidate's ``brief_raw``; the second hands every candidate a placeholder keyed to
    that text, which is what lets the selection and the splits come out as the frozen
    build's. Any rewrite already purchased is substituted for its placeholder.
    """
    first = _build({})
    rewrites = _placeholder_rewrites(first)
    purchased = load_rewrites()
    rewrites.update({key: dict(record) for key, record in purchased.items()})
    entries = _build(rewrites)

    version = manifest_version()
    for entry in entries:
        entry.manifest_version = version
        record = purchased.get(entry.package_key)
        if record is not None and str(record.get("brief") or "").strip():
            continue
        # No purchased rewrite: carry the structure and the raw brief, and leave the
        # rewritten brief empty. An empty query_text is what makes a run of this row
        # fail at the harness's query-context check instead of quietly happening.
        entry.query_text = ""
        entry.brief_rewritten = ""
        entry.rewrite_model = ""
        entry.rewrite_prompt_digest = ""
        entry.rewrite_input_digest = ""
    return entries


def write_manifest(entries: Sequence[PackageManifestEntry], verification: dict) -> None:
    """Write the sibling manifest and the metadata that labels it one."""
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(entry.model_dump_json() + "\n")

    rewritten = [entry for entry in entries if entry.brief_rewritten]
    manifest_meta_path().write_text(
        json.dumps(
            {
                "manifest_version": manifest_version(),
                "sibling_of": PACKAGE_MANIFEST_VERSION,
                "sibling": True,
                "sibling_reason": (
                    "The frozen v4 manifest and its rewrite checkpoints were lost on "
                    "2026-08-16 (docs/incident-2026-08-16-data-loss.md). The structure "
                    "here is rebuilt deterministically from the same Stage 0 parquet and "
                    "verified against the published record; the brief rewrites are "
                    "re-generated and are therefore not byte-identical to the frozen "
                    "ones. Same cases, same as-of times, same rosters, same truth sets, "
                    "different words in the brief."
                ),
                "seed": int(settings["eval.v4.seed"]),
                "sampling_hash_version": PACKAGE_MANIFEST_VERSION,
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "candidates": len(entries),
                "selected": sum(entry.split != "excluded" for entry in entries),
                "rewrites_purchased": len(rewritten),
                "rewrites_purchased_for_splits": sorted(
                    {entry.split for entry in rewritten}
                ),
                "rewrite_model": rewrite_model(),
                "rewrite_prompt": prompt_name(),
                "rewrite_prompt_digest": prompt_digest(),
                "unrewritten_rows_carry": (
                    "structure, roster, truth and brief_raw only; query_text is empty so "
                    "the harness refuses to run them"
                ),
                "test_split_exposure": (
                    "untouched — no test brief was rewritten, run, scored or read"
                ),
                "validation_split_verification": verification,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def load_manifest(
    *, splits: tuple[str, ...] | None = None
) -> list[PackageManifestEntry]:
    """Read the sibling manifest back, optionally keeping only named splits."""
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"missing sibling manifest {path} — run --structure first")
    entries: list[PackageManifestEntry] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = PackageManifestEntry.model_validate_json(line)
            if splits is None or entry.split in splits:
                entries.append(entry)
    return entries


def cases() -> list[PackageManifestEntry]:
    """The study's split, in manifest order. The only split this module ever loads."""
    return load_manifest(splits=(split_name(),))


# ---------- the verification gate ----------

def verify_validation_split(entries: Sequence[PackageManifestEntry]) -> dict[str, object]:
    """Compare the rebuilt split against the published v4 record, field by field."""
    expected = dict(study_setting("expected_validation") or {})
    selected = [entry for entry in entries if entry.split == split_name()]
    projects: dict[str, int] = {}
    for entry in selected:
        projects[entry.project_key] = projects.get(entry.project_key, 0) + 1
    truth_sizes = [len(entry.truth_person_ids) for entry in selected]
    decimals = int(expected.get("mean_truth_set_decimals", 2))
    observed_mean = (
        round(statistics.fmean(truth_sizes), decimals) if truth_sizes else 0.0
    )
    expected_projects = {str(k): int(v) for k, v in dict(expected["projects"]).items()}
    checks = {
        "cases": (len(selected), int(expected["cases"])),
        "projects": (dict(sorted(projects.items())), dict(sorted(expected_projects.items()))),
        "mean_truth_set_size": (
            observed_mean,
            round(float(expected["mean_truth_set_size"]), decimals),
        ),
    }
    mismatches = sorted(name for name, (got, want) in checks.items() if got != want)
    return {
        "record": "docs/benchmark-v4-manifest.md §6.2",
        "split": split_name(),
        "observed": {name: got for name, (got, _) in checks.items()},
        "expected": {name: want for name, (_, want) in checks.items()},
        "mismatches": mismatches,
        "matches": not mismatches,
        "truth_set_sizes": dict(
            sorted((size, truth_sizes.count(size)) for size in set(truth_sizes))
        ),
        "as_of_span": [
            min((e.as_of_time for e in selected), default=None),
            max((e.as_of_time for e in selected), default=None),
        ],
    }


def reconcile_structure(entries: Sequence[PackageManifestEntry]) -> dict[str, object]:
    """Compare the whole rebuilt manifest against the published record, not just the slice.

    A drifted sample could still produce a 28-case validation split holding different
    sprints, so the candidate count, every exclusion reason, the survivorship accounting
    and the *test* split's own shape are checked too. Nothing here reads a test brief, a
    test rewrite or a test result — only the structure this build just derived.
    """
    expected = dict(study_setting("expected_structure") or {})
    selected = [entry for entry in entries if entry.split != "excluded"]
    reasons: dict[str, int] = {}
    for entry in entries:
        if entry.exclusion_reason:
            reasons[entry.exclusion_reason] = reasons.get(entry.exclusion_reason, 0) + 1
    test = [entry for entry in selected if entry.split == "test"]
    test_projects: dict[str, int] = {}
    for entry in test:
        test_projects[entry.project_key] = test_projects.get(entry.project_key, 0) + 1
    want_reasons = {str(k): int(v) for k, v in dict(expected["exclusion_reasons"]).items()}
    want_test_projects = {str(k): int(v) for k, v in dict(expected["test_projects"]).items()}
    checks: dict[str, tuple[object, object]] = {
        "candidates": (len(entries), int(expected["candidates"])),
        "selected": (len(selected), int(expected["selected"])),
        "exclusion_reasons": (dict(sorted(reasons.items())), dict(sorted(want_reasons.items()))),
        "truth_people_total": (
            sum(len(entry.truth_person_ids) for entry in selected),
            int(expected["truth_people_total"]),
        ),
        "truth_people_dropped_ineligible": (
            sum(entry.truth_dropped_ineligible for entry in selected),
            int(expected["truth_people_dropped_ineligible"]),
        ),
        "briefs_hitting_a_cap": (
            sum(1 for entry in selected if entry.brief_issues_omitted),
            int(expected["briefs_hitting_a_cap"]),
        ),
        "test_cases": (len(test), int(expected["test_cases"])),
        "test_projects": (
            dict(sorted(test_projects.items())),
            dict(sorted(want_test_projects.items())),
        ),
        "test_mean_truth_set_size": (
            round(statistics.fmean(len(entry.truth_person_ids) for entry in test), 2)
            if test
            else 0.0,
            round(float(expected["test_mean_truth_set_size"]), 2),
        ),
    }
    mismatches = sorted(name for name, (got, want) in checks.items() if got != want)
    return {
        "record": "docs/benchmark-v4-manifest.md §6.1-6.3",
        "observed": {name: got for name, (got, _) in checks.items()},
        "expected": {name: want for name, (_, want) in checks.items()},
        "mismatches": mismatches,
        "matches": not mismatches,
    }


def structure(*, write: bool = True) -> tuple[list[PackageManifestEntry], dict]:
    """Rebuild, verify, and persist the sibling manifest. Offline; raises on drift."""
    bind_data_root()
    entries = build_structure()
    verification = verify_validation_split(entries)
    verification["structure"] = reconcile_structure(entries)
    if not verification["structure"]["matches"]:
        raise NearMissDriftError(
            "the rebuilt manifest structure does not match the published v4 record "
            f"({verification['structure']['record']}): mismatched "
            f"{', '.join(verification['structure']['mismatches'])}; observed "
            f"{verification['structure']['observed']} against expected "
            f"{verification['structure']['expected']} — escalate to the orchestrator "
            "rather than measuring anything on this manifest"
        )
    if not verification["matches"]:
        raise NearMissDriftError(
            "the rebuilt validation split does not match the published v4 record "
            f"({verification['record']}): mismatched {', '.join(verification['mismatches'])}; "
            f"observed {verification['observed']} against expected "
            f"{verification['expected']} — escalate to the orchestrator rather than "
            "measuring anything on this manifest"
        )
    if write:
        write_manifest(entries, verification)
    return entries, verification


# ---------- stage 2: the paid rewrites, validation split only ----------

def load_rewrites() -> dict[str, dict[str, object]]:
    """Read this study's own rewrite checkpoint; a later record for a package wins."""
    path = rewrites_path()
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


def append_rewrite(record: Mapping[str, object]) -> None:
    path = rewrites_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(record), sort_keys=True) + "\n")


def enforce_ceiling(projected_usd: float) -> float:
    """Refuse work that would break the owner's ceiling across both study stages."""
    ceiling = float(study_setting("max_total_cost_usd"))
    spent = sum(cost for _, _, cost in spend_by_stage(study_stages()))
    if spent + projected_usd > ceiling:
        raise NearMissBudgetError(
            f"projected near-miss spend ${spent + projected_usd:.4f} (logged "
            f"${spent:.4f} + projected ${projected_usd:.4f}) exceeds the "
            f"eval.{STUDY}.max_total_cost_usd ceiling of ${ceiling:.2f} — escalate to "
            "the orchestrator before running this"
        )
    return spent


def rewrite_validation(*, dry_run: bool = False, limit: int | None = None) -> dict[str, int]:
    """Rewrite the study split's briefs — and only those. Spends under nearmiss_rewrite.

    The v4 build rewrote all 157 eligible candidates because selection happened after the
    rewrite guards. Here the structure is already known and verified, so exactly the
    cases that will be run are paid for and the reserved test exposure is not touched.
    """
    bind_data_root()
    entries, _ = structure()
    tickets, people, _sprints, _membership = load_sources()
    from ..privacy import LeakageSanitizer, roster_identifiers

    # Same fallback the v1 builder uses: the people roster is the identifier source when
    # there is one, the tickets themselves when there is not.
    sanitizer = LeakageSanitizer(
        roster_identifiers(people if people is not None else tickets)
    )
    done = load_rewrites()
    # The study split, and nothing else. This is the line that keeps the reserved test
    # exposure unpaid-for: v4 rewrote every eligible candidate because selection happened
    # after the rewrite guards, but here the structure is already verified.
    todo = [
        entry
        for entry in entries
        if entry.split == split_name() and entry.package_key not in done
    ][:limit]

    projected = sum(
        estimate_call_cost_usd(
            render_prompt(entry),
            model=rewrite_model(),
            max_tokens=int(settings["eval.v4.rewrite_max_output_tokens"]),
        )
        for entry in todo
    )
    print(
        f"near-miss rewrite: {len(todo)} of {sum(e.split == split_name() for e in entries)} "
        f"{split_name()} package(s) pending, model {rewrite_model()}, stage "
        f"{rewrite_stage()}, prompt '{prompt_name()}' ({prompt_digest()}), projected "
        f"worst case ${projected:.4f}"
    )
    if dry_run or not todo:
        return {"pending": len(todo), "rewritten": 0, "failed": 0}

    enforce_ceiling(projected)
    counts = {"pending": len(todo), "rewritten": 0, "failed": 0}
    with settings.overridden({"eval.v4.rewrite_stage": rewrite_stage()}):
        for index, entry in enumerate(todo, 1):
            try:
                record = rewrite_one(entry, sanitizer)
            except CostControlError as error:
                print(f"\nstopped at {index}/{len(todo)}: {error}", file=sys.stderr)
                break
            except Exception as error:               # a failure is a recorded result
                counts["failed"] += 1
                print(f"  [{index}/{len(todo)}] {entry.package_key} FAILED {error!r}")
                continue
            record["study"] = STUDY
            record["split"] = entry.split
            append_rewrite(record)
            counts["rewritten"] += 1
            print(
                f"  [{index}/{len(todo)}] {entry.package_key} "
                f"{entry.brief_issue_count} issues -> {len(record['brief'])} chars"
            )
    structure()                                       # fold the new rewrites in
    stage, calls, cost = spend_by_stage([rewrite_stage()])[0]
    print(f"\nrewrites: {counts}; stage '{stage}' ledger: {calls} calls, ${cost:.4f}")
    return counts


# ---------- stage 3: the one paid run ----------

def run_case_cost_usd() -> float:
    projection = dict(study_setting("projection") or {})
    return float(projection["intent_call_usd"]) + float(
        projection["roles_per_case"]
    ) * float(projection["rerank_call_usd"])


def study_config() -> dict[str, object]:
    """Everything that could change a number in this run, recorded with the results."""
    prompt = str(settings["llm.rerank_prompt"])
    return {
        **run_config(),
        "manifest_version": manifest_version(),
        "sibling_of": PACKAGE_MANIFEST_VERSION,
        "seed": int(settings["eval.v4.seed"]),
        "stage": validation_stage(),
        "benchmark_version": "v4-sibling-nearmiss",
        "engine_config": engine_name(),
        "brief_variant": REWRITTEN,
        "rerank_prompt": prompt,
        "rerank_prompt_digest": hashlib.sha256(
            (PROMPTS_DIR / f"{prompt}.md").read_bytes()
        ).hexdigest()[:12],
        "rewrite_model": rewrite_model(),
        "rewrite_prompt_digest": prompt_digest(),
    }


def run(*, limit: int | None = None) -> dict[str, int]:
    """Run the full graph system once over the study split. Spends under nearmiss_val."""
    bind_data_root()
    split = split_name()
    selected = cases()
    if not selected:
        raise SystemExit(f"no {split} cases in {manifest_path()}")
    unrewritten = [entry.package_key for entry in selected if not entry.query_text.strip()]
    if unrewritten:
        raise SystemExit(
            f"{len(unrewritten)} {split} case(s) have no purchased rewrite "
            f"({', '.join(unrewritten[:3])}...) — run --rewrite first"
        )

    with settings.overridden(engine_overrides(engine_name())):
        config = study_config()
        target = runs_dir()
        done = load_checkpoint(split, runs_dir=target)
        pending = sum(
            1 for case in selected[:limit] if (CAPGRAPH_FULL, case.issue_id) not in done
        )
        projected = pending * run_case_cost_usd()
        spent = enforce_ceiling(projected)
        print(
            f"near-miss {split} [{engine_name()}/{REWRITTEN}]: stage {config['stage']}, "
            f"digest {config_digest(config)}, {pending} unpaid case(s) at "
            f"${run_case_cost_usd():.4f}/case, projected ${projected:.2f} on top of "
            f"${spent:.4f} logged against a ${float(study_setting('max_total_cost_usd')):.2f} "
            "ceiling"
        )
        return run_split(
            split,
            systems=GRAPH_SYSTEMS,
            limit=limit,
            stage=validation_stage(),
            runs_dir=target,
            config=config,
            cases=selected,
            manifest_version=manifest_version(),
        )


# ---------- person profiles, read-only from the production graph ----------

# Three plain reads rather than one nested aggregation. Collecting the embeddings in the
# same statement as the terms would make Cypher group rows on a list value, which is both
# slower and a subtlety this study has no reason to depend on. All three are read-only.
SPECIALIZATIONS_QUERY = """
UNWIND $person_ids AS pid
MATCH (p:Person {id: pid})-[:HAS_SPECIALIZATION]->(s:Specialization)
RETURN p.id AS person_id, collect(DISTINCT s.name) AS names
"""

SKILLS_QUERY = """
UNWIND $person_ids AS pid
MATCH (p:Person {id: pid})-[h:HAS_SKILL]->(s:Skill)
RETURN p.id AS person_id, s.name AS name, h.evidence_count AS evidence_count,
       h.last_used AS last_used
ORDER BY person_id ASC, name ASC
"""

# Embeddings are the bulky half (384 floats per contribution), so they are streamed per
# contribution and averaged in Python rather than collected into one row per person.
EMBEDDINGS_QUERY = """
UNWIND $person_ids AS pid
MATCH (p:Person {id: pid})-[:MADE]->(c:Contribution)
WHERE c.embedding IS NOT NULL
RETURN p.id AS person_id, c.id AS contribution_id, c.embedding AS embedding
ORDER BY person_id ASC, contribution_id ASC
"""


@dataclass(frozen=True)
class Profile:
    """One person's capability profile, as the graph holds it. Read once, never written."""

    person_id: str
    specializations: frozenset[str]
    skills: tuple[tuple[str, int, date], ...]     # (name, evidence_count, last_used)
    mean_embedding: np.ndarray | None

    def top_skills(self, as_of: date, *, k: int, half_life_days: int) -> frozenset[str]:
        """The ``k`` skills with most recency-weighted evidence at ``as_of``.

        Weight is ``evidence_count * decay(last_used)`` through Stage 4's own decay,
        recomputed at the case's as-of time. The graph's stored ``decay_score`` is frozen
        at the holdout cutoff and is never read here, for the same reason the harness
        never reads it.
        """
        weighted = [
            (name, count * decay(last_used, half_life_days, as_of=as_of))
            for name, count, last_used in self.skills
        ]
        weighted.sort(key=lambda item: (-item[1], item[0]))
        return frozenset(name for name, _ in weighted[:k])


def load_profiles(person_ids: Sequence[str], driver) -> dict[str, Profile]:
    """Fetch capability profiles for a set of people. Three read-only queries, no writes."""
    wanted = sorted({str(person_id) for person_id in person_ids})
    if not wanted:
        return {}
    specializations: dict[str, set[str]] = {person_id: set() for person_id in wanted}
    skills: dict[str, list[tuple[str, int, date]]] = {
        person_id: [] for person_id in wanted
    }
    sums: dict[str, np.ndarray] = {}
    counts: dict[str, int] = dict.fromkeys(wanted, 0)

    with driver.session() as session:
        for record in session.run(SPECIALIZATIONS_QUERY, person_ids=wanted):
            specializations[str(record["person_id"])] = {
                str(name) for name in record["names"] if name
            }
        for record in session.run(SKILLS_QUERY, person_ids=wanted):
            if record["name"] is None or record["last_used"] is None:
                continue
            skills[str(record["person_id"])].append(
                (
                    str(record["name"]),
                    int(record["evidence_count"] or 0),
                    _as_date(record["last_used"]),
                )
            )
        for record in session.run(EMBEDDINGS_QUERY, person_ids=wanted):
            person_id = str(record["person_id"])
            vector = np.asarray(record["embedding"], dtype=np.float64)
            total = sums.get(person_id)
            sums[person_id] = vector if total is None else total + vector
            counts[person_id] += 1

    profiles = {
        person_id: Profile(
            person_id=person_id,
            specializations=frozenset(specializations[person_id]),
            skills=tuple(skills[person_id]),
            mean_embedding=(
                sums[person_id] / counts[person_id] if counts[person_id] else None
            ),
        )
        for person_id in wanted
    }
    empty = sorted(
        person_id
        for person_id, profile in profiles.items()
        if not profile.specializations and not profile.skills
        and profile.mean_embedding is None
    )
    if empty:
        # A roster member with no graph profile at all would silently score 0.0 against
        # everyone, which would look like a finding rather than a missing person.
        raise ValueError(
            f"no graph profile for {len(empty)} person id(s): {empty[:5]} — is the graph "
            "the one this manifest's roster was frozen from?"
        )
    return profiles


# ---------- the three similarity definitions ----------

def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Intersection over union. Two empty sets share nothing measurable, so 0.0."""
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def cosine(left: np.ndarray | None, right: np.ndarray | None) -> float:
    """Cosine between two mean embedding vectors; 0.0 when either has no magnitude."""
    if left is None or right is None:
        return 0.0
    norms = float(np.linalg.norm(left)) * float(np.linalg.norm(right))
    return float(np.dot(left, right) / norms) if norms > 0 else 0.0


def similarities(
    subject: Profile, other: Profile, *, as_of: date, top_k: int, half_life_days: int
) -> dict[str, float]:
    """All three pre-specified similarity numbers between two people."""
    return {
        SPECIALIZATION_JACCARD: jaccard(subject.specializations, other.specializations),
        SKILL_JACCARD: jaccard(
            subject.top_skills(as_of, k=top_k, half_life_days=half_life_days),
            other.top_skills(as_of, k=top_k, half_life_days=half_life_days),
        ),
        EMBEDDING_COSINE: cosine(subject.mean_embedding, other.mean_embedding),
    }


def nearest(
    subject: Profile,
    others: Sequence[Profile],
    *,
    as_of: date,
    top_k: int,
    half_life_days: int,
) -> tuple[dict[str, float], dict[str, str]]:
    """Similarity to the *nearest* of ``others``, maximised per definition.

    Each definition picks its own nearest person: they measure different things, and
    forcing one winner on all three would report a number no definition produced.
    """
    best = {name: 0.0 for name in DEFINITIONS}
    who = {name: "" for name in DEFINITIONS}
    for other in others:
        values = similarities(
            subject, other, as_of=as_of, top_k=top_k, half_life_days=half_life_days
        )
        for name, value in values.items():
            if not who[name] or value > best[name]:
                best[name], who[name] = value, other.person_id
    return best, who


def intra_truth_similarity(
    truth: Sequence[Profile], *, as_of: date, top_k: int, half_life_days: int
) -> dict[str, float]:
    """How alike the people who worked the same package are, per definition.

    For each truth member, their similarity to their own nearest teammate; averaged over
    members. This is the yardstick a reader needs to size the study's numbers — is 0.19 a
    lot? — and it is deliberately independent of what the system answered, which the
    obvious alternative ("the top-1 person's nearest truth member other than themselves")
    is not: on a miss the top-1 person is outside the truth set, so excluding them removes
    nobody and the number collapses back onto the study's own metric.

    Undefined, and returned as NaN, for a single-person truth set: one person has no
    teammate to be alike.
    """
    if len(truth) < 2:
        return dict.fromkeys(DEFINITIONS, float("nan"))
    collected: dict[str, list[float]] = {name: [] for name in DEFINITIONS}
    for member in truth:
        others = [person for person in truth if person.person_id != member.person_id]
        best, _ = nearest(
            member, others, as_of=as_of, top_k=top_k, half_life_days=half_life_days
        )
        for name, value in best.items():
            collected[name].append(value)
    return {name: statistics.fmean(values) for name, values in collected.items()}


def control_seed_for(package_key: str, base_seed: int) -> int:
    """A per-case seed that depends only on the case and the configured base seed."""
    digest = hashlib.sha256(f"{STUDY}\0{base_seed}\0{package_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def control_median(
    roster: Sequence[str],
    truth: Sequence[Profile],
    profiles: Mapping[str, Profile],
    *,
    package_key: str,
    as_of: date,
    top_k: int,
    half_life_days: int,
    draws: int,
    base_seed: int,
) -> dict[str, float]:
    """Median of ``draws`` seeded draws of one random roster member's nearest-truth score.

    The control answers "what would this number be for *anybody* on this roster?", so it
    is the same measurement with a different subject, and it is drawn from the whole
    frozen roster with replacement.
    """
    rng = random.Random(control_seed_for(package_key, base_seed))
    drawn = [rng.choice(list(roster)) for _ in range(draws)]
    values: dict[str, list[float]] = {name: [] for name in DEFINITIONS}
    for person_id in drawn:
        best, _ = nearest(
            profiles[person_id], truth, as_of=as_of, top_k=top_k,
            half_life_days=half_life_days,
        )
        for name, value in best.items():
            values[name].append(value)
    return {name: statistics.median(series) for name, series in values.items()}


# ---------- adjacent-sprint truth membership ----------

def adjacent_truth(
    entries: Sequence[PackageManifestEntry],
) -> dict[str, dict[str, object]]:
    """Per package, the truth sets of the immediately previous and next sprint.

    Adjacency is over the *whole* rebuilt structure — every candidate sprint with a
    recorded start, selected or not — so "the next sprint" means the project's next
    sprint and not the next sampled case. Both neighbours are post-as-of information for
    the next one and are diagnostics only; nothing here tunes anything.
    """
    by_project: dict[str, list[PackageManifestEntry]] = {}
    for entry in entries:
        if entry.as_of_time is not None:
            by_project.setdefault(entry.project_key, []).append(entry)
    out: dict[str, dict[str, object]] = {}
    for project_key, group in by_project.items():
        group.sort(key=lambda entry: (entry.as_of_time, entry.package_key))
        for index, entry in enumerate(group):
            previous = group[index - 1] if index > 0 else None
            following = group[index + 1] if index + 1 < len(group) else None
            out[entry.package_key] = {
                "project_key": project_key,
                "previous_package": previous.package_key if previous else None,
                "previous_truth": list(previous.truth_person_ids) if previous else [],
                "next_package": following.package_key if following else None,
                "next_truth": list(following.truth_person_ids) if following else [],
            }
    return out


def concurrent_starts(
    entries: Sequence[PackageManifestEntry], *, project_key: str, window_days: int
) -> dict[str, object]:
    """How many other sprints in the project start around the same time as a given one.

    This is the evidence that a project runs several sprint boards in parallel, and it is
    what makes the adjacent-sprint diagnostic readable: with one board, "the project's
    next sprint by start date" is the same team's next sprint; with several live at once,
    it usually is not.

    Measured from recorded start dates only. An earlier version of this inferred boards
    from sprint names, which broke on the project whose names begin with a year
    ("2019 Sprint 4") and reported every sprint as its own board. Dates need no such
    guess.
    """
    starts = sorted(
        entry.as_of_time
        for entry in entries
        if entry.project_key == project_key
        and entry.as_of_time is not None
        and entry.exclusion_reason != "sprint_start_not_post_cutoff"
    )
    window = pd.Timedelta(days=window_days)
    counts = sorted(
        sum(1 for other in starts if other != start and abs(other - start) <= window)
        for start in starts
    )
    return {
        "project_key": project_key,
        "post_cutoff_sprints": len(starts),
        "window_days": window_days,
        "median_concurrent_starts": counts[len(counts) // 2] if counts else 0,
        "max_concurrent_starts": counts[-1] if counts else 0,
    }


# ---------- the study ----------

@dataclass
class CaseResult:
    """One scored case: what the system answered, and how close that answer was."""

    package_key: str
    project_key: str
    as_of: datetime
    roster_size: int
    truth_size: int
    top1: str
    is_hit: bool
    first_truth_rank: int | None
    hit_at_1: float
    recall_at_10: float
    reciprocal_rank: float
    similarity: dict[str, float] = field(default_factory=dict)
    nearest_truth: dict[str, str] = field(default_factory=dict)
    control: dict[str, float] = field(default_factory=dict)
    intra_truth: dict[str, float] = field(default_factory=dict)
    adjacent_previous: bool = False
    adjacent_next: bool = False
    # Diagnostics *about* the adjacency diagnostic: how many people the neighbouring
    # sprints contribute, and how much they overlap this case's own truth set. A project
    # running several boards in parallel makes the date-adjacent sprint another team's,
    # which is the difference between "not on this team" and "not in the next sprint".
    neighbour_truth_size: int = 0
    neighbour_truth_jaccard: float = 0.0

    @property
    def adjacent_either(self) -> bool:
        return self.adjacent_previous or self.adjacent_next


def compute_study(driver=None) -> dict[str, object]:
    """Compute every pre-specified metric from this study's own run checkpoint."""
    bind_data_root()
    all_entries = load_manifest()
    selected = [entry for entry in all_entries if entry.split == split_name()]
    records = load_checkpoint(split_name(), runs_dir=runs_dir())
    ranked_by_case = {
        issue_id: record
        for (system, issue_id), record in records.items()
        if system == CAPGRAPH_FULL and "error" not in record
    }
    failures = sorted(
        issue_id
        for (system, issue_id), record in records.items()
        if system == CAPGRAPH_FULL and "error" in record
    )
    scored = [entry for entry in selected if entry.package_key in ranked_by_case]
    if not scored:
        raise SystemExit(f"no scored {CAPGRAPH_FULL} records in {runs_dir()} — run --run")

    metrics = dict(study_setting("metrics") or {})
    top_k = int(metrics["top_skills"])
    draws = int(metrics["control_draws"])
    base_seed = int(metrics["control_seed"])
    half_life = int(settings["projections.recency_half_life_days"])

    owned_driver = driver is None
    if owned_driver:
        from ..query.engine import connected_driver

        driver = connected_driver()
    try:
        needed = {person_id for entry in scored for person_id in entry.eligible_roster}
        needed.update(
            person_id for entry in scored for person_id in entry.truth_person_ids
        )
        needed.update(
            str(ranked_by_case[entry.package_key]["ranked_ids"][0]) for entry in scored
        )
        profiles = load_profiles(sorted(needed), driver)
    finally:
        if owned_driver:
            driver.close()

    adjacency = adjacent_truth(all_entries)
    results: list[CaseResult] = []
    for entry in scored:
        record = ranked_by_case[entry.package_key]
        ranked = [str(person_id) for person_id in record["ranked_ids"]]
        truth_ids = list(entry.truth_person_ids)
        truth = [profiles[person_id] for person_id in truth_ids]
        top1 = ranked[0]
        as_of = entry.as_of_time.date()
        best, who = nearest(
            profiles[top1], truth, as_of=as_of, top_k=top_k, half_life_days=half_life
        )
        intra = intra_truth_similarity(
            truth, as_of=as_of, top_k=top_k, half_life_days=half_life
        )
        neighbours = adjacency.get(entry.package_key, {})
        neighbour_truth = set(neighbours.get("previous_truth", [])) | set(
            neighbours.get("next_truth", [])
        )
        first_truth = next(
            (index for index, pid in enumerate(ranked, 1) if pid in set(truth_ids)), None
        )
        results.append(
            CaseResult(
                package_key=entry.package_key,
                project_key=entry.project_key,
                as_of=entry.as_of_time,
                roster_size=len(entry.eligible_roster),
                truth_size=len(truth_ids),
                top1=top1,
                is_hit=top1 in set(truth_ids),
                first_truth_rank=first_truth,
                hit_at_1=hit_at_k(ranked, set(truth_ids), 1),
                recall_at_10=recall_at_k(ranked, set(truth_ids), 10),
                reciprocal_rank=mrr(ranked, set(truth_ids)),
                similarity=best,
                nearest_truth=who,
                control=control_median(
                    entry.eligible_roster, truth, profiles,
                    package_key=entry.package_key, as_of=as_of, top_k=top_k,
                    half_life_days=half_life, draws=draws, base_seed=base_seed,
                ),
                intra_truth=intra,
                adjacent_previous=top1 in set(neighbours.get("previous_truth", [])),
                adjacent_next=top1 in set(neighbours.get("next_truth", [])),
                neighbour_truth_size=len(neighbour_truth),
                neighbour_truth_jaccard=jaccard(
                    frozenset(truth_ids), frozenset(neighbour_truth)
                ),
            )
        )

    results.sort(key=lambda case: (case.project_key, case.as_of, case.package_key))
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "manifest": display_path(manifest_path()),
        "manifest_version": manifest_version(),
        "runs_dir": display_path(runs_dir()),
        "config_digest": config_digest(study_config()),
        "split": split_name(),
        "engine": engine_name(),
        "brief_variant": REWRITTEN,
        "definitions": {name: DEFINITION_LABELS[name] for name in DEFINITIONS},
        "settings": {
            "top_skills": top_k,
            "control_draws": draws,
            "control_seed": base_seed,
            "recency_half_life_days": half_life,
            "bootstrap_resamples": int(metrics["bootstrap_resamples"]),
            "bootstrap_seed": int(metrics["bootstrap_seed"]),
        },
        "verification": {
            **verify_validation_split(all_entries),
            "structure": reconcile_structure(all_entries),
        },
        "sprint_calendar": [
            concurrent_starts(
                all_entries,
                project_key=project_key,
                window_days=int(metrics["concurrent_window_days"]),
            )
            for project_key in sorted({entry.project_key for entry in scored})
        ],
        "cases_in_split": len(selected),
        "cases_scored": len(scored),
        "run_failures": failures,
        "cases": [_case_payload(case) for case in results],
        "distributions": distributions(results),
        "spend": {
            "stages": [
                {"stage": stage, "calls": calls, "cost_usd": cost}
                for stage, calls, cost in spend_by_stage(study_stages())
            ],
            "by_purpose": {
                name: {"calls": calls, "cost_usd": cost}
                for name, (calls, cost) in spend_by_purpose(study_stages()).items()
            },
            "ceiling_usd": float(study_setting("max_total_cost_usd")),
        },
    }
    path = study_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")
    return payload


def _case_payload(case: CaseResult) -> dict[str, object]:
    return {
        "package_key": case.package_key,
        "project_key": case.project_key,
        "as_of": case.as_of.isoformat(),
        "roster_size": case.roster_size,
        "truth_size": case.truth_size,
        "top1": case.top1,
        "outcome": "hit" if case.is_hit else "miss",
        "first_truth_rank": case.first_truth_rank,
        "hit_at_1": case.hit_at_1,
        "recall_at_10": case.recall_at_10,
        "reciprocal_rank": case.reciprocal_rank,
        "similarity_to_nearest_truth": case.similarity,
        "nearest_truth_person": case.nearest_truth,
        "control_median": case.control,
        "intra_truth_set_supplementary": {
            name: (None if value != value else value)
            for name, value in case.intra_truth.items()
        },
        "adjacent_sprint_truth": {
            "previous": case.adjacent_previous,
            "next": case.adjacent_next,
            "either": case.adjacent_either,
            "neighbour_truth_size": case.neighbour_truth_size,
            "neighbour_truth_jaccard_with_own": case.neighbour_truth_jaccard,
        },
    }


def bootstrap(values: Mapping[str, float]) -> dict[str, float]:
    """Mean of a per-case series with a seeded bootstrap interval over cases."""
    metrics = dict(study_setting("metrics") or {})
    row = paired_bootstrap(
        "mean",
        {case: 0.0 for case in values},
        dict(values),
        resamples=int(metrics["bootstrap_resamples"]),
        seed=int(metrics["bootstrap_seed"]),
    )
    return {"n": row.n, "mean": row.variant_mean, "ci_low": row.ci_low,
            "ci_high": row.ci_high}


def paired_delta(
    control: Mapping[str, float], actual: Mapping[str, float]
) -> dict[str, float]:
    """The per-case difference from the control, with its own bootstrap interval."""
    metrics = dict(study_setting("metrics") or {})
    row = paired_bootstrap(
        "delta",
        dict(control),
        dict(actual),
        resamples=int(metrics["bootstrap_resamples"]),
        seed=int(metrics["bootstrap_seed"]),
    )
    # Named so a reader of study.json cannot mistake the similarity mean for the delta:
    # ci_low/ci_high are the interval on `mean_delta`, not on `similarity_mean`.
    return {
        "n": row.n, "control_mean": row.baseline_mean,
        "similarity_mean": row.variant_mean, "mean_delta": row.mean_delta,
        "ci_low": row.ci_low, "ci_high": row.ci_high,
        "above_control": row.better, "below_control": row.worse, "ties": row.ties,
    }


def distributions(results: Sequence[CaseResult]) -> dict[str, object]:
    """The summaries the work order pre-specified, for misses and for hits alike."""
    out: dict[str, object] = {}
    for group, subset in (
        ("misses", [case for case in results if not case.is_hit]),
        ("hits", [case for case in results if case.is_hit]),
    ):
        entry: dict[str, object] = {
            "n": len(subset),
            "packages": [case.package_key for case in subset],
            "similarity": {},
            "control": {},
            "delta_vs_control": {},
            "intra_truth_set_supplementary": {},
        }
        for name in DEFINITIONS:
            actual = {case.package_key: case.similarity[name] for case in subset}
            control = {case.package_key: case.control[name] for case in subset}
            # NaN for a single-person truth set: dropped rather than counted as zero.
            intra = {
                case.package_key: case.intra_truth[name]
                for case in subset
                if case.intra_truth[name] == case.intra_truth[name]
            }
            entry["similarity"][name] = bootstrap(actual)
            entry["control"][name] = bootstrap(control)
            entry["delta_vs_control"][name] = paired_delta(control, actual)
            entry["intra_truth_set_supplementary"][name] = bootstrap(intra)
        # How many *different* people the system named first. If one profile takes the
        # top slot in most of the misses, then a similarity measured on those misses is
        # substantially a fact about that one person, not about the system's
        # discrimination — and a reader cannot see that from a mean.
        picks: dict[str, int] = {}
        for case in subset:
            picks[case.top1] = picks.get(case.top1, 0) + 1
        ranked_picks = sorted(picks.items(), key=lambda item: (-item[1], item[0]))
        entry["top1_concentration"] = {
            "n": len(subset),
            "distinct_people": len(picks),
            "most_frequent": ranked_picks[0][0] if ranked_picks else None,
            "most_frequent_count": ranked_picks[0][1] if ranked_picks else 0,
            "counts": dict(ranked_picks),
        }
        adjacent = [case for case in subset if case.adjacent_either]
        neighbour_sizes = sorted(case.neighbour_truth_size for case in subset)
        entry["adjacent_sprint"] = {
            "n": len(subset),
            "in_previous_or_next": len(adjacent),
            "in_previous": sum(case.adjacent_previous for case in subset),
            "in_next": sum(case.adjacent_next for case in subset),
            "share": len(adjacent) / len(subset) if subset else 0.0,
            "packages": [case.package_key for case in adjacent],
            # How much of a chance the diagnostic had: the neighbours' truth sets are the
            # people it can find, and their overlap with the case's own truth set says
            # whether date-adjacency tracks team continuity at all on this project.
            "neighbour_truth_size_median": (
                neighbour_sizes[len(neighbour_sizes) // 2] if neighbour_sizes else 0
            ),
            "cases_with_no_neighbour_truth": sum(
                1 for size in neighbour_sizes if size == 0
            ),
            "own_truth_jaccard_with_neighbours": bootstrap(
                {case.package_key: case.neighbour_truth_jaccard for case in subset}
            ),
        }
        out[group] = entry
    return out


# ---------- the report ----------

def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None or value != value:
        return "n/a"
    return f"{value:.{digits}f}"


def _interval(row: Mapping[str, float], key: str = "mean") -> str:
    return (
        f"{_fmt(row[key])} [{_fmt(row['ci_low'])}, {_fmt(row['ci_high'])}]"
        if row.get("n")
        else "n/a"
    )


def render_report(payload: Mapping[str, object]) -> str:
    """The deliverable: method, verification, every case, the distributions, the claim."""
    from .report_nearmiss import render

    return render(payload)


def report() -> str:
    payload = compute_study()
    markdown = render_report(payload)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    print(f"wrote {REPORT_PATH} and {study_json_path()}")
    return markdown


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Near-miss quality study")
    parser.add_argument("--structure", action="store_true",
                        help="offline: rebuild and verify the sibling manifest")
    parser.add_argument("--rewrite", action="store_true",
                        help=f"SPENDS: rewrite the {split_name()} briefs only")
    parser.add_argument("--run", action="store_true",
                        help=f"SPENDS: one {split_name()} run of the full graph system")
    parser.add_argument("--study", action="store_true",
                        help="offline: compute the pre-specified metrics")
    parser.add_argument("--report", action="store_true",
                        help="offline: compute and write docs/nearmiss-study.md")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --rewrite: price the batch without calling")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    if args.structure:
        entries, verification = structure()
        print(json.dumps(verification, indent=2, default=str))
        print(f"\nwrote {manifest_path()} ({len(entries)} candidate rows) and "
              f"{manifest_meta_path()}")
    if args.rewrite:
        rewrite_validation(dry_run=args.dry_run, limit=args.limit)
    if args.run:
        print(json.dumps(dict(sorted(run(limit=args.limit).items())), indent=2))
    if args.study:
        print(json.dumps(compute_study()["distributions"], indent=2, default=str))
    if args.report:
        report()
    if not any((args.structure, args.rewrite, args.run, args.study, args.report)):
        parser.error("nothing to do: pass --structure, --rewrite, --run, --study or --report")
    return 0


__all__ = [
    "CaseResult",
    "DEFINITIONS",
    "NearMissBudgetError",
    "NearMissDriftError",
    "Profile",
    "adjacent_truth",
    "bind_data_root",
    "bootstrap",
    "build_structure",
    "compute_study",
    "concurrent_starts",
    "control_median",
    "cosine",
    "data_root",
    "display_path",
    "distributions",
    "intra_truth_similarity",
    "jaccard",
    "load_profiles",
    "manifest_path",
    "nearest",
    "paired_delta",
    "reconcile_structure",
    "report",
    "run",
    "rewrite_validation",
    "similarities",
    "structure",
    "verify_validation_split",
]


if __name__ == "__main__":
    raise SystemExit(main())
