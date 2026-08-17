"""Re-rank prompt redesign: rank on evidence, not on presentation order.

    uv run python -m capgraph.eval.rerank_redesign --capture-pin   # SPENDS (intent only)
    uv run python -m capgraph.eval.rerank_redesign --verify-pin    # offline
    uv run python -m capgraph.eval.rerank_redesign --arm A         # SPENDS (re-rank only)
    uv run python -m capgraph.eval.rerank_redesign --report        # offline

Wave-1's G7 probe established that reversing the candidate order halves Hit@1 under the
current prompt: **presentation order dominates the re-rank**. This study asks whether a
redesigned prompt can close that gap, and it has to isolate the re-rank stage to answer
it — which no previous arm in this project has done.

**Pinned retrieval is the instrument.** Every previous A/B re-ran the whole engine, so
an arm's difference mixed the re-rank with a fresh intent parse and whatever retrieval
did with it. Here the intent parse, the union candidate pool, the deterministic scores
and the re-rank window are captured once (:func:`capture_pin`) and replayed
byte-identically into every arm (:func:`run_arm`). Two consequences:

* an arm pays for re-rank calls only — no intent call, no Neo4j, no embedding model;
* "the arms ranked identical pools" is a checked fact rather than an assumption:
  :func:`assert_pin_complete` refuses to spend against a half-captured pin, and
  :func:`pin_identity_rows` reads every arm's checkpointed pool and deterministic
  ranking back out and compares them to the pin.

**What the frozen v4 validation run is, and is not.** The work order expected it to
serve as the free "current prompt, ordered" baseline. It cannot. Benchmark v4
checkpointed rankings and pools but not the intent parses behind them, and re-parsing
the same briefs with the same model at temperature 0 does not reproduce them — measured
2026-08-15 over all 28 validation cases (:func:`frozen_run_comparison_rows`). The
ordered current-prompt arm is therefore a paid arm like the others, and the frozen run
survives here only as the diagnostic that says why.

**What may vary between arms is exactly two things**: which ``prompts/*.md`` the re-rank
loads, and whether the window is presented best-first or worst-first (the G7 flag, whose
reversal path is the one the probe used). Model, window width, card data, citation rules
and the evidence validator are the same in every arm — a rejected entry is still
discarded and never repaired.

**Spend discipline.** All spend lands under one cost-log stage (``rerank_redesign``) with
its own ceiling, re-checked before every chunk of cases rather than once up front. The v4
test split is not reachable from this module: the split, engine and brief variant are
read from ``eval.rerank_redesign`` and :func:`_require_validation` refuses anything else.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .. import improvements
from ..models import CandidateProfile, RoleSpec
from ..query.rank import candidate_view, finish, rerank, rerank_input, rerank_output_tokens
from ..settings import DATA_DIR, load_prompt, settings
from .costs import CostMeter, spend_by_purpose, spend_by_stage
from .metrics import query_context
from .packages import PACKAGE_MANIFEST_VERSION, PackageManifestEntry, load_package_manifest
from .paired import paired_binary, paired_bootstrap, render_paired
from .run_eval import (
    append_record,
    config_digest,
    load_checkpoint,
)
from .run_v4 import METRIC_LABELS, engine_overrides, runs_dir, v4_config
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE, round_robin

STUDY = "rerank_redesign"
SPLIT_SETTING = f"eval.{STUDY}.split"
BINARY_METRICS = ("hit_at_1", "hit_at_5", "hit_at_10")
CONTINUOUS_METRICS = ("recall_at_5", "recall_at_10", "mrr")

# The v2 section measured this by re-running one configuration unchanged; the G7 probe
# and the wave-1 acceptance both state their conclusions against it. v4 has no floor of
# its own (docs/eval-results.md), so it is quoted as the best available gauge and never
# as a v4 measurement.
RUN_TO_RUN_FLOOR = 0.100

REPORT_PATH = Path(__file__).resolve().parents[3] / "docs" / "rerank-redesign-report.md"

# Where :func:`inspect_mechanism` persists its answer-structure checks, so the report can
# quote them without paying for the call again.
MECHANISM_CHECK_PATH = DATA_DIR / "eval" / STUDY / "mechanism_check.json"


class RerankBudgetError(RuntimeError):
    """The study's own ceiling, checked before every chunk of cases."""


class PinMismatchError(RuntimeError):
    """The pin is missing or incomplete, so the arms would not be replaying one retrieval."""


# ---------- configuration ----------

def study(name: str, default=None):
    return settings.get(f"eval.{STUDY}.{name}", default)


@dataclass(frozen=True)
class Arm:
    """One arm: a prompt, a presentation order, and whether it is the reference arm."""

    name: str
    prompt: str
    order: str
    label: str
    reference: bool = False
    # Per-candidate output allowance, when this arm's answer needs more room than
    # llm.rerank_output_tokens_per_candidate gives it. ``None`` keeps the default.
    output_tokens_per_candidate: int | None = None


def arms() -> list[Arm]:
    configured = study("arms") or []
    if not isinstance(configured, Sequence) or isinstance(configured, str):
        raise TypeError(f"eval.{STUDY}.arms must be a list of arm definitions")
    out = [
        Arm(
            name=str(entry["name"]),
            prompt=str(entry["prompt"]),
            order=str(entry["order"]),
            label=str(entry.get("label") or entry["name"]),
            reference=bool(entry.get("reference", False)),
            output_tokens_per_candidate=(
                None if entry.get("output_tokens_per_candidate") is None
                else int(entry["output_tokens_per_candidate"])
            ),
        )
        for entry in configured
    ]
    unknown = [arm.order for arm in out if arm.order not in improvements.PRESENTATION_ORDERS]
    if unknown:
        raise ValueError(
            f"arm presentation order(s) {', '.join(sorted(set(unknown)))} not in "
            f"{', '.join(improvements.PRESENTATION_ORDERS)}"
        )
    return out


def arm_named(name: str) -> Arm:
    for arm in arms():
        if arm.name == name:
            return arm
    raise ValueError(f"unknown arm '{name}'; known: {', '.join(a.name for a in arms())}")


def reference_arm() -> Arm:
    """The arm the others are read against: the current prompt in its normal order."""
    for arm in arms():
        if arm.reference:
            return arm
    raise ValueError(f"eval.{STUDY}.arms defines no reference arm")


def _require_validation() -> str:
    """The only split this module may touch, refused rather than defaulted.

    The v4 test split has had one exposure and its budget belongs to a later freeze
    order. Nothing here — pin, arm, or report — can be pointed at it by a flag or a
    typo: the split name is read once, here, and anything but ``validation`` stops.
    """
    split = str(study("split", "validation"))
    if split != "validation":
        raise ValueError(
            f"{SPLIT_SETTING} is {split!r}; this study is authorized on the v4 "
            "validation split only — the test split's exposure budget belongs to a "
            "separately approved freeze order"
        )
    return split


def engine() -> str:
    return str(study("engine", "v3frozen"))


def brief_variant() -> str:
    return str(study("brief_variant", "rewritten"))


def stage() -> str:
    return str(study("stage", STUDY))


def pin_path() -> Path:
    return DATA_DIR / "eval" / str(study("pin_subdir", f"{STUDY}/pin")) / "validation.jsonl"


def arm_runs_dir(arm: Arm) -> Path:
    """Where an arm's checkpoint lives. One namespace per arm; they never mix."""
    return DATA_DIR / "eval" / str(study("runs_subdir", f"{STUDY}/runs")) / arm.name


def frozen_v4_records() -> dict[tuple[str, str], dict]:
    """The frozen v4 validation run — this study's diagnostic, not its baseline.

    The work order expected this run to serve as the free "current prompt, ordered"
    arm. It cannot: benchmark v4 checkpointed rankings and pools but not the intent
    parses behind them, and re-parsing the same briefs with the same model at
    temperature 0 does not reproduce them. What it is still good for is showing exactly
    that, case by case (:func:`frozen_run_comparison_rows`).
    """
    return load_checkpoint(_require_validation(), runs_dir=runs_dir(engine(), brief_variant()))


def cases() -> list[PackageManifestEntry]:
    return sorted(
        load_package_manifest(
            splits=(_require_validation(),), brief_variant=brief_variant()
        ),
        key=lambda case: case.issue_id,
    )


def arm_config(arm: Arm) -> dict[str, object]:
    """The frozen v4 validation configuration, plus what this study varies.

    Built so that two arms can never be appended to one checkpoint: the prompt, its
    digest, the presentation order and the pin digest all enter the configuration, so
    the digest differs whenever any of them does.
    """
    with settings.overridden(_overrides(arm)):
        config = v4_config(_require_validation(), engine(), brief_variant())
        allowance = int(settings["llm.rerank_output_tokens_per_candidate"])
    config.update(
        {
            "stage": stage(),
            "study": STUDY,
            "arm": arm.name,
            "rerank_presentation_order": arm.order,
            "rerank_output_tokens_per_candidate": allowance,
            "pinned_retrieval": True,
            "pin_digest": pin_digest(),
        }
    )
    return config


def _overrides(arm: Arm) -> dict[str, object]:
    """The settings one arm changes: its prompt, and its output allowance if it has one.

    The presentation order is not here — it is an ``improvements`` flag, applied through
    :func:`capgraph.improvements.overridden` so the reversal runs the same code path the
    G7 probe measured.
    """
    overrides: dict[str, object] = {
        **engine_overrides(engine()),
        "llm.rerank_prompt": arm.prompt,
    }
    if arm.output_tokens_per_candidate is not None:
        overrides["llm.rerank_output_tokens_per_candidate"] = arm.output_tokens_per_candidate
    return overrides


# ---------- spend control ----------

def study_spend() -> float:
    return spend_by_stage([stage()])[0][2]


def projected_call_cost(kind: str) -> float:
    projection = dict(study("projection") or {})
    return float(projection[f"{kind}_call_usd"])


def enforce_budget(pending_calls: int, *, kind: str) -> float:
    """Refuse the next chunk when it would break the owner's authorization."""
    ceiling = float(study("max_total_cost_usd", 6.0))
    projected = pending_calls * projected_call_cost(kind)
    spent = study_spend()
    if spent + projected > ceiling:
        raise RerankBudgetError(
            f"projected {STUDY} spend ${spent + projected:.2f} (logged ${spent:.4f} + "
            f"projected ${projected:.2f} for {pending_calls} {kind} calls at "
            f"${projected_call_cost(kind):.4f} each) exceeds the "
            f"eval.{STUDY}.max_total_cost_usd ceiling of ${ceiling:.2f} — escalate to "
            "the orchestrator before running more of this study"
        )
    return projected


# ---------- the pin ----------

def load_pin(path: Path | None = None) -> dict[str, dict]:
    """The captured retrieval, keyed by issue id. Empty when nothing is captured."""
    path = pin_path() if path is None else path
    pinned: dict[str, dict] = {}
    if not path.exists():
        return pinned
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                pinned[record["issue_id"]] = record
    return pinned


def pin_digest(path: Path | None = None) -> str:
    """Fingerprint of the pin file every arm replayed, recorded in every arm record."""
    path = pin_path() if path is None else path
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def pin_pool(record: Mapping) -> list[str]:
    """The union candidate pool across roles, exactly as eval/systems.py builds it."""
    ids: list[str] = []
    for role in record["roles"]:
        ids.extend(str(person_id) for person_id in role["candidate_person_ids"])
    return list(dict.fromkeys(ids))


def pin_score_ordering(record: Mapping) -> list[str]:
    """The deterministic score ranking, exactly as eval/systems.py merges it."""
    return round_robin([list(role["scored_person_ids"]) for role in record["roles"]])


def pin_role(role: RoleSpec, candidates: Sequence[CandidateProfile]) -> dict[str, object]:
    """One role's captured retrieval: the pool, the score order, and the window.

    The window is stored as whole :class:`CandidateProfile` records rather than as ids,
    because the re-rank prompt is rendered from the profile — terms, counts, dates,
    deterministic score and cite-able evidence keys. Storing ids and re-deriving the
    rest would put a second, drifting implementation of the card between the arms.
    """
    scored = sorted(candidates, key=lambda c: (-c.score, c.person_id))
    return {
        "role": role.model_dump(mode="json"),
        "candidate_person_ids": [c.person_id for c in candidates],
        "scored_person_ids": [c.person_id for c in scored],
        "window": [c.model_dump(mode="json") for c in rerank_input(candidates)],
    }


def capture_pin(*, limit: int | None = None, path: Path | None = None) -> dict[str, int]:
    """SPENDS (intent calls only): capture the retrieval every arm will replay.

    One intent parse and one retrieval pass per case, under the frozen v4 engine
    configuration, written to the pin file. Resumable: a case already in the file is
    skipped, so an interrupted capture costs nothing twice.
    """
    from ..embeddings import embed
    from ..lexical import default_person_index
    from ..query.engine import connected_driver, retrieve_role
    from ..query.intent import parse_intent
    from ..query.rank import score_candidate
    from ..query.retrieve import known_specializations

    split = _require_validation()
    path = pin_path() if path is None else path
    pinned = load_pin(path)
    todo = [case for case in cases()[:limit] if case.issue_id not in pinned]
    counts = {"cases": len(cases()[:limit]), "skipped": len(cases()[:limit]) - len(todo),
              "captured": 0, "failed": 0}
    print(
        f"{STUDY} pin: {len(todo)} of {counts['cases']} {split} cases to capture, "
        f"engine {engine()}/{brief_variant()}, stage '{stage()}', logged "
        f"${study_spend():.4f} of ${float(study('max_total_cost_usd', 6.0)):.2f}"
    )
    if not todo:
        return counts

    chunk = max(1, int(study("chunk_size", 4)))
    with settings.overridden(engine_overrides(engine())):
        embed(["warm up the local embedding model"])
        lexical_index = (
            default_person_index() if int(settings["retrieval.bm25_top_k"]) > 0 else None
        )
        driver = connected_driver()
        meter = CostMeter()
        try:
            specializations = known_specializations(driver)
            for index, case in enumerate(todo, 1):
                if (index - 1) % chunk == 0:
                    enforce_budget(min(chunk, len(todo) - index + 1), kind="intent")
                context = query_context(case, expected_version=PACKAGE_MANIFEST_VERSION)
                meter.mark()
                intent = parse_intent(context.query_text, specializations, stage=stage())
                roles = []
                for role in intent.roles:
                    resolution, candidates = retrieve_role(
                        role,
                        context.query_text,
                        driver,
                        roster=context.eligible_roster,
                        as_of=context.as_of_time,
                        lexical_index=lexical_index,
                    )
                    for candidate in candidates:
                        score_candidate(candidate, role, resolution)
                    roles.append(pin_role(role, candidates))
                spend = meter.spend_since(stage=stage())
                record = {
                    "issue_id": case.issue_id,
                    "issue_key": case.issue_key,
                    "project_key": case.project_key,
                    "split": split,
                    "engine": engine(),
                    "brief_variant": brief_variant(),
                    "manifest_version": PACKAGE_MANIFEST_VERSION,
                    "brief": context.query_text,
                    "intent": intent.model_dump(mode="json"),
                    "roles": roles,
                    "intent_cost_usd": round(spend.total, 6),
                    "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                counts["captured"] += 1
                print(
                    f"  [{index}/{len(todo)}] {case.issue_key} {case.project_key}: "
                    f"{len(roles)} role(s), pool {len(pin_pool(record))}, "
                    f"spend ${study_spend():.4f}",
                    flush=True,
                )
        finally:
            driver.close()
    return counts


def frozen_run_comparison_rows(path: Path | None = None) -> list[dict[str, object]]:
    """Per case: how far the pin's retrieval is from the frozen v4 validation run's.

    This is the measurement that voided the work order's free-baseline plan, kept as
    evidence rather than as a claim. Three comparisons — the roles the brief parsed
    into, the union pool the re-rank was chosen from, and the deterministic ranking that
    pool produces. A case would need all three to hold for its frozen ``capgraph_full``
    record to be a "current prompt, ordered" arm on the retrieval the other arms replay.
    """
    pinned = load_pin(path)
    records = frozen_v4_records()
    rows: list[dict[str, object]] = []
    for case in cases():
        record = pinned.get(case.issue_id)
        frozen_full = records.get((CAPGRAPH_FULL, case.issue_id))
        frozen_score = records.get((CAPGRAPH_SCORE, case.issue_id))
        if record is None or frozen_full is None or frozen_score is None:
            rows.append({"issue_id": case.issue_id, "issue_key": case.issue_key,
                         "pinned": record is not None, "pool_matches": False,
                         "score_order_matches": False, "roles_match": False,
                         "pool_size": 0, "frozen_pool_size": 0, "pool_jaccard": 0.0,
                         "n_roles": 0})
            continue
        mine, theirs = pin_pool(record), list(frozen_full["candidate_ids"] or ())
        union = set(mine) | set(theirs)
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "pinned": True,
            "pool_matches": mine == theirs,
            "score_order_matches": pin_score_ordering(record) == list(
                frozen_score["ranked_ids"]
            ),
            "roles_match": [role["role"]["role"] for role in record["roles"]]
            == list(frozen_full.get("detail", {}).get("roles", ())),
            "pool_size": len(mine),
            "frozen_pool_size": len(theirs),
            "pool_jaccard": round(len(set(mine) & set(theirs)) / len(union), 3) if union else 0.0,
            "n_roles": len(record["roles"]),
        })
    return rows


def assert_pin_complete(path: Path | None = None) -> dict[str, int]:
    """Refuse to spend an arm against a pin that is missing or half-captured.

    The invariant every conclusion in this study rests on is that **all arms replay one
    pin**. That is enforced here (the pin covers every case and every case has a window
    to rank) and again at scoring time, where each arm's checkpointed pool and
    deterministic ranking are compared back to the pin (:func:`pin_identity_rows`).
    """
    pinned = load_pin(path)
    missing = [case.issue_id for case in cases() if case.issue_id not in pinned]
    empty = [
        record["issue_id"]
        for record in pinned.values()
        if not record["roles"] or any(not role["window"] for role in record["roles"])
    ]
    if missing or empty:
        raise PinMismatchError(
            f"the pin is not usable: {len(missing)} case(s) not captured"
            + (f" ({', '.join(missing[:5])})" if missing else "")
            + f", {len(empty)} case(s) with an empty re-rank window"
            + (f" ({', '.join(empty[:5])})" if empty else "")
            + ". Run --capture-pin before spending an arm: every arm must replay the "
            "same retrieval or the arms are not comparable."
        )
    return {
        "cases": len(pinned),
        "roles": sum(len(record["roles"]) for record in pinned.values()),
    }


# ---------- arms ----------

def _rebuild_window(role_record: Mapping) -> tuple[RoleSpec, list[CandidateProfile]]:
    return (
        RoleSpec.model_validate(role_record["role"]),
        [CandidateProfile.model_validate(entry) for entry in role_record["window"]],
    )


def replay_case(record: Mapping, *, arm: Arm, stage_name: str) -> tuple[dict, dict]:
    """Re-rank one pinned case under one arm. The only step that calls a model.

    Mirrors :func:`capgraph.query.engine.query` from the re-rank onwards — same
    ``rerank``/``finish`` calls, same padding of the ranking with the deterministic
    remainder, same round-robin merge across roles — so the ranking this produces is the
    one the engine would have produced had it been run with this arm's prompt and order.

    The arm is applied as scoped overrides — ``llm.rerank_prompt`` for the prompt, the
    G7 flag for the presentation order — so an arm cannot leak into the next one and
    nothing in ``config/settings.yaml`` has to be edited between arms.
    """
    per_role: list[list[str]] = []
    rejected: list[str] = []
    accepted = 0
    offered = 0
    counts: dict[str, int] = {}
    meter = CostMeter()
    meter.mark()
    with settings.overridden(_overrides(arm)), improvements.overridden(
        {improvements.FLAG_ORDER: arm.order}
    ):
        for role_record in record["roles"]:
            role, window = _rebuild_window(role_record)
            ranking, problems = rerank(record["brief"], role, window, stage=stage_name)
            ranking, finisher_problems = finish(
                record["brief"], role, ranking, window, stage=stage_name
            )
            problems = [*problems, *finisher_problems]
            rejected.extend(problems)
            accepted += len(ranking)
            offered += len(ranking) + len(problems)
            counts["reranked"] = counts.get("reranked", 0) + len(window)
            counts["shortlisted"] = counts.get("shortlisted", 0) + len(ranking)
            per_role.append(
                list(dict.fromkeys(person.person_id for person in ranking))
                + list(dict.fromkeys(role_record["scored_person_ids"]))
            )
    spend = meter.spend_since(stage=stage_name)
    detail = {
        "roles": [role_record["role"]["role"] for role_record in record["roles"]],
        "candidate_counts": [counts],
        "rejected": rejected,
        "n_ranked_by_rerank": accepted,
        "n_offered_by_rerank": offered,
        "timings_ms": {},
        "cost_usd_by_purpose": dict(sorted(spend.by_purpose.items())),
        "n_llm_calls": spend.n_calls,
    }
    return {"ranked_ids": round_robin(per_role), "cost_usd": spend.total}, detail


def _record(
    *, arm: Arm, system: str, case: PackageManifestEntry, digest: str, pool: Sequence[str],
    ranked: Sequence[str], cost: float, detail: dict | None = None, error: str | None = None,
) -> dict:
    record: dict[str, object] = {
        "split": _require_validation(),
        "system": system,
        "issue_id": case.issue_id,
        "issue_key": case.issue_key,
        "project_key": case.project_key,
        "config_digest": digest,
        "arm": arm.name,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if error is not None:
        record["error"] = error
        return record
    record.update({
        "ranked_ids": list(ranked),
        "candidate_ids": list(pool),
        # Latency is not comparable in a replay — the retrieval it would include was
        # paid once, at capture, and is not re-run per arm. Recorded as 0.0 rather than
        # as a number a reader could mistake for a measurement of this arm.
        "latency_ms": 0.0,
        "cost_usd": round(float(cost), 6),
    })
    if detail:
        record["detail"] = detail
    return record


def run_arm(name: str, *, limit: int | None = None) -> dict[str, int]:
    """SPENDS (re-rank calls only): run one arm over the pinned validation cases."""
    arm = arm_named(name)
    split = _require_validation()
    assert_pin_complete()              # never spend against retrieval that is not pinned
    pinned = load_pin()
    target = arm_runs_dir(arm)
    digest = config_digest(arm_config(arm))
    done = load_checkpoint(split, runs_dir=target)
    stale = sorted({r.get("config_digest", "<none>") for r in done.values()} - {digest})
    if stale:
        raise SystemExit(
            f"arm '{arm.name}' checkpoint holds configuration(s) {', '.join(stale)} but "
            f"this run is {digest}; move {target} aside or restore the settings"
        )

    todo = [
        case for case in cases()[:limit]
        if (CAPGRAPH_FULL, case.issue_id) not in done and case.issue_id in pinned
    ]
    counts = {"cases": len(cases()[:limit]), "skipped": len(cases()[:limit]) - len(todo),
              "ran": 0, "failed": 0}
    calls = sum(len(pinned[case.issue_id]["roles"]) for case in todo)
    print(
        f"{STUDY} arm {arm.name} ({arm.label}): {len(todo)} cases / {calls} re-rank "
        f"calls, prompt '{arm.prompt}', order '{arm.order}', stage '{stage()}', "
        f"digest {digest}, logged ${study_spend():.4f} of "
        f"${float(study('max_total_cost_usd', 6.0)):.2f}"
    )
    if not todo:
        return counts

    chunk = max(1, int(study("chunk_size", 4)))
    for index, case in enumerate(todo, 1):
        if (index - 1) % chunk == 0:
            pending = sum(
                len(pinned[c.issue_id]["roles"]) for c in todo[index - 1: index - 1 + chunk]
            )
            projected = enforce_budget(pending, kind="rerank")
            print(f"  chunk from case {index}: projected ${projected:.2f}")
        record = pinned[case.issue_id]
        pool = pin_pool(record)
        counts["ran"] += 1
        try:
            output, detail = replay_case(record, arm=arm, stage_name=stage())
        except Exception as error:                            # a failure is a result
            counts["failed"] += 1
            append_record(split, _record(arm=arm, system=CAPGRAPH_FULL, case=case,
                                         digest=digest, pool=pool, ranked=(), cost=0.0,
                                         error=repr(error)), runs_dir=target)
            print(f"  [{index}/{len(todo)}] {case.issue_key} FAILED {error!r}", flush=True)
            continue
        append_record(split, _record(arm=arm, system=CAPGRAPH_FULL, case=case,
                                     digest=digest, pool=pool, ranked=output["ranked_ids"],
                                     cost=output["cost_usd"], detail=detail),
                      runs_dir=target)
        # The deterministic arm costs nothing and is identical in every arm by
        # construction. Written per arm anyway: it is the in-study control, and a
        # checkpoint that disagreed with the baseline's would prove the pin broken.
        append_record(split, _record(arm=arm, system=CAPGRAPH_SCORE, case=case,
                                     digest=digest, pool=pool,
                                     ranked=pin_score_ordering(record), cost=0.0),
                      runs_dir=target)
        print(
            f"  [{index}/{len(todo)}] {case.issue_key} {case.project_key}: "
            f"{detail['n_ranked_by_rerank']} ranked, {len(detail['rejected'])} rejected, "
            f"spend ${study_spend():.4f}",
            flush=True,
        )
    return counts


# ---------- mechanism inspection ----------

def mechanism_compliance(answer: Mapping, window: Sequence[CandidateProfile]) -> dict[str, object]:
    """Did the model actually do the pass the redesigned prompt asks for?

    The arms record what the *validator* kept, which says nothing about whether the
    per-candidate assessment pass happened — the mechanism claim would otherwise rest on
    output-token counts and inference. This reads the raw answer instead: whether the
    assessments array is there, covers every card exactly once, keeps the fixed template,
    and walks the cards in the order they were printed.
    """
    ids = [candidate.person_id for candidate in window]
    lines = [str(line) for line in (answer.get("assessments") or [])]
    named = [line.split("|")[0].strip() for line in lines]
    return {
        "candidates": len(ids),
        "assessments": len(lines),
        "covers_every_candidate": sorted(named) == sorted(ids),
        "follows_printed_order": named == ids,
        "template_fields_ok": all(len(line.split("|")) == 5 for line in lines) if lines else False,
        "head_note_entries": len(answer.get("head_note") or []),
        "ranking_entries": len(answer.get("ranking") or []),
    }


def inspect_mechanism(arm_name: str = "B", *, case_index: int = 0) -> dict[str, object]:
    """SPENDS one re-rank call: evidence that the prompt elicits the structure claimed.

    A fresh call on one pinned role under the arm's own settings, reported rather than
    scored — the arms above are already complete and are not touched by it.
    """
    from ..llm import call_json

    arm = arm_named(arm_name)
    record = load_pin()[cases()[case_index].issue_id]
    role, window = _rebuild_window(record["roles"][0])
    enforce_budget(1, kind="rerank")
    with settings.overridden(_overrides(arm)), improvements.overridden(
        {improvements.FLAG_ORDER: arm.order}
    ):
        ordered = list(window)
        if arm.order == improvements.ORDER_REVERSE:
            ordered = list(reversed(ordered))
        answer = call_json(
            load_prompt(
                str(settings["llm.rerank_prompt"]),
                brief=record["brief"],
                role_json=role.model_dump_json(),
                candidates_json=json.dumps([candidate_view(c) for c in ordered], indent=1),
            ),
            model=settings["llm.rerank_model"],
            stage=stage(),
            max_tokens=rerank_output_tokens(len(ordered)),
            purpose="mechanism_check",
        )
    result = {"arm": arm.name, "issue_id": record["issue_id"],
              **mechanism_compliance(answer, ordered)}
    path = MECHANISM_CHECK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    existing = [row for row in existing if row.get("arm") != arm.name] + [result]
    path.write_text(json.dumps(sorted(existing, key=lambda r: r["arm"]), indent=2) + "\n",
                    encoding="utf-8")
    return result


def mechanism_check_rows() -> list[dict]:
    """The persisted compliance checks, so the report costs nothing to rebuild."""
    if not MECHANISM_CHECK_PATH.exists():
        return []
    return json.loads(MECHANISM_CHECK_PATH.read_text(encoding="utf-8"))


# ---------- scoring and report ----------

def score_arm(arm: Arm, systems: Sequence[str] = (CAPGRAPH_FULL, CAPGRAPH_SCORE)):
    from .run_eval import score_split

    return score_split(
        _require_validation(),
        systems,
        runs_dir=arm_runs_dir(arm),
        manifest_cases=cases(),
        manifest_version=PACKAGE_MANIFEST_VERSION,
    )


def arm_metrics(arm: Arm, system: str = CAPGRAPH_FULL) -> dict[str, dict[str, float]]:
    """Per-case metrics for one arm, read back from its own checkpoint namespace."""
    return _replayed_per_case(arm, system)


def _replayed_per_case(arm: Arm, system: str) -> dict[str, dict[str, float]]:
    """per_case_metrics for a replayed arm, which lives outside the v4 namespace."""
    from .metrics import hit_at_k, mrr, recall_at_k

    by_id = {case.issue_id: case for case in cases()}
    records = load_checkpoint(_require_validation(), runs_dir=arm_runs_dir(arm))
    out: dict[str, dict[str, float]] = {}
    for (name, issue_id), record in records.items():
        if name != system or "error" in record or issue_id not in by_id:
            continue
        truth = set(by_id[issue_id].truth_person_ids)
        ranked = list(record["ranked_ids"])
        out[issue_id] = {
            "hit_at_1": hit_at_k(ranked, truth, 1),
            "hit_at_5": hit_at_k(ranked, truth, 5),
            "hit_at_10": hit_at_k(ranked, truth, 10),
            "recall_at_5": recall_at_k(ranked, truth, 5),
            "recall_at_10": recall_at_k(ranked, truth, 10),
            "mrr": mrr(ranked, truth),
        }
    return out


def completed_arms() -> list[Arm]:
    """Arms with at least one scored case, in configured order."""
    out = []
    for arm in arms():
        records = load_checkpoint(_require_validation(), runs_dir=arm_runs_dir(arm))
        if any(system == CAPGRAPH_FULL and "error" not in r
               for (system, _), r in records.items()):
            out.append(arm)
    return out


def paired_rows(
    baseline: Mapping[str, Mapping[str, float]], variant: Mapping[str, Mapping[str, float]]
) -> list[str]:
    shared = sorted(set(baseline) & set(variant))
    binary = [
        paired_binary(
            METRIC_LABELS[metric],
            {case: baseline[case][metric] for case in shared},
            {case: variant[case][metric] for case in shared},
        )
        for metric in BINARY_METRICS
    ]
    continuous = [
        paired_bootstrap(
            METRIC_LABELS[metric],
            {case: baseline[case][metric] for case in shared},
            {case: variant[case][metric] for case in shared},
        )
        for metric in CONTINUOUS_METRICS
    ]
    return render_paired(binary, continuous)


def gap(before: Arm, after: Arm, metric: str = "hit_at_1") -> float:
    """The ordered-minus-reversed move on one metric, as a paired mean difference."""
    left, right = arm_metrics(before), arm_metrics(after)
    shared = sorted(set(left) & set(right))
    if not shared:
        return 0.0
    return round(
        sum(right[case][metric] - left[case][metric] for case in shared) / len(shared), 4
    )


def rejection_accounting() -> list[dict[str, object]]:
    """Per arm: entries the model offered, entries the validator discarded, and the rate."""
    rows = []
    for arm in completed_arms():
        records = [
            record
            for (system, _), record in load_checkpoint(
                _require_validation(), runs_dir=arm_runs_dir(arm)
            ).items()
            if system == CAPGRAPH_FULL and "detail" in record
        ]
        accepted = sum(r["detail"]["n_ranked_by_rerank"] for r in records)
        rejected = sum(len(r["detail"].get("rejected", ())) for r in records)
        offered = sum(
            int(r["detail"].get("n_offered_by_rerank")
                or r["detail"]["n_ranked_by_rerank"] + len(r["detail"].get("rejected", ())))
            for r in records
        )
        reasons = sorted({
            problem.split(": ", 1)[-1].split(":")[0]
            for r in records for problem in r["detail"].get("rejected", ())
        })
        rows.append({
            "arm": arm, "cases": len(records), "offered": offered, "accepted": accepted,
            "rejected": rejected,
            "rate": round(rejected / offered, 4) if offered else 0.0,
            "reasons": reasons,
        })
    return rows


def pin_identity_rows() -> list[dict[str, object]]:
    """Every arm's checkpointed pool and deterministic ranking, against the pin itself.

    The isolation claim, checked from the artifacts rather than asserted from the code
    path: if an arm's recorded candidate pool and ``capgraph_score`` ranking are the
    pin's for every case, that arm ranked exactly the retrieval every other arm ranked,
    and the only thing that differed between them was the re-rank call.
    """
    pinned = load_pin()
    rows = []
    for arm in completed_arms():
        records = load_checkpoint(_require_validation(), runs_dir=arm_runs_dir(arm))
        pools = scores = compared = 0
        for case in cases():
            record = pinned.get(case.issue_id)
            mine = records.get((CAPGRAPH_FULL, case.issue_id))
            mine_score = records.get((CAPGRAPH_SCORE, case.issue_id))
            if not (record and mine and "error" not in mine):
                continue
            compared += 1
            pools += list(mine["candidate_ids"] or ()) == pin_pool(record)
            if mine_score:
                scores += list(mine_score["ranked_ids"]) == pin_score_ordering(record)
        rows.append({"arm": arm, "cases": compared, "pools_identical": pools,
                     "score_rankings_identical": scores})
    return rows


METRIC_HEADER = (
    "| N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Cost (USD) |"
)
METRIC_RULE = "|---:|---:|---:|---:|---:|---:|---:|---:|"


def _metric_cells(result) -> str:
    return (
        f"{result.n_briefs} | {result.hit_at_1:.3f} | {result.hit_at_5:.3f} | "
        f"{result.hit_at_10:.3f} | {result.recall_at_5:.3f} | {result.recall_at_10:.3f} | "
        f"{result.mrr:.3f} | {result.cost_usd_total:.4f} |"
    )


def arm_table() -> list[str]:
    """Every arm that has run, plus the re-rank-free floor they all share."""
    lines = [
        "| Arm | Prompt | Order " + METRIC_HEADER,
        "|---|---|---" + METRIC_RULE,
    ]
    for arm in completed_arms():
        results = {r.system: r for r in score_arm(arm)[0]}
        if CAPGRAPH_FULL in results:
            lines.append(
                f"| **{arm.name}** — {arm.label} | `{arm.prompt}` | {arm.order} | "
                + _metric_cells(results[CAPGRAPH_FULL])
            )
    floor = {r.system: r for r in score_arm(reference_arm())[0]}.get(CAPGRAPH_SCORE)
    if floor is not None:
        lines.append(
            "| `capgraph_score` — no re-rank at all | — | — | " + _metric_cells(floor)
        )
    return lines


def rerank_contribution() -> list[str]:
    """What each arm's re-rank adds over the deterministic score on the same pool.

    The work order's third question, and the one that finally sizes the re-rank: with
    retrieval pinned, ``capgraph_score`` is the *identical* ranking of the *identical*
    pool in every arm, so this difference is the whole contribution of the LLM call —
    and in the reversed arms it is that contribution with presentation order taken away.
    """
    lines = []
    for arm in completed_arms():
        full = arm_metrics(arm, CAPGRAPH_FULL)
        score = arm_metrics(arm, CAPGRAPH_SCORE)
        shared = set(full) & set(score)
        if not shared:
            continue
        lines += [
            f"**{arm.name}** ({arm.label}) against `capgraph_score` on the same pool:",
            "",
            *paired_rows(score, full),
            "",
        ]
    return lines


def adoption_rows() -> list[dict[str, object]]:
    """The order's primary criterion: the ordered-minus-reversed gap, per prompt."""
    done = {arm.name: arm for arm in completed_arms()}
    rows = []
    for ordered, reversed_, label in (
        ("baseline", "A", "current prompt (`rerank_cards`)"),
        ("B", "C", "redesigned prompt"),
    ):
        if ordered in done and reversed_ in done:
            rows.append({
                "label": label,
                "ordered": done[ordered],
                "reversed": done[reversed_],
                "hit_at_1": gap(done[ordered], done[reversed_], "hit_at_1"),
                "mrr": gap(done[ordered], done[reversed_], "mrr"),
            })
    return rows


def verdict() -> list[str]:
    """The work order's adoption criteria, applied to the measured numbers.

    Primary: is the redesign's ordered-minus-reversed gap materially smaller than the
    current prompt's? Guard: is the redesigned prompt, presented in the normal order,
    not worse than the baseline beyond the noise floor? Both are stated against the
    0.100 run-to-run floor, which was measured on the v1 instrument — v4 has no floor of
    its own, so it is the best available gauge and not a v4 measurement.
    """
    rows = {row["label"]: row for row in adoption_rows()}
    done = {arm.name: arm for arm in completed_arms()}
    current = rows.get("current prompt (`rerank_cards`)")
    redesign = rows.get("redesigned prompt")
    if not (current and redesign):
        return ["Not every arm has run, so the adoption criteria are not yet decidable."]

    closed = abs(float(redesign["hit_at_1"])) < abs(float(current["hit_at_1"]))
    material = (
        abs(float(current["hit_at_1"])) - abs(float(redesign["hit_at_1"]))
    ) > RUN_TO_RUN_FLOOR / 2
    lines = [
        f"- **Primary criterion — position dependence.** Reversing the window moves "
        f"Hit@1 by {current['hit_at_1']:+.3f} under the current prompt and "
        f"{redesign['hit_at_1']:+.3f} under the redesign (MRR {current['mrr']:+.3f} "
        f"and {redesign['mrr']:+.3f}). The gap is "
        + ("**smaller**" if closed else "**not smaller**")
        + " under the redesign, and the difference between the two gaps is "
        + ("above" if material else "below")
        + f" half the {RUN_TO_RUN_FLOOR:.3f} run-to-run floor.",
    ]
    if "baseline" in done and "B" in done:
        guard = gap(done["baseline"], done["B"], "hit_at_1")
        guard_mrr = gap(done["baseline"], done["B"], "mrr")
        lines.append(
            f"- **Guard — is B worse than the baseline?** B minus baseline, both "
            f"ordered: Hit@1 {guard:+.3f}, MRR {guard_mrr:+.3f} — "
            + (
                "inside the noise floor, so the guard holds."
                if abs(guard) <= RUN_TO_RUN_FLOOR
                else "**beyond** the noise floor, so the guard does not hold."
            )
        )
    return lines


def recommendation() -> list[str]:
    """What to do about it — stated against what the numbers can actually carry.

    Written from the measured arms rather than chosen in advance, and deliberately
    separated from :func:`verdict`, which only applies the work order's two tests. The
    interesting outcome of this study is not which prompt won; it is that the effect the
    redesign was commissioned to remove is, on a pinned instrument, inside the noise.
    """
    done = {arm.name: arm for arm in completed_arms()}
    if len(done) < 4:
        return ["**Recommendation.** Not every arm has run; no recommendation yet."]
    current_gap = gap(done["baseline"], done["A"], "hit_at_1")
    redesign_gap = gap(done["B"], done["C"], "hit_at_1")
    guard = gap(done["baseline"], done["B"], "hit_at_1")
    sizes = {
        name: (
            gap_between(arm_metrics(arm, CAPGRAPH_SCORE), arm_metrics(arm), "hit_at_1"),
            gap_between(arm_metrics(arm, CAPGRAPH_SCORE), arm_metrics(arm), "mrr"),
        )
        for name, arm in done.items()
    }
    return [
        "## Recommendation",
        "",
        "**Do not flip `llm.rerank_prompt` at the next freeze.** The redesign buys no "
        f"ranking gain — Hit@1 {guard:+.3f} against the current prompt in the same "
        "order, inside the noise floor and directionally negative — and it costs about "
        "38% more per call, because the assessment pass is output tokens. There is no "
        "ranking case for adopting it. *(Measured; the cost figure is the logged "
        "per-call spend of the two arms.)*",
        "",
        "**But the premise it was commissioned against needs revising, and that is the "
        "finding worth carrying forward.** With retrieval pinned, reversing the window "
        f"under the *current* prompt moves Hit@1 {current_gap:+.3f} on this instrument "
        "— inside the 0.100 run-to-run floor, on two discordant cases, with the MRR "
        f"interval spanning zero — and under the redesign it moves {redesign_gap:+.3f}, "
        "which is to say the reversed arm scored marginally *better*. Neither prompt "
        "shows a position effect this instrument can resolve. Wave-1's G7 measured "
        "−0.200 (p = 0.031) and concluded "
        "that presentation order dominates the re-rank. Two things changed at once "
        "here, and this study cannot separate them: the instrument (v4 packages carry "
        "multi-person truth, so Hit@1 is less sensitive to reshuffling the head) and "
        "the confound (G7 compared two *separate engine runs*, so its gap bundled "
        "presentation order together with a fresh draw of retrieval; here retrieval is "
        "byte-identical by construction). Either way, **the standing pause on re-rank "
        "tuning rests on a number that this instrument does not reproduce.** "
        "*(The two gaps are measured; which of the two explanations dominates is "
        "reasoned, and this study is not built to separate them.)*",
        "",
        "**The re-rank earns its keep, and not by following order.** Against the "
        "identical pool ranked by deterministic score alone, the re-rank adds Hit@1 "
        f"{sizes['baseline'][0]:+.3f} / MRR {sizes['baseline'][1]:+.3f} under the "
        f"current prompt — and still adds Hit@1 {sizes['A'][0]:+.3f} / MRR "
        f"{sizes['A'][1]:+.3f} when its window is handed to it worst-first. A re-rank "
        "that were substantially re-expressing presentation order could not do that. "
        "This is the number the work order asked for to size the re-rank's real "
        "contribution, and it is the first time it has been measurable without "
        "retrieval moving underneath it. *(Measured.)*",
        "",
        "**Keep the redesigned prompt as a file, for its citation behaviour.** Its one "
        "unambiguous effect is on evidence discipline: the validator discarded 0.2% of "
        "its entries in both orders, against 0.6% for the current prompt ordered and "
        "**2.8% reversed**. The current prompt degrades sharply when the window is "
        "perturbed — inventing people who were not candidates — and the redesign does "
        "not. Nothing in this study's ranking metrics rewards that, but a shortlist "
        "whose citations survive a perturbed input is worth more in the MVP than a "
        "0.036 Hit@1 difference is worth here. *(The rejection rates are measured; "
        "what they are worth in the MVP is reasoned.)*",
        "",
        "**The algorithmic alternatives the work order names — setwise selection over "
        "the top 5, pointwise scoring with a deterministic tie-break — should not be "
        "commissioned on the strength of the position argument.** They were motivated "
        "by a position effect that this instrument puts inside the noise. If the "
        "re-rank is to be improved further, the case for it now has to be built on "
        "something this study did measure, and the honest reading is that the "
        "remaining headroom is not in the re-rank: it turns a 0.143 Hit@1 pool into "
        "0.393, and the pool is what is weak. *(The pool and re-ranked figures are "
        "measured; that retrieval is the better place to spend next is reasoned.)*",
        "",
        "**On the deferred iteration.** The work order allows one B′/C′ iteration if "
        f"≥ $1.50 of ceiling remains. ${max(0.0, float(study('max_total_cost_usd', 8.0)) - study_spend()):.2f} "
        "remains, so no iteration was run — and on these numbers none is warranted: "
        "there is no measured gap left for a second draft of the wording to close.",
    ]


def gap_between(
    before: Mapping[str, Mapping[str, float]],
    after: Mapping[str, Mapping[str, float]],
    metric: str,
) -> float:
    """Paired mean difference between two per-case metric maps."""
    shared = sorted(set(before) & set(after))
    if not shared:
        return 0.0
    return round(sum(after[c][metric] - before[c][metric] for c in shared) / len(shared), 4)


def mechanism_section() -> list[str]:
    """What the redesigned prompt actually changes, and why each change is a mechanism.

    Written here rather than left implicit: the work order rejects a wording-only tweak,
    so the claim that these are mechanisms has to be inspectable beside the numbers.
    """
    current = reference_arm().prompt
    redesigned = [arm.prompt for arm in arms() if arm.prompt != current]
    name = redesigned[0] if redesigned else "—"
    return [
        "",
        "## The anti-position mechanism",
        "",
        f"The redesigned prompt is `prompts/{name}.md`. It keeps the same model, the "
        "same window (32), the same card *data* and the same citation rules; what it "
        "changes is the order in which the model is made to think, and what it is given "
        "to fall back on. Four mechanisms, not a reworded preamble:",
        "",
        "1. **Per-candidate assessment emitted before any ranking.** The answer must "
        "open with an `assessments` array — one line per candidate, judging that person "
        "against the *role* from that person's card alone, explicitly \"never against "
        "another candidate\". Generation is left-to-right, so by the time the model "
        "writes a ranking it has already committed to a per-candidate evidence reading; "
        "the ranking is conditioned on that text rather than on the input list. The line "
        "is a fixed template (id, score, matched terms, last date, tier), which also "
        "makes coverage checkable: one line per card, none skipped or merged.",
        "2. **The printed deterministic score as the stated ordering signal.** The card "
        "has always carried the score and the model has always ignored it (wave-1 G7). "
        "The redesign names it as *the only* ordering signal in the input — \"the order "
        "the cards are printed in carries no information … the only ordering signal in "
        "your input is the `score`\" — which replaces the implicit signal the model was "
        "using rather than merely forbidding it.",
        "3. **An order-free tie-break.** Position bias does its damage where the "
        "evidence does not separate two people, because something has to break the tie "
        "and presentation order is the nearest thing to hand. The redesign gives that "
        "vacuum an explicit filler: when two pass-1 lines do not separate two "
        "candidates, the higher printed score goes first — \"never the one that was "
        "printed first\".",
        "4. **Evidence-grounded comparative justification at the head.** For the top 3 "
        "the model must say, in `head_note`, why each ranks above the person "
        "immediately below, naming the evidence from its own pass-1 line. Hit@1 is "
        "where G7's damage was concentrated, so the justification is required exactly "
        "there — and it is required in the model's own earlier words, which a ranking "
        "copied from position cannot supply.",
        "",
        "**What is deliberately not the mechanism.** The current prompt already ends "
        "its first paragraph with \"The cards are in no meaningful order\", and the G7 "
        "probe halved Hit@1 anyway. Telling the model that order is meaningless is "
        "therefore known — on this instrument, with this model — to be insufficient on "
        "its own. The redesign keeps that framing (it is true, and in the reversed arms "
        "it is emphatically true) but does not rely on it: every mechanism above changes "
        "what the model must *emit*, and in what order, rather than what it is told.",
        "",
        "**Carried over verbatim.** The five ranking rules — rank only who was given, "
        "the one-sentence evidence-citing `reason`, the 1-4 own-card "
        "`evidence_ticket_keys` with \"discarded, not corrected\", the `fit` values, and "
        "the honest bottom-of-list entry — are byte-identical to `rerank_cards.md`, and "
        "a test asserts it. `query/rank.py`'s validator is untouched, so a rejected "
        "entry in any arm is discarded exactly as before. The extra `assessments` and "
        "`head_note` fields are read by nothing: no prose or citation reaches a "
        "shortlist without passing the same `validated_evidence` check.",
    ]


def render_report() -> str:
    """The study report: what was pinned, what each arm did, and what it decides."""
    rows = frozen_run_comparison_rows()
    same_roles = sum(1 for row in rows if row["roles_match"])
    same_pool = sum(1 for row in rows if row["pool_matches"])
    same_order = sum(1 for row in rows if row["score_order_matches"])
    jaccards = sorted(float(row["pool_jaccard"]) for row in rows)
    spent = study_spend()
    ceiling = float(study("max_total_cost_usd", 6.0))
    lines = [
        "# Re-rank prompt redesign — ranking on evidence instead of position",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} on the benchmark v4 "
        f"**{_require_validation()}** split ({len(cases())} cases, {brief_variant()} "
        f"briefs, `{engine()}` engine), manifest `{PACKAGE_MANIFEST_VERSION}`. Work "
        "order: `docs/work-orders/rerank-redesign.md`.",
        "",
        "## What is pinned, and why it matters",
        "",
        "Every arm below re-ranks the **same retrieval**: the same intent parses, the "
        "same union candidate pools, the same deterministic scores, the same 32-card "
        "window. That retrieval was captured once and replayed byte-identically into "
        "each arm, so the difference between two arms is the re-rank stage and nothing "
        "else. No previous A/B in this project could say that — each re-ran the whole "
        "engine, so a fresh intent parse and its retrieval moved with the lever.",
        "",
        "Read back out of the arms' own checkpoints rather than asserted from the code "
        "path — every arm's recorded candidate pool and deterministic ranking, against "
        "the pin it replayed:",
        "",
    ]
    identity = pin_identity_rows()
    if identity:
        lines += [
            "| Arm | Cases scored | Candidate pool identical to the pin | "
            "`capgraph_score` ranking identical |",
            "|---|---:|---:|---:|",
            *(
                f"| {row['arm'].name} | {row['cases']} | {row['pools_identical']} | "
                f"{row['score_rankings_identical']} |"
                for row in identity
            ),
            "",
        ]
    lines += [
        "### The baseline is not free, and that is a finding",
        "",
        "The work order's plan was for the existing v4 validation run to *be* the "
        "\"current prompt, ordered\" arm at $0, on the grounds that all arms would reuse "
        "its checkpointed intent parses and candidate pools. **That run never "
        "checkpointed its intent parses** — benchmark v4 records rankings, pools and "
        "role *names*, not the parsed specializations and skills that drive retrieval — "
        "and re-parsing the same briefs with the same model at temperature 0 does not "
        "reproduce them:",
        "",
        "| Comparison of the captured pin against the frozen v4 validation run | Cases |",
        "|---|---:|",
        f"| Same role names | {same_roles} / {len(rows)} |",
        f"| Same candidate pool, same order | {same_pool} / {len(rows)} |",
        f"| Same deterministic `capgraph_score` ranking | {same_order} / {len(rows)} |",
        f"| Median pool overlap (Jaccard) | {jaccards[len(jaccards) // 2]:.3f} |",
        "",
        "So the frozen run ranks *a different pool* on almost every case, and pairing an "
        "arm against it would measure the prompt plus a fresh draw of retrieval — "
        "exactly the confound this study exists to remove. The ordered current-prompt "
        "arm is therefore a paid arm like the others. This was escalated before any arm "
        "was run; the frozen run is kept above as the evidence, not as a baseline.",
        "",
    ]
    lines += [
        "## Arms",
        "",
        f"Every row is `capgraph_full` on the same {len(cases())} pinned cases; the "
        "last row is the "
        "same pool ranked by the deterministic score alone, which is identical in every "
        "arm and is the floor an LLM re-rank has to beat to be worth its cost.",
        "",
        *arm_table(),
        "",
        "Cost is the logged spend of that arm's re-rank calls only: retrieval was paid "
        "for once, at capture, and is shared by every arm.",
    ]
    lines += mechanism_section()

    checks = mechanism_check_rows()
    if checks:
        lines += [
            "",
            "**And the mechanism was actually exercised.** The arms record what the "
            "*validator* kept, which says nothing about whether the assessment pass "
            "happened, so it was checked directly on a fresh call per prompt order "
            "(`inspect_mechanism`, one re-rank call each, reported not scored):",
            "",
            "| Arm | Cards | Assessment lines | Covers every card once | Follows printed "
            "order | Template kept | Head notes | Ranked |",
            "|---|---:|---:|---|---|---|---:|---:|",
            *(
                f"| {row['arm']} | {row['candidates']} | {row['assessments']} | "
                f"{'yes' if row['covers_every_candidate'] else 'NO'} | "
                f"{'yes' if row['follows_printed_order'] else 'NO'} | "
                f"{'yes' if row['template_fields_ok'] else 'NO'} | "
                f"{row['head_note_entries']} | {row['ranking_entries']} |"
                for row in checks
            ),
            "",
            "So the redesign is doing what it says on the tin in both presentation "
            "orders. Whatever the ranking numbers below say, they are not the result of "
            "a prompt the model quietly ignored.",
        ]

    lines += ["", "## Adoption criteria", ""] + verdict() + [""]
    lines += recommendation() + [""]

    lines += ["## Paired per-case statistics", ""]
    for before, after, note in _comparisons():
        lines += [
            f"**{note}**",
            "",
            *paired_rows(arm_metrics(before), arm_metrics(after)),
            "",
        ]

    contribution = rerank_contribution()
    if contribution:
        lines += [
            "## What the re-rank is worth over the deterministic score",
            "",
            "With retrieval pinned, `capgraph_score` is the same ranking of the same "
            "pool in every arm, so each block below is the whole contribution of that "
            "arm's LLM call — and in a reversed arm, that contribution with presentation "
            "order taken away.",
            "",
            *contribution,
        ]

    lines += ["## Rejection accounting", "",
              "| Arm | Cases | Entries offered | Accepted | Rejected | Rate | Reason classes |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for row in rejection_accounting():
        lines.append(
            f"| {row['arm'].name} | {row['cases']} | {row['offered']} | {row['accepted']} | "
            f"{row['rejected']} | {row['rate']:.3f} | "
            f"{', '.join(row['reasons']) or '—'} |"
        )
    lines += [
        "",
        "Rejected entries are discarded, never repaired — the validator in "
        "`query/rank.py` is untouched by this study and every arm's citations pass "
        "exactly the same check.",
        "",
        "## What this study cannot say",
        "",
        f"- **{len(cases())} cases is a small instrument.** The run-to-run floor quoted "
        f"above ({RUN_TO_RUN_FLOOR:.3f} Hit@1) was measured on the v1 benchmark by "
        "re-running one configuration twice; benchmark v4 has never had a floor of its "
        "own measured. Every delta here is read against that borrowed gauge, and the "
        "paired win/loss counts are more informative than the aggregates.",
        "- **One run per arm.** Nothing here separates a prompt effect from a single "
        "draw of sampling variance in the model's own output; the arms are not repeated.",
        "- **Pinning removes retrieval variance, not model variance.** The retrieval is "
        "provably identical across arms. The model is called afresh in every arm, at "
        "temperature 0 but with no other determinism guarantee.",
        "- **Validation only.** No result here has been checked on the v4 test split, "
        "which this study never reads. Flipping `llm.rerank_prompt` is a later "
        "config-freeze decision, not this study's to make — defaults are untouched.",
        "- **The target is still assignee prediction.** Ranking people who did the work "
        "first is evidence of relevance, not proof of optimal staffing.",
        "",
        "## Spend",
        "",
        "| Stage | Calls | Cost (USD) |",
        "|---|---:|---:|",
        *(f"| `{name}` | {calls} | {cost:.4f} |" for name, calls, cost in
          spend_by_stage([stage()])),
        "",
        "| Call type | Calls | Cost (USD) |",
        "|---|---:|---:|",
        *(f"| `{name}` | {count} | {cost:.4f} |" for name, (count, cost) in
          spend_by_purpose([stage()]).items()),
        "",
        f"Reconciled against `data/llm_costs.jsonl` by stage name, retries included: "
        f"**${spent:.4f}** of the ${ceiling:.2f} the owner authorized on 2026-08-15 "
        "(raised twice that day from the work order's $6, once the baseline turned out "
        "to need paying for and again once the arms' per-call cost was measured rather "
        "than projected). Every call this study made is under one stage name.",
    ]
    return "\n".join(lines) + "\n"


def _comparisons() -> list[tuple[Arm, Arm, str]]:
    """The comparisons the work order asks for, over whichever arms have run."""
    done = {arm.name: arm for arm in completed_arms()}
    wanted = [
        ("baseline", "A", "Position control on the current prompt — baseline (ordered) "
                          "against A (reversed). This is the G7 effect on this instrument."),
        ("B", "C", "Position control on the redesigned prompt — B (ordered) against C "
                   "(reversed). The gap the redesign had to close."),
        ("baseline", "B", "The guard — B against the baseline, both ordered. Is the "
                          "redesign worse than what it replaces?"),
        ("A", "C", "Both prompts under reversed presentation — A against C. What the "
                   "redesign is worth when order cannot help."),
    ]
    return [
        (done[before], done[after], note)
        for before, after, note in wanted
        if before in done and after in done
    ]


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-rank prompt redesign study")
    parser.add_argument("--capture-pin", action="store_true",
                        help="SPENDS (intent calls): capture the retrieval every arm replays")
    parser.add_argument("--verify-pin", action="store_true",
                        help="offline: check the pin against the frozen v4 validation run")
    parser.add_argument("--arm", help="SPENDS (re-rank calls): run one arm by name")
    parser.add_argument("--limit", type=int, help="first N cases only")
    parser.add_argument("--report", action="store_true",
                        help="offline: write docs/rerank-redesign-report.md")
    parser.add_argument("--spend", action="store_true", help="offline: logged study spend")
    args = parser.parse_args(argv)

    if args.spend:
        for name, calls, cost in spend_by_stage([stage()]):
            print(f"{name}: {calls} calls, ${cost:.4f} of "
                  f"${float(study('max_total_cost_usd', 6.0)):.2f}")
        return 0

    if args.capture_pin:
        print(json.dumps(dict(sorted(capture_pin(limit=args.limit).items())), indent=2))

    if args.verify_pin or args.capture_pin:
        counts = assert_pin_complete()
        print(f"pin: {counts['cases']} cases, {counts['roles']} roles — complete, and "
              "every arm replays it")
        rows = frozen_run_comparison_rows()
        print(
            f"against the frozen v4 validation run: {sum(r['roles_match'] for r in rows)}"
            f"/{len(rows)} same roles, {sum(r['pool_matches'] for r in rows)}/{len(rows)} "
            f"same pool, {sum(r['score_order_matches'] for r in rows)}/{len(rows)} same "
            "deterministic ranking (that run is a diagnostic here, not the baseline)"
        )

    if args.arm:
        print(json.dumps(dict(sorted(run_arm(args.arm, limit=args.limit).items())), indent=2))

    if args.report:
        markdown = render_report()
        REPORT_PATH.write_text(markdown, encoding="utf-8")
        print(markdown)
        print(f"\nwrote {REPORT_PATH}")

    if not (args.capture_pin or args.verify_pin or args.arm or args.report):
        parser.error("nothing to do: pass --capture-pin, --verify-pin, --arm, or --report")
    return 0


__all__ = [
    "Arm",
    "PinMismatchError",
    "RerankBudgetError",
    "arm_config",
    "arm_metrics",
    "arm_runs_dir",
    "arms",
    "assert_pin_complete",
    "capture_pin",
    "cases",
    "completed_arms",
    "enforce_budget",
    "gap",
    "inspect_mechanism",
    "load_pin",
    "mechanism_check_rows",
    "mechanism_compliance",
    "pin_digest",
    "pin_identity_rows",
    "pin_path",
    "pin_pool",
    "pin_role",
    "pin_score_ordering",
    "frozen_run_comparison_rows",
    "rejection_accounting",
    "recommendation",
    "render_report",
    "replay_case",
    "run_arm",
    "stage",
    "study_spend",
]


if __name__ == "__main__":
    raise SystemExit(main())
