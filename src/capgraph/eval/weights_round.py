"""The final weights round: the G6 control's lead, taken as far as arithmetic can take it.

The deterministic-sweeps study closed both of its levers and left exactly one lead: the
G6 *control* — a flat down-weighting of ``specialization_match`` — moved the offline
deterministic arm from 0.143 to 0.214 Hit@1. Its constant was fitted on the same 28
cases it was measured on, so that is a lead about a **weight**, not a result.

This module decides whether the lead is worth a paid arm, and it does so without
spending anything. Three facts make that possible:

* the score components of all 2187 candidates are checkpointed by the sweeps study
  (``data/eval/sweeps/offline/base/``), under pinned intent parses whose replay
  reproduced its source pin 28/28 — so any weight vector can be re-scored exactly,
  through the engine's own :func:`~capgraph.query.rank.combine_parts`;
* the re-rank is order-robust (rerank-redesign acceptance), so a retune can reach the
  full system only by changing **who is in the 32-card window**, not by reordering it;
* the window is a set, so "can a re-weighting move a truth person into it?" is a
  question about a finite grid of weight vectors — answerable by enumeration rather
  than by a $2 arm.

The selection recipe is benchmark v2's, implemented as code rather than restated as an
answer: read each component's *marginal* effect over a coarse grid, move one step from
the component whose marginal points down to the one whose marginal points up, and adopt
only what sits inside a plateau. The grid's best row is never adopted — with 28 cases a
216-point grid has more than enough freedom to fit noise.

Everything here is offline. The two paid stage names exist so the ledger can be
reconciled against them and shown to be empty when a gate stops.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from pathlib import Path

from ..query.rank import SCORE_COMPONENTS
from ..settings import DATA_DIR, settings
from . import rerank_redesign as rr
from . import sweeps
from .costs import spend_by_stage
from .run_eval import load_checkpoint
from .scores import coarse_grid

STUDY = "weights_round"

#: Metrics every arm and grid point in this round is summarized on.
METRICS = (
    "hit_at_1",
    "hit_at_5",
    "hit_at_10",
    "recall_at_5",
    "recall_at_10",
    "mrr",
    "candidate_recall",
    "window_hit",
    "window_recall",
)

#: The metrics a *weight* retune is read on. Window hit rate and window recall are the
#: ceiling on the full system; the rest are the deterministic arm's own ordering.
SELECTION_METRICS = ("hit_at_1", "hit_at_5", "mrr", "window_hit", "window_recall")


class WeightsRoundError(RuntimeError):
    """A guard in this round refused something — a split, a ceiling, a substrate."""


# ---------- configuration ----------

def study(name: str, default=None):
    return settings.get(f"eval.{STUDY}.{name}", default)


def _require_validation() -> str:
    """The only split tier 0 and tier 1 may touch, refused rather than defaulted.

    The v4 test split is opened once, in tier 2, after a freeze commit and an explicit
    orchestrator go-ahead. No typo here may reach it.
    """
    split = str(study("split", "validation"))
    if split != "validation":
        raise WeightsRoundError(
            f"eval.{STUDY}.split is {split!r}; tiers 0 and 1 are authorized on the v4 "
            "validation split only — the test split is opened by tier 2, once, after "
            "the freeze commit and the orchestrator's go-ahead"
        )
    return split


def root() -> Path:
    """Every checkpoint this round writes. Frozen namespaces are never written to."""
    return DATA_DIR / "eval" / str(study("root_subdir", "weights"))


def tier0_path() -> Path:
    return root() / "tier0.json"


def stages() -> list[str]:
    """Both paid stage names, which share one ceiling — empty when a gate stops."""
    return [str(value) for _, value in sorted(dict(study("stages") or {}).items())]


def ceiling() -> float:
    return float(study("max_total_cost_usd", 10.0))


def spend() -> float:
    return sum(cost for _, _, cost in spend_by_stage(stages()))


def selection(name: str, default=None):
    return dict(study("selection") or {}).get(name, default)


def step() -> float:
    return float(selection("step", 0.05))


def target_metric() -> str:
    return str(selection("target_metric", "hit_at_1"))


def current_weights() -> dict[str, float]:
    return dict(settings["scoring.weights"])


def window_width() -> int:
    return int(settings["retrieval.rerank_top_k"])


def levels(name: str) -> dict[str, list[float]]:
    """One configured grid, as a component -> levels map."""
    configured = dict(selection(name) or {})
    missing = [component for component in SCORE_COMPONENTS if component not in configured]
    if missing:
        raise WeightsRoundError(
            f"eval.{STUDY}.selection.{name} has no levels for {', '.join(missing)}"
        )
    return {component: [float(v) for v in configured[component]] for component in SCORE_COMPONENTS}


def grid_of(levels_map: Mapping[str, Sequence[float]]) -> list[dict[str, float]]:
    """Every combination of the levels, normalized, deduplicated, usable by the engine.

    ``combine_parts`` renormalizes over the components present, so a weight vector is
    scale-invariant: what matters is the ratios. Normalizing here means two grid points
    that differ only by scale are one point, and every vector reads as a share.
    """
    seen: dict[tuple[float, ...], dict[str, float]] = {}
    for combination in product(*(levels_map[component] for component in SCORE_COMPONENTS)):
        total = sum(combination)
        if total <= 0:
            continue
        vector = {
            component: round(value / total, 4)
            for component, value in zip(SCORE_COMPONENTS, combination, strict=True)
        }
        # A role with neither specializations nor skills is scored on the two
        # always-scored components alone; a vector that gives them nothing cannot rank
        # it at all, and the engine would refuse it.
        if vector["recency"] <= 0 and vector["evidence_strength"] <= 0:
            continue
        seen.setdefault(tuple(vector.values()), vector)
    return list(seen.values())


def simplex_grid() -> list[dict[str, float]]:
    """The whole simplex, coarsely — the upper bound on what *any* weighting can do."""
    steps = [float(v) for v in (selection("simplex_levels") or [])]
    if not steps:
        raise WeightsRoundError(f"eval.{STUDY}.selection.simplex_levels is empty")
    return grid_of(dict.fromkeys(SCORE_COMPONENTS, steps))


# ---------- the substrate ----------

def source_cases() -> list[sweeps.CaseReplay]:
    """The sweeps study's control condition: pinned parses, production graph, flags off.

    Guarded rather than assumed. Re-scoring a *lever's* checkpoint under new weights
    would measure the lever and the weights together, which is exactly the cross-run
    confound the rerank-redesign acceptance made a standing rule against.
    """
    name = str(study("source_condition", "base"))
    sidecar = sweeps.condition_sidecar(name)
    if sidecar.get("flags"):
        raise WeightsRoundError(
            f"condition '{name}' was replayed with flags {sidecar['flags']}; this round "
            "re-scores the flags-off control, so a lever's checkpoint is refused"
        )
    if str(sidecar.get("graph")) != "production":
        raise WeightsRoundError(
            f"condition '{name}' was replayed against the {sidecar.get('graph')} graph; "
            "this round re-scores the production-graph control"
        )
    return sweeps.load_condition(name)


def substrate() -> dict[str, object]:
    """Does the checkpoint being re-scored reproduce the pin it replays? Measured."""
    rows = sweeps.pin_agreement_rows(str(study("source_condition", "base")))
    sidecar = sweeps.condition_sidecar(str(study("source_condition", "base")))
    return {
        "cases": len(rows),
        "pool_identical": sum(1 for row in rows if row["pool_identical"]),
        "engine_order_identical": sum(1 for row in rows if row["engine_order_identical"]),
        "recombined_order_identical": sum(
            1 for row in rows if row["recombined_order_identical"]
        ),
        "window_identical": sum(1 for row in rows if row["window_identical"]),
        "parses_digest": sidecar.get("parses_digest"),
        "source_pin_digest": sidecar.get("source_pin_digest"),
        "manifest_version": sidecar.get("manifest_version"),
    }


# ---------- arithmetic over the checkpoint ----------

def per_case(
    cases: Sequence[sweeps.CaseReplay], weights: Mapping[str, float]
) -> dict[str, dict[str, float]]:
    """Deterministic-arm metrics per case under one weight vector.

    Delegates to the sweeps study's own measurement, so a weight arm and a lever arm are
    never scored by two implementations that could drift apart.
    """
    return sweeps.per_case_metrics(cases, weights_=dict(weights), top_k=window_width())


def aggregate(
    cases: Sequence[sweeps.CaseReplay], weights: Mapping[str, float]
) -> dict[str, float]:
    metrics = per_case(cases, weights)
    n = len(metrics) or 1
    return {
        metric: round(sum(values[metric] for values in metrics.values()) / n, 4)
        for metric in METRICS
    }


def windows(
    cases: Sequence[sweeps.CaseReplay], weights: Mapping[str, float]
) -> dict[str, set[str]]:
    """Who the re-rank would be shown, per case, under one weight vector."""
    top_k = window_width()
    return {
        case.issue_id: set(case.to_case_scores().window(dict(weights), top_k))
        for case in cases
    }


# ---------- mechanism: marginal effects ----------

def marginal_rows(
    cases: Sequence[sweeps.CaseReplay],
    *,
    grid: Sequence[Mapping[str, float]] | None = None,
    min_points: int = 5,
) -> list[dict[str, object]]:
    """Each component's mean effect over every grid point that holds it at one weight.

    This, not the grid's argmax, is what a weight decision is read from: a single top
    row on 28 cases is a coin flip, while a component whose mean metric moves one way
    across a whole grid is a mechanism.
    """
    grid = coarse_grid() if grid is None else list(grid)
    scored = [(vector, aggregate(cases, vector)) for vector in grid]
    rows: list[dict[str, object]] = []
    for component in SCORE_COMPONENTS:
        buckets: dict[float, list[dict[str, float]]] = {}
        for vector, result in scored:
            buckets.setdefault(round(vector[component], 2), []).append(result)
        for weight, bucket in sorted(buckets.items()):
            if len(bucket) < min_points:          # ignore thinly populated levels
                continue
            row: dict[str, object] = {
                "component": component,
                "weight": weight,
                "points": len(bucket),
            }
            for metric in SELECTION_METRICS:
                row[metric] = round(sum(item[metric] for item in bucket) / len(bucket), 4)
            rows.append(row)
    return rows


def direction(
    rows: Sequence[Mapping[str, object]], component: str, metric: str
) -> dict[str, object]:
    """Which way a component's marginal points, and how cleanly.

    "Monotone" is reported as the share of adjacent steps that agree with the overall
    direction rather than as a yes/no: a marginal computed over a coarse grid is a mean
    of means, and demanding strict monotonicity of it would reject mechanisms for one
    noisy level.
    """
    series = [
        (float(row["weight"]), float(row[metric]))
        for row in rows
        if row["component"] == component
    ]
    series.sort()
    if len(series) < 2:
        return {"component": component, "metric": metric, "direction": "flat", "steps": 0}
    span = series[-1][1] - series[0][1]
    ups = sum(1 for (_, a), (_, b) in zip(series, series[1:], strict=False) if b > a)
    downs = sum(1 for (_, a), (_, b) in zip(series, series[1:], strict=False) if b < a)
    steps = len(series) - 1
    agreeing, contradicting = (ups, downs) if span > 0 else (downs, ups)
    return {
        "component": component,
        "metric": metric,
        "low_weight": series[0][0],
        "high_weight": series[-1][0],
        "low": round(series[0][1], 4),
        "high": round(series[-1][1], 4),
        "span": round(span, 4),
        "direction": "up" if span > 0 else "down" if span < 0 else "flat",
        "steps": steps,
        "agreeing_steps": agreeing,
        # Steps that move *against* the overall direction. This, not the count of
        # agreeing steps, is what would disqualify a mechanism: a level that repeats the
        # one before it is not evidence against a direction, while one that reverses it
        # is.
        "contradicting_steps": contradicting,
        "monotone_share": round(agreeing / steps, 3) if steps else 0.0,
    }


def one_step_moves(
    directions: Mapping[str, Mapping[str, object]],
    current: Mapping[str, float],
) -> list[dict[str, object]]:
    """Every retune the marginals support, each a single step and nothing more.

    A component whose marginal on the target metric points *down* gives up one step; the
    component whose marginal points up hardest receives it. There are at most a handful
    of these, they are all minimal deviations from the current weighting, and none of
    them is a grid row — which is the point.
    """
    receiving = [
        component for component in SCORE_COMPONENTS
        if directions[component]["direction"] == "up"
    ]
    if not receiving:
        return []
    sink = max(receiving, key=lambda component: directions[component]["span"])
    moves = []
    for source in SCORE_COMPONENTS:
        if directions[source]["direction"] != "down" or current.get(source, 0.0) <= 0:
            continue
        amount = round(min(step(), float(current[source])), 4)
        weights = dict(current)
        weights[source] = round(weights[source] - amount, 4)
        weights[sink] = round(weights[sink] + amount, 4)
        moves.append({
            "moved_from": source,
            "moved_to": sink,
            "step": amount,
            "weights": weights,
        })
    return moves


def select_candidate(
    cases: Sequence[sweeps.CaseReplay],
    *,
    grid: Sequence[Mapping[str, float]] | None = None,
) -> dict[str, object]:
    """Benchmark v2's recipe as code: marginals decide the direction, one step is the size.

    The marginals say which components should give weight up and which should take it;
    that is the mechanism, and it is read off means over a whole grid rather than off a
    top row. Where the marginals support more than one such move — here they support two
    — each is measured and the stronger on the target metric is adopted, so a *stop*
    verdict is tested against the most favourable defensible retune rather than a
    convenient one. Every move is a single configured step from the current weighting;
    the grid's best row is never adopted.
    """
    rows = marginal_rows(cases, grid=grid)
    metric = target_metric()
    directions = {
        component: direction(rows, component, metric) for component in SCORE_COMPONENTS
    }
    current = current_weights()
    baseline = aggregate(cases, current)
    moves = one_step_moves(directions, current)
    for move in moves:
        move["metrics"] = aggregate(cases, move["weights"])
        move["delta"] = round(move["metrics"][metric] - baseline[metric], 4)
    if not moves:
        return {
            "weights": dict(current),
            "moved_from": None,
            "moved_to": None,
            "step": step(),
            "directions": directions,
            "considered": [],
            "note": "no component pair points in a retune direction; current weights stand",
        }
    chosen = max(moves, key=lambda move: (move["delta"], -abs(move["step"]), move["moved_from"]))
    source, sink = chosen["moved_from"], chosen["moved_to"]
    return {
        "weights": chosen["weights"],
        "moved_from": source,
        "moved_to": sink,
        "step": chosen["step"],
        "directions": directions,
        "considered": moves,
        "note": (
            f"one step of {chosen['step']:g} out of {source} (marginal {metric} "
            f"{directions[source]['span']:+.4f} across the grid) into {sink} "
            f"(marginal {metric} {directions[sink]['span']:+.4f}); "
            f"{len(moves)} marginal-supported move(s) measured, this one moves "
            f"{metric} {chosen['delta']:+.4f}"
        ),
    }


# ---------- plateau ----------

def plateau_rows(
    cases: Sequence[sweeps.CaseReplay],
    grid: Sequence[Mapping[str, float]],
    *,
    against: Mapping[str, float] | None = None,
) -> list[dict[str, object]]:
    """Per metric: how the whole neighbourhood scores, not just the adopted point.

    A vector adopted from a plateau is a vector whose neighbours agree with it. The
    counts here are what separate that from a spike: ``beats`` and ``ties`` are against
    the current weighting, and ``min`` is the worst a reader would get by rounding the
    adopted vector to a neighbouring grid point.
    """
    against = current_weights() if against is None else dict(against)
    baseline = aggregate(cases, against)
    scored = [aggregate(cases, vector) for vector in grid]
    rows = []
    for metric in METRICS:
        values = sorted(result[metric] for result in scored)
        if not values:
            continue
        middle = len(values) // 2
        rows.append({
            "metric": metric,
            "points": len(values),
            "min": round(values[0], 4),
            "median": round(
                values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2,
                4,
            ),
            "max": round(values[-1], 4),
            "current": round(baseline[metric], 4),
            "beats": sum(1 for value in values if value > baseline[metric] + 1e-9),
            "ties": sum(1 for value in values if abs(value - baseline[metric]) <= 1e-9),
            "worse": sum(1 for value in values if value < baseline[metric] - 1e-9),
        })
    return rows


# ---------- the window: membership, and what could ever change it ----------

def membership_rows(
    cases: Sequence[sweeps.CaseReplay],
    before: Mapping[str, float],
    after: Mapping[str, float],
) -> list[dict[str, object]]:
    """Per case: how the 32-card window's *population* moves between two weightings."""
    first, second = windows(cases, before), windows(cases, after)
    rows = []
    for case in cases:
        mine, theirs = first[case.issue_id], second[case.issue_id]
        truth = set(case.truth)
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "project_key": case.project_key,
            "before": len(mine),
            "after": len(theirs),
            "entered": sorted(theirs - mine),
            "left": sorted(mine - theirs),
            "truth_entered": sorted(truth & (theirs - mine)),
            "truth_left": sorted(truth & (mine - theirs)),
            "identical": mine == theirs,
        })
    return rows


def membership_totals(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {
        "cases": len(rows),
        "cases_changed": sum(1 for row in rows if not row["identical"]),
        "entered": sum(len(row["entered"]) for row in rows),
        "left": sum(len(row["left"]) for row in rows),
        "truth_entered": sum(len(row["truth_entered"]) for row in rows),
        "truth_left": sum(len(row["truth_left"]) for row in rows),
    }


def truth_totals(
    cases: Sequence[sweeps.CaseReplay], weights: Mapping[str, float]
) -> dict[str, int]:
    """How many truth people the re-rank is already shown, split-wide."""
    current = windows(cases, weights)
    total = sum(len(case.truth) for case in cases)
    inside = sum(len(set(case.truth) & current[case.issue_id]) for case in cases)
    return {
        "truth_people": total,
        "in_window": inside,
        "outside_window": total - inside,
        "cases_with_truth_outside": sum(
            1 for case in cases if set(case.truth) - current[case.issue_id]
        ),
    }


def order_change(
    cases: Sequence[sweeps.CaseReplay],
    before: Mapping[str, float],
    after: Mapping[str, float],
) -> dict[str, object]:
    """How far the *presentation order* of the cards moves, not just their membership.

    The other channel a retune could act through is the order the cards are printed in.
    The rerank-redesign study measured that channel at its extreme — a full reversal —
    and found -0.071 Hit@1 on two discordant cases. This measures how far short of a
    reversal a retune actually falls: the displacement of the people who stay in the
    window, and how often the first card changes at all.
    """
    top_k = window_width()
    displacements: list[float] = []
    first_card_changed = 0
    roles = 0
    for case in cases:
        scores = case.to_case_scores()
        for role in scores.roles:
            roles += 1
            was = role.ordering(dict(before))[:top_k]
            now = role.ordering(dict(after))[:top_k]
            if was and now and was[0] != now[0]:
                first_card_changed += 1
            positions = {person: index for index, person in enumerate(now)}
            shared = [person for person in was if person in positions]
            if shared:
                displacements.append(
                    sum(abs(positions[person] - was.index(person)) for person in shared)
                    / len(shared)
                )
    return {
        "roles": roles,
        "roles_whose_first_card_changed": first_card_changed,
        "mean_card_displacement": round(
            sum(displacements) / len(displacements), 3
        ) if displacements else 0.0,
        "max_card_displacement": round(max(displacements), 3) if displacements else 0.0,
    }


def truth_outside_window(
    cases: Sequence[sweeps.CaseReplay], weights: Mapping[str, float]
) -> list[dict[str, object]]:
    """The truth people the re-rank is never shown — all a retune could ever add."""
    current = windows(cases, weights)
    rows = []
    for case in cases:
        missing = sorted(set(case.truth) - current[case.issue_id])
        if missing:
            rows.append({
                "issue_id": case.issue_id,
                "issue_key": case.issue_key,
                "truth": len(case.truth),
                "outside": missing,
            })
    return rows


def baseline_first_choices() -> dict[str, dict[str, object]]:
    """Who the paid re-rank actually ranked first, per case, in the baseline arm.

    Read-only from the rerank-redesign reference arm's checkpoint. This is the other
    half of the propagation question: a retune can force the full system to change its
    answer only by taking the person it chose *out* of the window.
    """
    runs_dir = DATA_DIR / "eval" / str(study("baseline_runs_subdir")) / rr.reference_arm().name
    records = load_checkpoint(_require_validation(), runs_dir=runs_dir)
    out: dict[str, dict[str, object]] = {}
    for (system, issue_id), record in records.items():
        if system != "capgraph_full":
            continue
        ranked = [str(person) for person in record.get("ranked_ids") or []]
        if ranked:
            out[str(issue_id)] = {"first": ranked[0]}
    return out


def choice_rows(
    cases: Sequence[sweeps.CaseReplay],
    before: Mapping[str, float],
    after: Mapping[str, float],
    choices: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Per case: was the baseline's rank-1 person right, and does the retune remove them?"""
    choices = baseline_first_choices() if choices is None else choices
    first, second = windows(cases, before), windows(cases, after)
    rows = []
    for case in cases:
        pick = choices.get(case.issue_id)
        if not pick:
            continue
        person = str(pick["first"])
        rows.append({
            "issue_id": case.issue_id,
            "issue_key": case.issue_key,
            "chose": person,
            "correct": person in set(case.truth),
            "in_window_before": person in first[case.issue_id],
            "removed": person in (first[case.issue_id] - second[case.issue_id]),
        })
    return rows


def scan(
    cases: Sequence[sweeps.CaseReplay],
    grid: Sequence[Mapping[str, float]],
    *,
    against: Mapping[str, float] | None = None,
    choices: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Ask a whole grid the propagation question, not one adopted point.

    Two ways a retune could change the full system's answer through *membership*: put a
    truth person in front of the model who was not there before, or take away the person
    the model wrongly chose. Both are set questions on a finite grid, so the honest
    answer is an enumeration and not an estimate.
    """
    against = current_weights() if against is None else dict(against)
    choices = baseline_first_choices() if choices is None else choices
    baseline_windows = windows(cases, against)
    truths = {case.issue_id: set(case.truth) for case in cases}
    wrong = {
        issue_id: str(pick["first"])
        for issue_id, pick in choices.items()
        if issue_id in truths and str(pick["first"]) not in truths[issue_id]
    }
    right = {
        issue_id: str(pick["first"])
        for issue_id, pick in choices.items()
        if issue_id in truths and str(pick["first"]) in truths[issue_id]
    }
    best = {
        "vectors": 0,
        "vectors_with_truth_entering": 0,
        "max_truth_entering": 0,
        "max_wrong_choices_removed": 0,
        "max_right_choices_removed": 0,
        "best_window_recall": 0.0,
        "best_window_recall_vector": None,
        "baseline_window_recall": 0.0,
    }
    baseline_recall = sum(
        len(truths[case.issue_id] & baseline_windows[case.issue_id]) / len(truths[case.issue_id])
        for case in cases
        if truths[case.issue_id]
    ) / (len(cases) or 1)
    best["baseline_window_recall"] = round(baseline_recall, 4)
    for vector in grid:
        best["vectors"] += 1
        entering = wrong_removed = right_removed = 0
        recall = 0.0
        for case in cases:
            window = set(case.to_case_scores().window(dict(vector), window_width()))
            was = baseline_windows[case.issue_id]
            truth = truths[case.issue_id]
            entering += len(truth & (window - was))
            if truth:
                recall += len(truth & window) / len(truth)
            gone = was - window
            if case.issue_id in wrong and wrong[case.issue_id] in gone:
                wrong_removed += 1
            if case.issue_id in right and right[case.issue_id] in gone:
                right_removed += 1
        recall /= len(cases) or 1
        best["vectors_with_truth_entering"] += entering > 0
        best["max_truth_entering"] = max(best["max_truth_entering"], entering)
        best["max_wrong_choices_removed"] = max(best["max_wrong_choices_removed"], wrong_removed)
        best["max_right_choices_removed"] = max(best["max_right_choices_removed"], right_removed)
        if recall > best["best_window_recall"]:
            best["best_window_recall"] = round(recall, 4)
            best["best_window_recall_vector"] = dict(vector)
    return best


# ---------- gate 1 ----------

@dataclass(frozen=True)
class Gate:
    """A gate decided in code, so the report cannot claim what the numbers do not."""

    name: str
    passed: bool
    reasons: list[str]
    detail: dict[str, object]


def gate_one(tier0: Mapping[str, object]) -> Gate:
    """The order's gate to ``weights_val``: a real improvement AND a way for it to land.

    Both halves are required, and the second is the one the re-rank's order-robustness
    makes decisive: a retune the model is shown the same people under cannot change what
    the model answers, however much it improves the ranking the model replaces.
    """
    plateau = {row["metric"]: row for row in tier0["plateau"]}
    candidate = tier0["candidate_metrics"]
    current = tier0["current_metrics"]
    metric = str(tier0["target_metric"])
    totals = tier0["membership_totals"]
    neighbourhood = tier0["neighbourhood_scan"]
    simplex = tier0["simplex_scan"]
    floors = dict(tier0["floors"])

    delta = round(float(candidate[metric]) - float(current[metric]), 4)
    floor = floors.get(metric)
    row = plateau.get(metric, {})
    plateau_holds = bool(row) and row["worse"] == 0 and row["beats"] >= row["points"] / 2
    improves = delta > 0 and (floor is None or delta > float(floor))

    reasons = [
        f"deterministic {sweeps.ALL_LABELS.get(metric, metric)} "
        f"{float(current[metric]):.4f} → {float(candidate[metric]):.4f} ({delta:+.4f}) "
        f"against the measured v4 floor of "
        + ("—" if floor is None else f"{float(floor):.4f}")
        + (" — clears it" if improves else " — does not clear it"),
        f"the plateau {'holds' if plateau_holds else 'does not hold'}: of "
        f"{row.get('points', 0)} neighbouring vectors, {row.get('beats', 0)} beat the "
        f"current weighting on {metric}, {row.get('ties', 0)} tie and "
        f"{row.get('worse', 0)} are worse (worst {row.get('min', 0):.4f})",
        f"the window population moves on {totals['cases_changed']}/{totals['cases']} "
        f"cases ({totals['entered']} in, {totals['left']} out) — but "
        f"{totals['truth_entered']} truth people enter it and {totals['truth_left']} "
        "leave",
        f"across all {neighbourhood['vectors']} vectors in the mechanism direction, "
        f"{neighbourhood['max_truth_entering']} truth people can be moved into the "
        f"window and {neighbourhood['max_wrong_choices_removed']} of the baseline "
        "re-rank's wrong rank-1 choices can be removed from it",
        f"across all {simplex['vectors']} vectors of the whole simplex the same two "
        f"maxima are {simplex['max_truth_entering']} and "
        f"{simplex['max_wrong_choices_removed']}; the best window recall any weighting "
        f"reaches is {simplex['best_window_recall']:.4f} against "
        f"{simplex['baseline_window_recall']:.4f} now",
    ]
    propagation = bool(
        totals["truth_entered"] > 0 or neighbourhood["max_wrong_choices_removed"] > 0
    )
    return Gate(
        name="gate 1 → weights_val",
        passed=bool(improves and plateau_holds and propagation),
        reasons=reasons,
        detail={
            "target_metric": metric,
            "delta": delta,
            "floor": floor,
            "improves": improves,
            "plateau_holds": plateau_holds,
            "propagation_possible": propagation,
        },
    )


# ---------- tier 0 ----------

def run_tier0() -> dict[str, object]:
    """Offline ($0, no model call, no graph): the whole selection, written to a checkpoint."""
    _require_validation()
    cases = source_cases()
    current = current_weights()
    chosen = select_candidate(cases)
    candidate = chosen["weights"]
    plateau_grid = grid_of(levels("plateau"))
    neighbourhood = grid_of(levels("neighbourhood"))
    choices = baseline_first_choices()
    rows = membership_rows(cases, current, candidate)
    tier0: dict[str, object] = {
        "study": STUDY,
        "split": _require_validation(),
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_condition": str(study("source_condition", "base")),
        "substrate": substrate(),
        "cases": len(cases),
        "roles": sum(len(case.roles) for case in cases),
        "candidates": sum(len(role.parts) for case in cases for role in case.roles),
        "window_width": window_width(),
        "target_metric": target_metric(),
        "current_weights": current,
        "candidate_weights": candidate,
        "selection": {
            "moved_from": chosen["moved_from"],
            "moved_to": chosen["moved_to"],
            "step": chosen["step"],
            "note": chosen["note"],
            "directions": chosen["directions"],
            "considered": chosen.get("considered", []),
        },
        "marginals": marginal_rows(cases),
        "current_metrics": aggregate(cases, current),
        "candidate_metrics": aggregate(cases, candidate),
        "paired": sweeps.paired_rows(per_case(cases, current), per_case(cases, candidate)),
        "plateau": plateau_rows(cases, plateau_grid),
        "plateau_points": len(plateau_grid),
        "membership": rows,
        "membership_totals": membership_totals(rows),
        "truth_totals": truth_totals(cases, current),
        "order_change": order_change(cases, current, candidate),
        "truth_outside_window": truth_outside_window(cases, current),
        "choices": choice_rows(cases, current, candidate, choices),
        "neighbourhood_scan": scan(cases, neighbourhood, choices=choices),
        "simplex_scan": scan(cases, simplex_grid(), choices=choices),
        "floors": {
            metric: sweeps.measured_floor(metric)
            for metric in ("hit_at_1", "hit_at_5", "recall_at_5", "mrr")
        },
        "role_pools": role_pool_summary(cases),
    }
    tier0["gate_1"] = _gate_json(gate_one(tier0))
    tier0_path().parent.mkdir(parents=True, exist_ok=True)
    tier0_path().write_text(
        json.dumps(tier0, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return tier0


def role_pool_summary(cases: Sequence[sweeps.CaseReplay]) -> dict[str, object]:
    """How often the window is a real constraint: pools larger than it, and by how much."""
    sizes = [len(role.parts) for case in cases for role in case.roles]
    top_k = window_width()
    over = [size for size in sizes if size > top_k]
    return {
        "roles": len(sizes),
        "roles_over_window": len(over),
        "roles_within_window": len(sizes) - len(over),
        "mean_pool": round(sum(sizes) / len(sizes), 1) if sizes else 0.0,
        "max_pool": max(sizes, default=0),
    }


def namespace_mtimes() -> list[dict[str, object]]:
    """When each checkpoint namespace was last written. Read at report time, not stored.

    The round writes one namespace and reads several. This is the cheap check that says
    so out of the filesystem rather than out of a promise.
    """
    base = DATA_DIR / "eval"
    rows = []
    for name in ("v1", "v2", "v3", "v4", "rerank_redesign", "sweeps",
                 str(study("root_subdir", "weights"))):
        path = base / name
        if not path.exists():
            rows.append({"namespace": name, "present": False, "newest": None})
            continue
        newest = max(
            (item.stat().st_mtime for item in path.rglob("*") if item.is_file()),
            default=0.0,
        )
        rows.append({
            "namespace": name,
            "present": True,
            "newest": datetime.fromtimestamp(newest, UTC).isoformat(timespec="seconds"),
        })
    return rows


def _gate_json(gate: Gate) -> dict[str, object]:
    return {
        "name": gate.name,
        "passed": gate.passed,
        "reasons": gate.reasons,
        "detail": gate.detail,
    }


def load_tier0() -> dict[str, object]:
    if not tier0_path().exists():
        raise WeightsRoundError(
            f"no tier-0 checkpoint at {tier0_path()}; run "
            "`uv run python -m capgraph.eval.weights_round --tier0` first (it is free)"
        )
    return json.loads(tier0_path().read_text(encoding="utf-8"))


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The final weights round (tier 0 is free)")
    parser.add_argument("--tier0", action="store_true",
                        help="offline: sweep, select, and evaluate gate 1")
    parser.add_argument("--gate", action="store_true", help="offline: print gate 1 from the checkpoint")
    parser.add_argument("--report", action="store_true",
                        help="offline: write docs/weights-round-report.md")
    parser.add_argument("--spend", action="store_true", help="offline: logged spend by stage")
    args = parser.parse_args(argv)
    did = False

    if args.tier0:
        did = True
        tier0 = run_tier0()
        print(json.dumps({
            "candidate_weights": tier0["candidate_weights"],
            "current_metrics": tier0["current_metrics"],
            "candidate_metrics": tier0["candidate_metrics"],
            "membership_totals": tier0["membership_totals"],
            "gate_1": tier0["gate_1"],
        }, indent=2, sort_keys=True))

    if args.gate:
        did = True
        print(json.dumps(load_tier0()["gate_1"], indent=2, sort_keys=True))

    if args.report:
        did = True
        from .report_weights_round import build_report

        path = Path("docs/weights-round-report.md")
        path.write_text(build_report(), encoding="utf-8")
        print(f"wrote {path}")

    if args.spend:
        did = True
        for name, calls, cost in spend_by_stage(stages()):
            print(f"{name}: {calls} calls, ${cost:.4f}")
        print(f"total: ${spend():.4f} of ${ceiling():.2f}")

    if not did:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
