"""Re-rank probes: literature methods against the last-mile ranking.

    uv run python -m capgraph.eval.rerank_probes --diagnose      # offline
    uv run python -m capgraph.eval.rerank_probes --verify-pin    # offline
    uv run python -m capgraph.eval.rerank_probes --arm S1        # SPENDS (re-rank only)
    uv run python -m capgraph.eval.rerank_probes --gate          # offline
    uv run python -m capgraph.eval.rerank_probes --report        # offline

The re-rank redesign study left the ranking question in a specific place. Retrieval is
not what loses the top-1 misses on this split: **every** one of them has a correct
person inside the 32-card window the model was shown (:func:`miss_decomposition`), and
the re-rank put someone else first anyway. This study probes published re-ranking
methods against exactly that bucket, cheaply and pre-registered, and adopts nothing —
a winner graduates to a later freeze order, a clean set of failures closes the question.

**It reuses the redesign study's instrument rather than rebuilding it.** The pin
(``data/eval/rerank_redesign/pin/``) and the baseline arm's checkpoints are read
**read-only**: no arm here re-captures retrieval, and the baseline is not re-paid for.
Every arm therefore replays the same intent parses, the same union pools, the same
deterministic scores and the same window that produced the 0.393 / 0.607 / 0.501 the
probes are read against — and that is checked from the artifacts rather than assumed
(:func:`pin_identity_rows` compares each arm's recorded pool *and* the baseline's back
to the pin itself).

**What an arm may vary** is the re-rank stage and nothing else: which ``prompts/*.md``
it loads, which model ranks, how the window is rendered
(``retrieval.rerank_candidate_view``), what shape of answer is asked for
(``retrieval.rerank_mode``), and the presentation order. Window width, card data,
candidate pool and the evidence validator are fixed. An arm that generates claims passes
:func:`capgraph.query.rank.validated_evidence` unchanged; an arm that only orders ids
makes no claims at all, and :func:`rejection_accounting` labels which is which.

**Spend discipline.** All spend lands under one cost-log stage (``rerank_probes``) with
its own ceiling, re-checked before every chunk of cases rather than once up front. Per
the work order the per-call ceiling may be raised for one arm's calls; that raise is a
per-arm setting override applied inside :func:`replay_case`, so it cannot leak into any
other arm or into the defaults. The v4 test split is not reachable from this module:
:func:`_require_validation` refuses anything but the validation split.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .. import improvements
from ..models import CandidateProfile, RoleSpec
from ..query.rank import finish, rerank
from ..settings import DATA_DIR, settings
from . import rerank_redesign as redesign
from .costs import CostMeter, spend_by_purpose, spend_by_stage
from .packages import PACKAGE_MANIFEST_VERSION, PackageManifestEntry, load_package_manifest
from .paired import paired_binary, paired_bootstrap, render_paired
from .run_eval import append_record, config_digest, load_checkpoint
from .run_v4 import METRIC_LABELS, engine_overrides, v4_config
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE, round_robin

STUDY = "rerank_probes"
SPLIT_SETTING = f"eval.{STUDY}.split"
BINARY_METRICS = ("hit_at_1", "hit_at_5", "hit_at_10")
CONTINUOUS_METRICS = ("recall_at_5", "recall_at_10", "mrr")

REPORT_PATH = Path(__file__).resolve().parents[3] / "docs" / "rerank-probes-report.md"

# The per-metric floor benchmark v4 measured on *this* instrument: one pinned-pool
# repeat of the baseline arm, model-only variance (docs/deterministic-sweeps-report.md).
# v1's 0.100 is not used here — it was measured on a retired instrument and is both too
# loose for Hit@1 and too tight for Recall@5.
FLOORS = {
    "hit_at_1": 0.0357,
    "hit_at_5": 0.0714,
    "hit_at_10": 0.0357,
    "recall_at_5": 0.0946,
    "recall_at_10": 0.0098,
    "mrr": 0.0341,
}

# The work order's sequencing gate: after two paid arms, continue only if one of these
# two metrics moved beyond its floor with paired support.
GATE_METRICS = ("hit_at_1", "mrr")


class ProbeBudgetError(RuntimeError):
    """The study's own ceiling, checked before every chunk of cases."""


class PreRegistrationError(RuntimeError):
    """An arm has not been pre-registered in the report, so it must not spend."""


class SequencingGateError(RuntimeError):
    """Two arms have run without signal, so the remaining budget goes back."""


# ---------- configuration ----------

def study(name: str, default=None):
    return settings.get(f"eval.{STUDY}.{name}", default)


@dataclass(frozen=True)
class ProbeArm:
    """One probe: a method, and the re-rank-stage settings that express it.

    Every field except ``name``/``label``/``method``/``citation`` is a setting override
    scoped to this arm's calls. ``reorder_only`` records whether the method generates
    claims (which the evidence validator then checks) or only orders ids — the work
    order requires the two to be labelled apart, and :func:`rejection_accounting` reads
    it rather than inferring it from an empty rejection list.
    """

    name: str
    label: str
    method: str
    citation: str
    prompt: str
    order: str = "score"
    model: str | None = None
    view: str | None = None
    mode: str | None = None
    hybrid_detail_top_k: int | None = None
    output_tokens_per_candidate: int | None = None
    max_call_cost_usd: float | None = None
    reorder_only: bool = False
    projected_call_usd: float | None = None
    # What the answer is expected to cost in output tokens. Only the pre-flight reads
    # it, to turn the gateway's worst case (the whole allowance) into what the arm will
    # actually cost. A permutation answer is nothing like a listwise one, so it cannot
    # be one study-wide number.
    expected_output_tokens: int | None = None


def arms() -> list[ProbeArm]:
    configured = study("arms") or []
    if not isinstance(configured, Sequence) or isinstance(configured, str):
        raise TypeError(f"eval.{STUDY}.arms must be a list of arm definitions")
    out = [
        ProbeArm(
            name=str(entry["name"]),
            label=str(entry.get("label") or entry["name"]),
            method=str(entry.get("method") or ""),
            citation=str(entry.get("citation") or ""),
            prompt=str(entry["prompt"]),
            order=str(entry.get("order", "score")),
            model=None if entry.get("model") is None else str(entry["model"]),
            view=None if entry.get("view") is None else str(entry["view"]),
            mode=None if entry.get("mode") is None else str(entry["mode"]),
            hybrid_detail_top_k=(
                None if entry.get("hybrid_detail_top_k") is None
                else int(entry["hybrid_detail_top_k"])
            ),
            output_tokens_per_candidate=(
                None if entry.get("output_tokens_per_candidate") is None
                else int(entry["output_tokens_per_candidate"])
            ),
            max_call_cost_usd=(
                None if entry.get("max_call_cost_usd") is None
                else float(entry["max_call_cost_usd"])
            ),
            reorder_only=bool(entry.get("reorder_only", False)),
            projected_call_usd=(
                None if entry.get("projected_call_usd") is None
                else float(entry["projected_call_usd"])
            ),
            expected_output_tokens=(
                None if entry.get("expected_output_tokens") is None
                else int(entry["expected_output_tokens"])
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


def arm_named(name: str) -> ProbeArm:
    for arm in arms():
        if arm.name == name:
            return arm
    raise ValueError(f"unknown arm '{name}'; known: {', '.join(a.name for a in arms())}")


def _require_validation() -> str:
    """The only split this module may touch, refused rather than defaulted.

    The v4 test split has one exposure left and its budget belongs to a freeze order,
    not to a probe. Nothing here — diagnostic, arm, or report — can be pointed at it by
    a flag or a typo: the split name is read once, here, and anything but ``validation``
    stops.
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
    """The rerank-redesign pin, read-only. This study captures no retrieval of its own."""
    return DATA_DIR / "eval" / str(study("source_pin", "rerank_redesign/pin")) / "validation.jsonl"


def arm_runs_dir(arm: ProbeArm) -> Path:
    """Where an arm's checkpoint lives. One namespace per arm; they never mix."""
    return DATA_DIR / "eval" / str(study("root_subdir", STUDY)) / "runs" / arm.name


def baseline_arm() -> redesign.Arm:
    """The rerank-redesign reference arm — this study's baseline, read-only.

    Taken from ``eval.rerank_redesign.arms`` rather than restated here, so "the same
    prompt, the same order, the same pin" is structural rather than a copied string, and
    so a change to that study's definition could not silently re-point this one.
    """
    return redesign.arm_named(str(study("baseline_arm", "baseline")))


def cases() -> list[PackageManifestEntry]:
    return sorted(
        load_package_manifest(splits=(_require_validation(),), brief_variant=brief_variant()),
        key=lambda case: case.issue_id,
    )


def arm_config(arm: ProbeArm) -> dict[str, object]:
    """The frozen v4 validation configuration, plus everything this arm varies.

    Built so two arms can never be appended to one checkpoint: the prompt, model, view,
    answer mode, presentation order and the pin digest all enter the configuration, so
    the digest differs whenever any of them does.
    """
    with settings.overridden(_overrides(arm)):
        config = v4_config(_require_validation(), engine(), brief_variant())
        allowance = int(settings["llm.rerank_output_tokens_per_candidate"])
        model = str(settings["llm.rerank_model"])
        view = str(settings["retrieval.rerank_candidate_view"])
        mode = str(settings["retrieval.rerank_mode"])
    config.update(
        {
            "stage": stage(),
            "study": STUDY,
            "arm": arm.name,
            "rerank_model": model,
            "rerank_candidate_view": view,
            "rerank_mode": mode,
            "rerank_hybrid_detail_top_k": arm.hybrid_detail_top_k,
            "rerank_presentation_order": arm.order,
            "rerank_output_tokens_per_candidate": allowance,
            "pinned_retrieval": True,
            "pin_digest": redesign.pin_digest(pin_path()),
        }
    )
    return config


def _overrides(arm: ProbeArm) -> dict[str, object]:
    """The settings one arm changes. Scoped to its own calls, never written to disk.

    The presentation order is not here — it is an ``improvements`` flag, applied through
    :func:`capgraph.improvements.overridden` so a reversed arm runs the same code path
    the G7 probe measured.
    """
    overrides: dict[str, object] = {
        **engine_overrides(engine()),
        "llm.rerank_prompt": arm.prompt,
    }
    if arm.model is not None:
        overrides["llm.rerank_model"] = arm.model
    if arm.view is not None:
        overrides["retrieval.rerank_candidate_view"] = arm.view
    if arm.mode is not None:
        overrides["retrieval.rerank_mode"] = arm.mode
    if arm.hybrid_detail_top_k is not None:
        overrides["retrieval.rerank_hybrid_detail_top_k"] = arm.hybrid_detail_top_k
    if arm.output_tokens_per_candidate is not None:
        overrides["llm.rerank_output_tokens_per_candidate"] = arm.output_tokens_per_candidate
    if arm.max_call_cost_usd is not None:
        # The work order's scoped raise. It applies to this arm's calls only: the
        # override is entered per case in replay_case and unwound with it, so
        # config/settings.yaml keeps the $0.05 default that governs every other caller.
        overrides["llm.max_call_cost_usd"] = arm.max_call_cost_usd
    return overrides


# ---------- spend control ----------

def study_spend() -> float:
    return spend_by_stage([stage()])[0][2]


def ceiling() -> float:
    return float(study("max_total_cost_usd", 15.0))


def projected_call_cost(arm: ProbeArm) -> float:
    """What one of this arm's re-rank calls is projected to cost.

    Per arm rather than per study: the arms differ by a factor of five in model price
    and by a factor of two in input size, so a single projection would be far too loose
    for the cheap arms and too tight for the dear ones. Over-projecting can only refuse
    a chunk early, which is the safe direction.
    """
    if arm.projected_call_usd is not None:
        return float(arm.projected_call_usd)
    return float(dict(study("projection") or {})["rerank_call_usd"])


def enforce_budget(pending_calls: int, *, arm: ProbeArm) -> float:
    """Refuse the next chunk when it would break the owner's authorization."""
    projected = pending_calls * projected_call_cost(arm)
    spent = study_spend()
    if spent + projected > ceiling():
        raise ProbeBudgetError(
            f"projected {STUDY} spend ${spent + projected:.2f} (logged ${spent:.4f} + "
            f"projected ${projected:.2f} for {pending_calls} calls at "
            f"${projected_call_cost(arm):.4f} each) exceeds the "
            f"eval.{STUDY}.max_total_cost_usd ceiling of ${ceiling():.2f} — escalate to "
            "the orchestrator before running more of this study"
        )
    return projected


# ---------- pre-registration and the sequencing gate ----------

def preregistered_arms(path: Path | None = None) -> set[str]:
    """Arm names the report pre-registers, read out of the report itself.

    The work order's first rule is that an arm is written down — method, mechanism,
    projected cost, failure condition — *before* its first call. That is enforced here
    rather than trusted: :func:`run_arm` refuses to spend on an arm whose heading is not
    already in ``docs/rerank-probes-report.md``.
    """
    path = REPORT_PATH if path is None else path
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    return {arm.name for arm in arms() if f"### Arm {arm.name} —" in text}


def assert_preregistered(arm: ProbeArm, path: Path | None = None) -> None:
    if arm.name not in preregistered_arms(path):
        raise PreRegistrationError(
            f"arm '{arm.name}' is not pre-registered: {REPORT_PATH.name} has no "
            f"'### Arm {arm.name} — ' section. The work order requires the method, its "
            "citation, the mechanism, the projected cost and the failure condition to "
            "be written down before the arm's first call — no post-hoc arm swaps."
        )


def gate_rows() -> list[dict[str, object]]:
    """Per completed arm, the gate metrics against their floors, with paired support."""
    base = baseline_per_case()
    rows = []
    for arm in completed_arms():
        variant = arm_metrics(arm)
        shared = sorted(set(base) & set(variant))
        row: dict[str, object] = {"arm": arm, "n": len(shared)}
        for metric in GATE_METRICS:
            delta = gap_between(base, variant, metric)
            wins = sum(1 for c in shared if variant[c][metric] > base[c][metric])
            losses = sum(1 for c in shared if variant[c][metric] < base[c][metric])
            row[metric] = {
                "delta": delta,
                "floor": FLOORS[metric],
                "beyond_floor": delta > FLOORS[metric],
                "wins": wins,
                "losses": losses,
                "paired_support": wins > losses,
            }
        row["signal"] = any(
            row[metric]["beyond_floor"] and row[metric]["paired_support"]
            for metric in GATE_METRICS
        )
        rows.append(row)
    return rows


def gate_open() -> bool:
    """The work order's rule: after two arms, at least one must show signal."""
    rows = gate_rows()
    if len(rows) < 2:
        return True
    return any(row["signal"] for row in rows)


def assert_gate_open(arm: ProbeArm) -> None:
    """Refuse a third or later arm when the first two produced nothing."""
    done = {a.name for a in completed_arms()}
    if arm.name in done or len(done) < 2 or gate_open():
        return
    raise SequencingGateError(
        f"the sequencing gate is closed: {len(done)} arms have run "
        f"({', '.join(sorted(done))}) and none moved Hit@1 or MRR beyond its measured "
        f"floor ({FLOORS['hit_at_1']:.3f} / {FLOORS['mrr']:.3f}) with paired support. "
        "The work order says stop and report — the remaining budget goes back."
    )


# ---------- the pin, reused read-only ----------

def load_pin() -> dict[str, dict]:
    return redesign.load_pin(pin_path())


def assert_pin_complete() -> dict[str, int]:
    """Refuse to spend an arm against a pin that is missing or half-captured."""
    return redesign.assert_pin_complete(pin_path())


def pin_identity_rows() -> list[dict[str, object]]:
    """Every arm's recorded pool and deterministic ranking, against the pin itself.

    The isolation claim, checked from the artifacts rather than asserted from the code
    path. The baseline row is the one that matters most here and is the one the redesign
    study could not provide for a *later* study: it proves that the arm this study pairs
    against ranked the same pin these arms replay, so a probe's delta is the re-rank
    method and not a different retrieval draw.
    """
    pinned = load_pin()
    rows: list[dict[str, object]] = []
    entries: list[tuple[str, Path, bool]] = [
        (baseline_arm().name, redesign.arm_runs_dir(baseline_arm()), True),
        *((arm.name, arm_runs_dir(arm), False) for arm in completed_arms()),
    ]
    for name, runs, is_baseline in entries:
        records = load_checkpoint(_require_validation(), runs_dir=runs)
        pools = scores = compared = 0
        for case in cases():
            record = pinned.get(case.issue_id)
            mine = records.get((CAPGRAPH_FULL, case.issue_id))
            mine_score = records.get((CAPGRAPH_SCORE, case.issue_id))
            if not (record and mine and "error" not in mine):
                continue
            compared += 1
            pools += list(mine["candidate_ids"] or ()) == redesign.pin_pool(record)
            if mine_score:
                scores += list(mine_score["ranked_ids"]) == redesign.pin_score_ordering(record)
        rows.append({"arm": name, "baseline": is_baseline, "cases": compared,
                     "pools_identical": pools, "score_rankings_identical": scores})
    return rows


# ---------- the failure bucket, measured offline ----------

def _window_ids(record: Mapping) -> list[str]:
    ids: list[str] = []
    for role in record["roles"]:
        ids.extend(str(c["person_id"]) for c in role["window"])
    return list(dict.fromkeys(ids))


def _window_positions(record: Mapping, person_id: str) -> list[tuple[int, int]]:
    """Where this person sits in each role window, in deterministic score order."""
    out = []
    for role in record["roles"]:
        scored = sorted(role["window"], key=lambda c: (-float(c["score"]), c["person_id"]))
        ids = [c["person_id"] for c in scored]
        if person_id in ids:
            out.append((ids.index(person_id) + 1, len(ids)))
    return out


def miss_decomposition() -> dict[str, object]:
    """Where the baseline's top-1 misses actually fail. Offline, free, and the premise.

    The work order commissions probes against one bucket: the misses where a correct
    person **was** in the window the model saw and was ranked too low. This measures
    that bucket on the pin rather than importing the figure, and it measures the thing
    an arm design actually needs — how deep in the deterministic ordering those people
    sit, which is what decides whether spending extra evidence on the head of the window
    could reach them at all.
    """
    pinned = load_pin()
    records = load_checkpoint(_require_validation(), runs_dir=redesign.arm_runs_dir(baseline_arm()))
    rows: list[dict[str, object]] = []
    for case in cases():
        record = pinned.get(case.issue_id)
        mine = records.get((CAPGRAPH_FULL, case.issue_id))
        if not (record and mine and "error" not in mine):
            continue
        truth = list(case.truth_person_ids)
        ranked = list(mine["ranked_ids"])
        pool, window = redesign.pin_pool(record), _window_ids(record)
        positions = [pos for person in truth for pos in _window_positions(record, person)]
        ranks = [ranked.index(p) + 1 for p in truth if p in ranked]
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "project_key": case.project_key,
            "hit_at_1": bool(ranks) and min(ranks) == 1,
            "best_rank": min(ranks) if ranks else None,
            "truth_in_pool": sum(1 for p in truth if p in pool),
            "truth_in_window": sum(1 for p in truth if p in window),
            "best_window_position": min((p for p, _ in positions), default=None),
            "window_positions": positions,
        })
    misses = [row for row in rows if not row["hit_at_1"]]
    shown = [row for row in misses if int(row["truth_in_window"]) > 0]
    reach = {
        k: sum(1 for row in shown
               if row["best_window_position"] is not None and row["best_window_position"] <= k)
        for k in (4, 8, 12, 16, 24, 32)
    }
    return {
        "cases": len(rows),
        "misses": len(misses),
        "shown_but_ranked_low": len(shown),
        "outside_window_but_in_pool": sum(
            1 for row in misses
            if int(row["truth_in_window"]) == 0 and int(row["truth_in_pool"]) > 0
        ),
        "outside_pool": sum(1 for row in misses if int(row["truth_in_pool"]) == 0),
        "reachable_by_detail_top_k": reach,
        "rows": rows,
    }


def mechanism_rows() -> list[dict[str, object]]:
    """Per arm: which top-1 cases it fixed, which it broke, and *where* the fixes landed.

    An aggregate delta cannot tell a targeted mechanism from a lucky draw. This can, at
    least partly: an arm whose extra evidence is spent on the head of the window can
    only help a case whose truth person is *in* that head, so the fixes it produces
    should concentrate there. Splitting the misses into the ones the mechanism reached
    and the ones it did not is the closest this instrument gets to asking whether the
    arm worked for the reason it was pre-registered for.

    The split is only meaningful for an arm that has a head (``hybrid_detail_top_k``);
    for the others the reachable counts are ``None`` and only fixed/broke is reported.
    """
    diag = miss_decomposition()
    rows_by_id = {row["issue_id"]: row for row in diag["rows"]}
    base = baseline_per_case()
    out = []
    for arm in completed_arms():
        variant = arm_metrics(arm)
        shared = [i for i in rows_by_id if i in base and i in variant]
        fixed = [i for i in shared if base[i]["hit_at_1"] == 0.0 and variant[i]["hit_at_1"] == 1.0]
        broke = [i for i in shared if base[i]["hit_at_1"] == 1.0 and variant[i]["hit_at_1"] == 0.0]
        row: dict[str, object] = {
            "arm": arm,
            "fixed": len(fixed),
            "broke": len(broke),
            "fixed_keys": [rows_by_id[i]["issue_key"] for i in fixed],
            "broke_keys": [rows_by_id[i]["issue_key"] for i in broke],
            "reachable_fixed": None, "reachable": None,
            "unreachable_fixed": None, "unreachable": None,
        }
        if arm.hybrid_detail_top_k is not None:
            head = int(arm.hybrid_detail_top_k)
            misses = [i for i in shared if base[i]["hit_at_1"] == 0.0]
            inside = [
                i for i in misses
                if rows_by_id[i]["best_window_position"] is not None
                and int(rows_by_id[i]["best_window_position"]) <= head
            ]
            outside = [i for i in misses if i not in inside]
            row.update({
                "reachable": len(inside),
                "reachable_fixed": sum(1 for i in inside if variant[i]["hit_at_1"] == 1.0),
                "unreachable": len(outside),
                "unreachable_fixed": sum(1 for i in outside if variant[i]["hit_at_1"] == 1.0),
            })
        out.append(row)
    return out


# ---------- pre-flight: price the real request before spending on it ----------

def preflight(arm: ProbeArm) -> dict[str, object]:
    """Offline: render every call this arm would send, and price it. No model call.

    Two things this catches before an arm spends, both of which have bitten this project
    before. First, a per-call ceiling that the arm's widest window does not fit: the
    gateway refuses such a call, and finding that out on case 19 of 28 wastes the 18
    before it. Second, a projection that is wrong in the expensive direction — the
    numbers here are rendered from the pin through the *same* prompt builder the re-rank
    uses (:func:`capgraph.query.rank.rerank_prompt_text`), so they cannot drift from
    what is actually sent.

    The output-token side is the gateway's own worst case (the whole allowance), which
    is what the ceiling is checked against; the ``expected`` figure re-prices it at the
    output length the baseline arm actually produced, which is what the arm will cost.
    """
    from ..llm import estimate_tokens, model_price_usd_per_mtok
    from ..query.rank import rerank_input, rerank_max_tokens, rerank_prompt_text

    expected_output = int(
        arm.expected_output_tokens
        if arm.expected_output_tokens is not None
        else study("expected_output_tokens", 3272)
    )
    pinned = load_pin()
    worst = {"estimate": 0.0, "issue_key": "", "input_tokens": 0, "allowance": 0}
    totals = {"calls": 0, "input_tokens": 0, "worst_case_usd": 0.0, "expected_usd": 0.0}
    with settings.overridden(_overrides(arm)), improvements.overridden(
        {improvements.FLAG_ORDER: arm.order}
    ):
        price_in, price_out = model_price_usd_per_mtok(str(settings["llm.rerank_model"]))
        ceiling_usd = float(settings["llm.max_call_cost_usd"])
        for case in cases():
            record = pinned.get(case.issue_id)
            if record is None:
                continue
            for role_record in record["roles"]:
                role = RoleSpec.model_validate(role_record["role"])
                window = rerank_input(
                    [CandidateProfile.model_validate(e) for e in role_record["window"]]
                )
                ordered = list(window)
                if arm.order == improvements.ORDER_REVERSE:
                    ordered = list(reversed(ordered))
                prompt = rerank_prompt_text(record["brief"], role, ordered)
                tokens_in = estimate_tokens(prompt)
                allowance = rerank_max_tokens(len(ordered))
                estimate = tokens_in / 1e6 * price_in + allowance / 1e6 * price_out
                totals["calls"] += 1
                totals["input_tokens"] += tokens_in
                totals["worst_case_usd"] += estimate
                totals["expected_usd"] += (
                    tokens_in / 1e6 * price_in + expected_output / 1e6 * price_out
                )
                if estimate > worst["estimate"]:
                    worst = {"estimate": estimate, "issue_key": case.issue_key,
                             "input_tokens": tokens_in, "allowance": allowance}
    calls = max(totals["calls"], 1)
    return {
        "arm": arm.name,
        "calls": totals["calls"],
        "per_call_ceiling_usd": ceiling_usd,
        "mean_input_tokens": round(totals["input_tokens"] / calls),
        "worst_call": worst,
        "worst_call_fits_ceiling": worst["estimate"] <= ceiling_usd,
        "arm_worst_case_usd": round(totals["worst_case_usd"], 4),
        "arm_expected_usd": round(totals["expected_usd"], 4),
        "expected_per_call_usd": round(totals["expected_usd"] / calls, 4),
        "projected_per_call_usd": projected_call_cost(arm),
    }


# ---------- arms ----------

def replay_case(record: Mapping, *, arm: ProbeArm) -> tuple[dict, dict]:
    """Re-rank one pinned case under one arm. The only step that calls a model.

    Mirrors :func:`capgraph.query.engine.query` from the re-rank onwards — same
    ``rerank``/``finish`` calls, same padding of the ranking with the deterministic
    remainder, same round-robin merge across roles — so the ranking this produces is the
    one the engine would have produced under this arm's settings. The arm is applied as
    scoped overrides, so it cannot leak into the next arm and nothing in
    ``config/settings.yaml`` changes between arms.
    """
    stage_name = stage()
    per_role: list[list[str]] = []
    rejected: list[str] = []
    accepted = offered = 0
    counts: dict[str, int] = {}
    meter = CostMeter()
    meter.mark()
    with settings.overridden(_overrides(arm)), improvements.overridden(
        {improvements.FLAG_ORDER: arm.order}
    ):
        for role_record in record["roles"]:
            role = RoleSpec.model_validate(role_record["role"])
            window = [CandidateProfile.model_validate(e) for e in role_record["window"]]
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
        "reorder_only": arm.reorder_only,
        "timings_ms": {},
        "cost_usd_by_purpose": dict(sorted(spend.by_purpose.items())),
        "n_llm_calls": spend.n_calls,
    }
    return {"ranked_ids": round_robin(per_role), "cost_usd": spend.total}, detail


def _record(
    *, arm: ProbeArm, system: str, case: PackageManifestEntry, digest: str,
    pool: Sequence[str], ranked: Sequence[str], cost: float,
    detail: dict | None = None, error: str | None = None,
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
        # paid once, at capture, and is not re-run per arm.
        "latency_ms": 0.0,
        "cost_usd": round(float(cost), 6),
    })
    if detail:
        record["detail"] = detail
    return record


def run_arm(name: str, *, limit: int | None = None) -> dict[str, int]:
    """SPENDS (re-rank calls only): run one pre-registered arm over the pinned cases."""
    arm = arm_named(name)
    assert_preregistered(arm)          # never spend on an arm that was not written down
    assert_gate_open(arm)              # never spend a third arm after two dead ones
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
        f"calls, prompt '{arm.prompt}', model '{arm.model or settings['llm.rerank_model']}', "
        f"view '{arm.view or settings['retrieval.rerank_candidate_view']}', "
        f"mode '{arm.mode or settings['retrieval.rerank_mode']}', order '{arm.order}', "
        f"digest {digest}, logged ${study_spend():.4f} of ${ceiling():.2f}"
    )
    if not todo:
        return counts

    chunk = max(1, int(study("chunk_size", 4)))
    for index, case in enumerate(todo, 1):
        if (index - 1) % chunk == 0:
            pending = sum(
                len(pinned[c.issue_id]["roles"]) for c in todo[index - 1: index - 1 + chunk]
            )
            projected = enforce_budget(pending, arm=arm)
            print(f"  chunk from case {index}: projected ${projected:.2f}")
        record = pinned[case.issue_id]
        pool = redesign.pin_pool(record)
        counts["ran"] += 1
        try:
            output, detail = replay_case(record, arm=arm)
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
        # checkpoint that disagreed with the pin would prove the replay broken.
        append_record(split, _record(arm=arm, system=CAPGRAPH_SCORE, case=case,
                                     digest=digest, pool=pool,
                                     ranked=redesign.pin_score_ordering(record), cost=0.0),
                      runs_dir=target)
        print(
            f"  [{index}/{len(todo)}] {case.issue_key} {case.project_key}: "
            f"{detail['n_ranked_by_rerank']} ranked, {len(detail['rejected'])} rejected, "
            f"spend ${study_spend():.4f}",
            flush=True,
        )
    return counts


# ---------- scoring ----------

def _per_case(runs: Path, system: str) -> dict[str, dict[str, float]]:
    """per_case_metrics for a replayed arm, which lives outside the v4 namespace."""
    from .metrics import hit_at_k, mrr, recall_at_k

    by_id = {case.issue_id: case for case in cases()}
    records = load_checkpoint(_require_validation(), runs_dir=runs)
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


def arm_metrics(arm: ProbeArm, system: str = CAPGRAPH_FULL) -> dict[str, dict[str, float]]:
    return _per_case(arm_runs_dir(arm), system)


def baseline_per_case(system: str = CAPGRAPH_FULL) -> dict[str, dict[str, float]]:
    """The rerank-redesign baseline arm's per-case metrics, read out of its checkpoint."""
    return _per_case(redesign.arm_runs_dir(baseline_arm()), system)


def score_arm(arm: ProbeArm, systems: Sequence[str] = (CAPGRAPH_FULL, CAPGRAPH_SCORE)):
    from .run_eval import score_split

    return score_split(
        _require_validation(), systems, runs_dir=arm_runs_dir(arm),
        manifest_cases=cases(), manifest_version=PACKAGE_MANIFEST_VERSION,
    )


def completed_arms() -> list[ProbeArm]:
    """Arms with at least one scored case, in configured order."""
    out = []
    for arm in arms():
        records = load_checkpoint(_require_validation(), runs_dir=arm_runs_dir(arm))
        if any(system == CAPGRAPH_FULL and "error" not in r
               for (system, _), r in records.items()):
            out.append(arm)
    return out


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


def paired_rows(
    baseline: Mapping[str, Mapping[str, float]], variant: Mapping[str, Mapping[str, float]]
) -> list[str]:
    shared = sorted(set(baseline) & set(variant))
    binary = [
        paired_binary(METRIC_LABELS[metric],
                      {case: baseline[case][metric] for case in shared},
                      {case: variant[case][metric] for case in shared})
        for metric in BINARY_METRICS
    ]
    continuous = [
        paired_bootstrap(METRIC_LABELS[metric],
                         {case: baseline[case][metric] for case in shared},
                         {case: variant[case][metric] for case in shared})
        for metric in CONTINUOUS_METRICS
    ]
    return render_paired(binary, continuous)


def rejection_accounting() -> list[dict[str, object]]:
    """Per arm: what the model offered, what the validator discarded, and of what kind.

    The baseline row is recomputed from the redesign study's own checkpoint rather than
    transcribed, so a wrong transcription could not survive. ``claims`` records whether
    the arm's answers contained prose and citations at all: a reorder-only arm cannot
    fail the evidence validator because it never offers evidence, and reporting its 0%
    beside a claim-generating arm's rate without that label would be misleading.
    """
    rows = []
    entries: list[tuple[str, Path, bool]] = [
        (baseline_arm().name, redesign.arm_runs_dir(baseline_arm()), False),
        *((arm.name, arm_runs_dir(arm), arm.reorder_only) for arm in completed_arms()),
    ]
    for name, runs, reorder_only in entries:
        records = [
            record for (system, _), record in
            load_checkpoint(_require_validation(), runs_dir=runs).items()
            if system == CAPGRAPH_FULL and "detail" in record
        ]
        if not records:
            continue
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
            "arm": name, "cases": len(records), "offered": offered, "accepted": accepted,
            "rejected": rejected, "rate": round(rejected / offered, 4) if offered else 0.0,
            "reasons": reasons,
            "claims": "no (orders ids only)" if reorder_only else "yes (reason + citations)",
        })
    return rows


# ---------- report ----------

METRIC_HEADER = "| N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Cost (USD) |"
METRIC_RULE = "|---:|---:|---:|---:|---:|---:|---:|---:|"


def _metric_cells(result) -> str:
    return (
        f"{result.n_briefs} | {result.hit_at_1:.3f} | {result.hit_at_5:.3f} | "
        f"{result.hit_at_10:.3f} | {result.recall_at_5:.3f} | {result.recall_at_10:.3f} | "
        f"{result.mrr:.3f} | {result.cost_usd_total:.4f} |"
    )


def arm_table() -> list[str]:
    """The baseline (read-only, $0 here), every probe that has run, and the score floor."""
    lines = ["| Arm | Method | Model | Answer " + METRIC_HEADER,
             "|---|---|---|---" + METRIC_RULE]
    base = {r.system: r for r in redesign.score_arm(baseline_arm())[0]}
    if CAPGRAPH_FULL in base:
        lines.append(
            "| **baseline** — rerank-redesign reference arm | listwise cards | "
            "`gpt-5.6-terra` | reasons + citations | " + _metric_cells(base[CAPGRAPH_FULL])
        )
    for arm in completed_arms():
        results = {r.system: r for r in score_arm(arm)[0]}
        if CAPGRAPH_FULL in results:
            answer = "ids only" if arm.reorder_only else "reasons + citations"
            model = (arm.model or str(settings["llm.rerank_model"])).replace("openai/", "")
            lines.append(
                f"| **{arm.name}** — {arm.label} | {arm.method} | `{model}` | {answer} | "
                + _metric_cells(results[CAPGRAPH_FULL])
            )
    floor = base.get(CAPGRAPH_SCORE)
    if floor is not None:
        lines.append("| `capgraph_score` — no re-rank at all | — | — | — | "
                     + _metric_cells(floor))
    return lines


def floor_table() -> list[str]:
    """Every arm's move against the per-metric floor measured on this instrument."""
    base = baseline_per_case()
    lines = ["| Arm | Metric | Baseline | Arm | Δ | Floor | Beyond floor? | Wins | Losses |",
             "|---|---|---:|---:|---:|---:|---|---:|---:|"]
    for arm in completed_arms():
        variant = arm_metrics(arm)
        shared = sorted(set(base) & set(variant))
        for metric in ("hit_at_1", "hit_at_5", "mrr"):
            delta = gap_between(base, variant, metric)
            wins = sum(1 for c in shared if variant[c][metric] > base[c][metric])
            losses = sum(1 for c in shared if variant[c][metric] < base[c][metric])
            mean_b = sum(base[c][metric] for c in shared) / len(shared) if shared else 0.0
            mean_v = sum(variant[c][metric] for c in shared) / len(shared) if shared else 0.0
            verdict = "**yes**" if abs(delta) > FLOORS[metric] else "no"
            lines.append(
                f"| {arm.name} | {METRIC_LABELS[metric]} | {mean_b:.3f} | {mean_v:.3f} | "
                f"{delta:+.3f} | {FLOORS[metric]:.3f} | {verdict} | {wins} | {losses} |"
            )
    return lines


def cost_rows() -> list[dict[str, object]]:
    """Per arm: calls, logged spend, and cost per call, read from the arm's records."""
    rows = []
    for arm in completed_arms():
        records = [
            r for (system, _), r in
            load_checkpoint(_require_validation(), runs_dir=arm_runs_dir(arm)).items()
            if system == CAPGRAPH_FULL and "error" not in r
        ]
        calls = sum(int(r.get("detail", {}).get("n_llm_calls", 0)) for r in records)
        cost = sum(float(r.get("cost_usd", 0.0)) for r in records)
        rows.append({
            "arm": arm, "cases": len(records), "calls": calls, "cost": cost,
            "per_call": cost / calls if calls else 0.0,
            "projected_per_call": projected_call_cost(arm),
        })
    return rows


MEASURED_BEGIN = "<!-- measured-sections-below -->"
MEASURED_END = "<!-- measured-sections-above -->"


def render_report(path: Path | None = None) -> str:
    """Regenerate the measured middle, and keep the written prose at both ends.

    This report has three parts and only one of them is generated. The pre-registrations
    above are written by hand *before* an arm spends and must never be rewritten
    afterwards — that is the whole point of pre-registering. The recommendation below is
    a judgement, written once the numbers are in. Between the two markers is everything
    read back out of the checkpoints, which is rebuilt on every render so a stale number
    cannot survive in it.
    """
    path = REPORT_PATH if path is None else path
    head, tail = "", ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        head = text.split(MEASURED_BEGIN)[0] if MEASURED_BEGIN in text else text
        tail = text.split(MEASURED_END)[1] if MEASURED_END in text else ""
    return (
        head.rstrip("\n") + "\n\n" + MEASURED_BEGIN + "\n\n"
        + "\n".join(measured_sections()).rstrip("\n") + "\n\n"
        + MEASURED_END + "\n" + tail.lstrip("\n")
    )


def measured_sections() -> list[str]:
    diag = miss_decomposition()
    lines = [
        "## What is pinned, and proven so",
        "",
        "Every arm below re-ranks the **same retrieval** as the rerank-redesign baseline "
        "it is paired against: the same intent parses, the same union candidate pools, "
        "the same deterministic scores, the same window. This study captured no "
        "retrieval of its own — it replays that study's pin "
        f"(`{pin_path().parent.relative_to(DATA_DIR.parent)}`) read-only, and pays for "
        "re-rank calls only.",
        "",
        "Read back out of the arms' own checkpoints rather than asserted from the code "
        "path. The baseline row is the load-bearing one: it is what licenses pairing a "
        "probe against a number measured in a different study.",
        "",
        "| Arm | Cases scored | Candidate pool identical to the pin | "
        "`capgraph_score` ranking identical |",
        "|---|---:|---:|---:|",
    ]
    for row in pin_identity_rows():
        label = f"{row['arm']} (baseline, read-only)" if row["baseline"] else str(row["arm"])
        lines.append(
            f"| {label} | {row['cases']} | {row['pools_identical']} | "
            f"{row['score_rankings_identical']} |"
        )
    lines += [
        "",
        "## The bucket these probes aim at, measured on the pin",
        "",
        f"The work order commissions probes against one failure bucket. On this split it "
        f"is not a fraction of the misses — it is all of them: of the "
        f"{diag['misses']} top-1 misses the baseline makes over {diag['cases']} cases, "
        f"**{diag['shown_but_ranked_low']} had a correct person inside the window the "
        f"model was shown**, {diag['outside_window_but_in_pool']} had one in the pool "
        f"but outside the window, and {diag['outside_pool']} had none in the pool at "
        "all. Retrieval is not what loses these cases. *(Measured.)*",
        "",
        "How deep those people sit matters, because it decides whether a method that "
        "spends extra attention on the head of the window can reach them at all. Of "
        f"those {diag['shown_but_ranked_low']} misses, the number whose best truth "
        "person is inside the top *k* of the deterministic ordering of some role window:",
        "",
        "| Detail head *k* | Misses reachable |",
        "|---:|---:|",
        *(f"| {k} | {n} / {diag['shown_but_ranked_low']} |"
          for k, n in sorted(diag["reachable_by_detail_top_k"].items())),
        "",
        "The curve is flat after 8: widening the detailed head from 8 to 16 reaches "
        f"{diag['reachable_by_detail_top_k'][16] - diag['reachable_by_detail_top_k'][8]} "
        "more of these misses while roughly doubling the tokens spent on detail. "
        "*(Measured; the choice of 8 that follows is reasoned from it.)*",
        "",
        "## Arms",
        "",
        f"Every row is `capgraph_full` on the same {len(cases())} pinned cases. The "
        "baseline row is the rerank-redesign reference arm, read out of its checkpoint "
        "— this study did not re-run or re-pay for it. The last row is the same pool "
        "ranked by the deterministic score alone, identical in every arm.",
        "",
        *arm_table(),
        "",
        "Cost is the logged spend of that arm's own re-rank calls.",
        "",
        "## Against the measured floor",
        "",
        "Benchmark v4's own per-metric floor, from a pinned-pool repeat of this exact "
        f"baseline arm: Hit@1 {FLOORS['hit_at_1']:.3f}, Hit@5 {FLOORS['hit_at_5']:.3f}, "
        f"MRR {FLOORS['mrr']:.3f} (`docs/deterministic-sweeps-report.md`). v1's 0.100 is "
        "not used.",
        "",
        *floor_table(),
        "",
    ]
    lines += [
        "## Did the mechanism act where it was aimed?",
        "",
        "An aggregate delta cannot separate a targeted mechanism from a lucky draw. An "
        "arm that spends its extra evidence on the head of the window can only help a "
        "case whose truth person is in that head, so its fixes should concentrate "
        "there — and that is checkable per case rather than argued.",
        "",
        "| Arm | Top-1 cases fixed | Broken | Fixed where the mechanism applied | "
        "Fixed where it did not |",
        "|---|---:|---:|---|---|",
    ]
    for row in mechanism_rows():
        if row["reachable"] is None:
            reach = unreach = "— (mechanism applies to the whole window)"
        else:
            reach = f"{row['reachable_fixed']} / {row['reachable']}"
            unreach = f"{row['unreachable_fixed']} / {row['unreachable']}"
        lines.append(
            f"| {row['arm'].name} | {row['fixed']} | {row['broke']} | {reach} | {unreach} |"
        )
    lines += [
        "",
        *(f"- **{row['arm'].name}** fixed {', '.join(f'`{k}`' for k in row['fixed_keys']) or 'nothing'}"
          f" and broke {', '.join(f'`{k}`' for k in row['broke_keys']) or 'nothing'}."
          for row in mechanism_rows()),
        "",
        "## Paired per-case statistics",
        "",
    ]
    for arm in completed_arms():
        lines += [
            f"**{arm.name} ({arm.label}) against the rerank-redesign baseline arm, on "
            "the same pinned pools.**",
            "",
            *paired_rows(baseline_per_case(), arm_metrics(arm)),
            "",
        ]
    lines += ["## Rejection and validator accounting", "",
              "| Arm | Generates claims | Cases | Entries offered | Accepted | Rejected | "
              "Rate | Reason classes |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in rejection_accounting():
        lines.append(
            f"| {row['arm']} | {row['claims']} | {row['cases']} | {row['offered']} | "
            f"{row['accepted']} | {row['rejected']} | {row['rate']:.4f} | "
            f"{', '.join(row['reasons']) or '—'} |"
        )
    lines += [
        "",
        "The evidence validator in `query/rank.py` is untouched by this study: a "
        "rejected entry is discarded, never repaired, in every arm. A reorder-only arm "
        "answers with an ordering of ids and no prose, so it offers the validator "
        "nothing to check — its zero rejection rate is a property of the answer shape, "
        "not evidence of better citation behaviour, and the `Generates claims` column "
        "is there so the two cannot be read as the same thing.",
        "",
        "## Sequencing gate",
        "",
        "| Arm | Hit@1 Δ | beyond 0.036? | W/L | MRR Δ | beyond 0.034? | W/L | Signal |",
        "|---|---:|---|---|---:|---|---|---|",
    ]
    for row in gate_rows():
        h, m = row["hit_at_1"], row["mrr"]
        lines.append(
            f"| {row['arm'].name} | {h['delta']:+.3f} | "
            f"{'yes' if h['beyond_floor'] else 'no'} | {h['wins']}/{h['losses']} | "
            f"{m['delta']:+.3f} | {'yes' if m['beyond_floor'] else 'no'} | "
            f"{m['wins']}/{m['losses']} | {'**signal**' if row['signal'] else 'none'} |"
        )
    lines += [
        "",
        "## Spend",
        "",
        "| Arm | Cases | Calls | Cost (USD) | Per call | Projected per call |",
        "|---|---:|---:|---:|---:|---:|",
        *(f"| {row['arm'].name} | {row['cases']} | {row['calls']} | {row['cost']:.4f} | "
          f"${row['per_call']:.4f} | ${row['projected_per_call']:.4f} |"
          for row in cost_rows()),
        "",
        "| Stage | Calls | Cost (USD) |",
        "|---|---:|---:|",
        *(f"| `{name}` | {calls} | {cost:.4f} |"
          for name, calls, cost in spend_by_stage([stage()])),
        "",
        "| Call type | Calls | Cost (USD) |",
        "|---|---:|---:|",
        *(f"| `{name}` | {count} | {cost:.4f} |"
          for name, (count, cost) in spend_by_purpose([stage()]).items()),
        "",
        f"Reconciled against `data/llm_costs.jsonl` by stage name, retries included: "
        f"**${study_spend():.4f}** of the ${ceiling():.2f} the owner authorized on "
        "2026-08-16. Every call this study made is under one stage name.",
    ]
    return lines


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-rank probes study")
    parser.add_argument("--diagnose", action="store_true",
                        help="offline: the miss decomposition the arms are aimed at")
    parser.add_argument("--verify-pin", action="store_true",
                        help="offline: check the pin and the baseline arm's identity to it")
    parser.add_argument("--preflight", metavar="ARM",
                        help="offline: render and price every call an arm would send")
    parser.add_argument("--arm", help="SPENDS (re-rank calls): run one pre-registered arm")
    parser.add_argument("--limit", type=int, help="first N cases only")
    parser.add_argument("--gate", action="store_true",
                        help="offline: the sequencing gate over the arms that have run")
    parser.add_argument("--report", action="store_true",
                        help="offline: rebuild docs/rerank-probes-report.md")
    parser.add_argument("--spend", action="store_true", help="offline: logged study spend")
    args = parser.parse_args(argv)

    if args.spend:
        for name, calls, cost in spend_by_stage([stage()]):
            print(f"{name}: {calls} calls, ${cost:.4f} of ${ceiling():.2f}")

    if args.verify_pin:
        counts = assert_pin_complete()
        print(f"pin: {counts['cases']} cases, {counts['roles']} roles — complete, "
              "and read-only to this study")
        for row in pin_identity_rows():
            print(f"  {row['arm']}: {row['pools_identical']}/{row['cases']} pools "
                  f"identical, {row['score_rankings_identical']}/{row['cases']} "
                  "deterministic rankings identical")

    if args.diagnose:
        diag = miss_decomposition()
        print(json.dumps({k: v for k, v in diag.items() if k != "rows"}, indent=2))

    if args.gate:
        for row in gate_rows():
            print(f"{row['arm'].name}: " + ", ".join(
                f"{METRIC_LABELS[m]} {row[m]['delta']:+.4f} "
                f"(floor {row[m]['floor']:.4f}, {row[m]['wins']}W/{row[m]['losses']}L)"
                for m in GATE_METRICS
            ) + f" -> {'SIGNAL' if row['signal'] else 'none'}")
        print(f"gate {'OPEN' if gate_open() else 'CLOSED'}")

    if args.preflight:
        print(json.dumps(preflight(arm_named(args.preflight)), indent=2))

    if args.arm:
        print(json.dumps(dict(sorted(run_arm(args.arm, limit=args.limit).items())), indent=2))

    if args.report:
        markdown = render_report()
        REPORT_PATH.write_text(markdown, encoding="utf-8")
        print(markdown)
        print(f"wrote {REPORT_PATH}")

    if not any([args.spend, args.verify_pin, args.diagnose, args.gate, args.preflight,
                args.arm, args.report]):
        parser.error("nothing to do: pass --diagnose, --verify-pin, --preflight, --arm, "
                     "--gate, --report or --spend")
    return 0


__all__ = [
    "FLOORS",
    "PreRegistrationError",
    "ProbeArm",
    "ProbeBudgetError",
    "SequencingGateError",
    "arm_config",
    "arm_metrics",
    "arm_named",
    "arm_runs_dir",
    "arms",
    "assert_gate_open",
    "assert_pin_complete",
    "assert_preregistered",
    "baseline_arm",
    "baseline_per_case",
    "cases",
    "ceiling",
    "completed_arms",
    "enforce_budget",
    "gap_between",
    "gate_open",
    "gate_rows",
    "load_pin",
    "mechanism_rows",
    "miss_decomposition",
    "pin_identity_rows",
    "pin_path",
    "preflight",
    "preregistered_arms",
    "rejection_accounting",
    "render_report",
    "replay_case",
    "run_arm",
    "stage",
    "study_spend",
]


if __name__ == "__main__":
    raise SystemExit(main())
