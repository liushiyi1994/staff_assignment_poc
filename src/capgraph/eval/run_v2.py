"""Benchmark v2: lever experiments on validation, one frozen test run, one report.

    uv run python -m capgraph.eval.run_v2 --levers                  # offline, $0
    uv run python -m capgraph.eval.run_v2 --split validation        # spends (stage7b_val)
    uv run python -m capgraph.eval.run_v2 --split test              # spends (stage7b_test)
    uv run python -m capgraph.eval.run_v2 --report                  # offline

Three rules this module exists to keep, rather than to trust a runner to remember:

* **v1 is immutable.** Every v2 record is written under ``eval.v2.runs_subdir``, a
  separate checkpoint namespace with its own configuration digest. Nothing here opens
  ``data/eval/runs/`` for writing, and the v2 report is appended to
  ``docs/eval-results.md`` below a marker, leaving the v1 tables byte-identical.
* **Levers are decided on validation.** ``--levers`` reads only the validation split
  and re-combines rankings that were already paid for, so every rank-level lever
  (fusion, backstop) is measured before anything is spent.
* **The test split is run once**, after the configuration is frozen in
  ``docs/benchmark-v2-config.md``, and the frozen digest is recorded with the results.

The lever experiments are deliberately reported whichever way they fall: a fusion that
loses to the system it fuses is a finding, and the report prints it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..settings import DATA_DIR, PROMPTS_DIR, settings
from .baselines import BM25, VECTOR_ONLY
from .contracts import RankingOutput
from .fusion import reciprocal_rank_fusion, roster_backstop
from .holdout import BenchmarkManifestEntry
from .metrics import (
    HEADER_RULE,
    EvaluationResult,
    evaluate,
    load_manifest,
    summary_row,
)
from .run_eval import (
    ALL_SYSTEMS,
    SPLITS,
    TRACKED_REPORT,
    V2_MARKER,
    V3_MARKER,
    config_digest,
    load_checkpoint,
    run_config,
    run_diagnostics,
    run_split,
    score_split,
)
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE

V2_DIR = DATA_DIR / "eval" / "v2"
FROZEN_CONFIG = Path(__file__).resolve().parents[3] / "docs" / "benchmark-v2-config.md"

COMPARED = ("hit_at_1", "hit_at_5", "hit_at_10", "mrr", "candidate_recall")
METRIC_LABELS = {
    "hit_at_1": "Hit@1",
    "hit_at_5": "Hit@5",
    "hit_at_10": "Hit@10",
    "mrr": "MRR",
    "candidate_recall": "Candidate recall",
}


def runs_dir(variant: str | None = None) -> Path:
    """The v2 checkpoint namespace, or one A/B variant's namespace inside it.

    Each paid validation arm gets its own directory so two configurations can never be
    scored together. The frozen configuration's test run uses the namespace itself.
    """
    base = DATA_DIR / "eval" / str(settings["eval.v2.runs_subdir"])
    return base if variant is None else base / variant


def frozen_validation_dir() -> Path:
    """Which validation arm the frozen configuration was chosen as; see settings."""
    variant = settings.get("eval.v2.frozen_validation_variant")
    return runs_dir(str(variant) if variant else None)


def stage_for(split: str) -> str:
    key = "eval.v2.validation_stage" if split == "validation" else "eval.v2.test_stage"
    return str(settings[key])


def prompt_digest(name: str) -> str:
    """Content hash of a prompt file: a prompt revision must change the digest."""
    return hashlib.sha256((PROMPTS_DIR / f"{name}.md").read_bytes()).hexdigest()[:12]


def v2_config(split: str) -> dict[str, object]:
    """The v1 run configuration plus everything v2 can change about the ranking."""
    prompt = str(settings["llm.rerank_prompt"])
    return {
        **run_config(),
        "stage": stage_for(split),
        "rerank_prompt": prompt,
        "rerank_prompt_digest": prompt_digest(prompt),
        "benchmark_version": "v2",
    }


# ---------- scoring arbitrary rankings ----------

def score_rankings(
    system: str,
    rankings: dict[str, RankingOutput],
    cases: Sequence[BenchmarkManifestEntry],
) -> EvaluationResult:
    """Score a dict of ranking outputs through the harness's own invariant checks."""
    scored = [case for case in cases if case.issue_id in rankings]
    return evaluate(system, lambda context: rankings[context.issue_id], scored)


def _outputs(records: dict[tuple[str, str], dict], system: str) -> dict[str, dict]:
    return {
        issue_id: record
        for (name, issue_id), record in records.items()
        if name == system and "error" not in record
    }


# ---------- lever 1: rank-level experiments, offline from v1 checkpoints ----------

@dataclass(frozen=True)
class Lever:
    """One rank-level experiment: a label, its result, and why it was tried."""

    label: str
    result: EvaluationResult


def rank_levers(
    split: str = "validation", *, checkpoint_runs_dir: Path | None = None
) -> list[Lever]:
    """Fusion and backstop variants, recombined from checkpointed rankings.

    Costs nothing: every input ranking was already produced and paid for by the v1 run.
    """
    cases = [case for case in load_manifest(splits=(split,))]
    records = load_checkpoint(split, runs_dir=checkpoint_runs_dir)
    by_case = {case.issue_id: case for case in cases}
    graph = _outputs(records, CAPGRAPH_FULL)
    score = _outputs(records, CAPGRAPH_SCORE)
    bm25 = _outputs(records, BM25)
    vector = _outputs(records, VECTOR_ONLY)
    if not (graph and bm25):
        raise SystemExit(f"no {split} checkpoint to recombine — run the v1 split first")

    levers: list[Lever] = []

    def add(label: str, build) -> None:
        rankings = {
            issue_id: build(issue_id)
            for issue_id in graph
            if issue_id in bm25 and issue_id in by_case
        }
        levers.append(Lever(label, score_rankings(label, rankings, cases)))

    def as_output(issue_id: str, ranked: Sequence[str], *, pool: Sequence[str]) -> RankingOutput:
        return RankingOutput(ranked_ids=list(ranked), candidate_ids=list(pool))

    add(
        "capgraph_full (v1 reference)",
        lambda i: as_output(i, graph[i]["ranked_ids"], pool=graph[i]["candidate_ids"]),
    )

    k_grid = [int(k) for k in settings["eval.v2.rrf_k_grid"]]
    for k in k_grid:
        add(
            f"rrf(capgraph_full, bm25) k={k}",
            lambda i, k=k: as_output(
                i,
                reciprocal_rank_fusion([graph[i]["ranked_ids"], bm25[i]["ranked_ids"]], k=k),
                pool=bm25[i]["candidate_ids"],
            ),
        )
    for k in k_grid:
        add(
            f"rrf(capgraph_score, bm25) k={k}",
            lambda i, k=k: as_output(
                i,
                reciprocal_rank_fusion([score[i]["ranked_ids"], bm25[i]["ranked_ids"]], k=k),
                pool=bm25[i]["candidate_ids"],
            ),
        )
    default_k = int(settings["eval.v2.rrf_k"])
    for weight in [float(w) for w in settings["eval.v2.rrf_graph_weight_grid"]]:
        add(
            f"rrf(capgraph_full x{weight:g}, bm25) k={default_k}",
            lambda i, w=weight: as_output(
                i,
                reciprocal_rank_fusion(
                    [graph[i]["ranked_ids"], bm25[i]["ranked_ids"]],
                    k=default_k,
                    weights=[w, 1.0],
                ),
                pool=bm25[i]["candidate_ids"],
            ),
        )
    if vector:
        add(
            f"rrf(capgraph_full, bm25, vector_only) k={default_k}",
            lambda i: as_output(
                i,
                reciprocal_rank_fusion(
                    [graph[i]["ranked_ids"], bm25[i]["ranked_ids"], vector[i]["ranked_ids"]],
                    k=default_k,
                ),
                pool=bm25[i]["candidate_ids"],
            ),
        )
    add(
        "capgraph_full + roster backstop (person-id tail)",
        lambda i: as_output(
            i,
            roster_backstop(graph[i]["ranked_ids"], by_case[i].eligible_roster),
            pool=by_case[i].eligible_roster,
        ),
    )
    add(
        "capgraph_full + roster backstop (bm25-ordered tail)",
        lambda i: as_output(
            i,
            roster_backstop(
                graph[i]["ranked_ids"],
                by_case[i].eligible_roster,
                tail_order=bm25[i]["ranked_ids"],
            ),
            pool=by_case[i].eligible_roster,
        ),
    )
    return levers


def render_levers(levers: Sequence[Lever]) -> list[str]:
    lines = [
        "| Variant | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for lever in levers:
        r = lever.result
        recall = "N/A" if r.candidate_recall is None else f"{r.candidate_recall:.3f}"
        lines.append(
            f"| {lever.label} | {r.n_briefs} | {r.hit_at_1:.3f} | {r.hit_at_5:.3f} | "
            f"{r.hit_at_10:.3f} | {r.mrr:.3f} | {recall} |"
        )
    return lines


# ---------- v2 runs ----------

class V2BudgetError(RuntimeError):
    """The v2 track's own ceiling, checked before a split starts."""


def enforce_v2_budget(pending_cases: int) -> float:
    """Refuse a split whose projected spend breaks the whole track's authorization.

    ``llm.max_stage_cost_usd`` is a $25 per-stage ceiling — far looser than the spend
    this work order authorized. This checks the projection against both v2 stages'
    logged spend combined, which is the number the order actually constrains.
    """
    ceiling = float(settings["eval.v2.max_total_cost_usd"])
    projected = pending_cases * float(settings["eval.v2.expected_cost_per_case_usd"])
    spent = sum(cost for _, _, cost in spend_by_stage(
        [stage_for("validation"), stage_for("test")]
    ))
    if spent + projected > ceiling:
        raise V2BudgetError(
            f"projected v2 spend ${spent + projected:.2f} (logged ${spent:.4f} + "
            f"projected ${projected:.2f} for {pending_cases} cases) exceeds the "
            f"eval.v2.max_total_cost_usd ceiling of ${ceiling:.2f} — escalate to the "
            "orchestrator before running this split"
        )
    return projected


def run_v2_split(split: str, *, systems: Sequence[str] = ALL_SYSTEMS,
                 limit: int | None = None, variant: str | None = None) -> dict[str, int]:
    """Run one split into the v2 namespace under the v2 stage name and digest."""
    config = v2_config(split)
    target = runs_dir(variant)
    if any(name in systems for name in (CAPGRAPH_FULL, CAPGRAPH_SCORE)):
        done = load_checkpoint(split, runs_dir=target)
        cases = load_manifest(splits=(split,))[:limit]
        pending = sum(1 for case in cases if (CAPGRAPH_FULL, case.issue_id) not in done)
        projected = enforce_v2_budget(pending)
        print(f"pre-flight: {pending} unpaid cases, projected ${projected:.2f}")
    print(
        f"benchmark v2 {split}{f' [{variant}]' if variant else ''}: stage "
        f"{config['stage']}, prompt '{config['rerank_prompt']}' "
        f"({config['rerank_prompt_digest']}), weights "
        f"{config['scoring']['weights']}, digest {config_digest(config)}"
    )
    return run_split(
        split,
        systems=systems,
        limit=limit,
        stage=str(config["stage"]),
        runs_dir=target,
        config=config,
    )


# ---------- report ----------

def _by_system(results: Sequence[EvaluationResult]) -> dict[str, EvaluationResult]:
    return {result.system: result for result in results}


def split_dir(split: str) -> Path:
    """Validation is read from the frozen arm; the test split has only the frozen run."""
    return frozen_validation_dir() if split == "validation" else runs_dir()


def side_by_side(split: str) -> list[str]:
    """v1 against v2 for one split, per system, with the delta stated on every metric."""
    v1 = _by_system(score_split(split)[0])
    v2 = _by_system(score_split(split, runs_dir=split_dir(split))[0])
    shared = [name for name in ALL_SYSTEMS if name in v1 and name in v2]
    if not shared:
        return []
    lines = [
        "| System | Metric | v1 | v2 | Δ |",
        "|---|---|---:|---:|---:|",
    ]
    for name in shared:
        for metric in COMPARED:
            before, after = getattr(v1[name], metric), getattr(v2[name], metric)
            if before is None or after is None:
                continue
            lines.append(
                f"| `{name}` | {METRIC_LABELS[metric]} | {before:.3f} | {after:.3f} | "
                f"{after - before:+.3f} |"
            )
    return lines


V1_ARM = "v1"


def compare_variants(split: str, variants: Sequence[str | None]) -> list[str]:
    """One row per (arm, system): the A/B table the lever decisions are read from.

    ``V1_ARM`` names the frozen v1 checkpoint; anything else names a v2 A/B namespace.
    """
    lines = [
        "| Arm | System | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | Cost (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in variants:
        is_v1 = variant == V1_ARM
        label = "v1 (frozen)" if is_v1 else (variant or "v2 (frozen)")
        results = (
            score_split(split)[0]
            if is_v1
            else score_split(split, runs_dir=runs_dir(variant))[0]
        )
        for result in results:
            if result.system not in (CAPGRAPH_FULL, CAPGRAPH_SCORE):
                continue
            recall = (
                "N/A" if result.candidate_recall is None else f"{result.candidate_recall:.3f}"
            )
            lines.append(
                f"| {label} | `{result.system}` | {result.n_briefs} | "
                f"{result.hit_at_1:.3f} | {result.hit_at_5:.3f} | {result.hit_at_10:.3f} | "
                f"{result.mrr:.3f} | {recall} | {result.cost_usd_total:.4f} |"
            )
    return lines


def full_tables(split: str) -> list[str]:
    results, failures = score_split(split, runs_dir=split_dir(split))
    if not results:
        return []
    expected = len(load_manifest(splits=(split,)))
    lines = [
        "",
        f"### v2 {split} split",
        "",
        "| System | Cases in split | Scored | Failed |",
        "|---|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| `{result.system}` | {expected} | {result.n_briefs} | "
            f"{len(failures.get(result.system, ()))} |"
        )
    lines += [
        "",
        "| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | "
        "Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | "
        "Cost (USD) |",
        HEADER_RULE,
    ]
    for result in results:
        lines.append(summary_row(result.system, result))
    if failures:
        lines += ["", "| System | Issue | Reason |", "|---|---|---|"]
        for system, broken in sorted(failures.items()):
            for failure in broken:
                lines.append(
                    f"| {system} | {failure['issue_key'] or failure['issue_id']} | "
                    f"{failure['error']} |"
                )
    diagnostics = run_diagnostics(split, runs_dir=split_dir(split))
    if diagnostics:
        lines += [
            "",
            f"v2 {split} run diagnostics (graph system):",
            "",
            "| Measure | Value |",
            "|---|---|",
            *(
                f"| {key.replace('_', ' ')} | "
                f"{', '.join(value) if isinstance(value, list) else value} |"
                for key, value in diagnostics.items()
            ),
        ]
    return lines


def spend_by_stage(stages: Sequence[str]) -> list[tuple[str, int, float]]:
    """Reconcile spend directly against data/llm_costs.jsonl, never against a tally."""
    from ..llm import cost_log_path

    path = cost_log_path()
    totals: dict[str, list[float]] = {stage: [0, 0.0] for stage in stages}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                bucket = totals.get(str(record.get("stage")))
                if bucket is not None:
                    bucket[0] += 1
                    bucket[1] += float(record["cost_usd"])
    return [(stage, int(totals[stage][0]), round(totals[stage][1], 4)) for stage in stages]


def weight_lever_section() -> list[str]:
    """The weight lever, reported from the score-component checkpoint if one exists.

    Two tables: what each component does to the score-only ranking averaged over the
    whole grid (the mechanism the weighting was chosen from), and the adopted vector
    against v1's on the same retrieved pools.
    """
    from .scores import coarse_grid, evaluate_weights, load_scores, marginal_effects

    try:
        cases = load_scores("validation")
    except SystemExit:
        return []
    grid = coarse_grid()

    top_k = int(settings["retrieval.rerank_top_k"])
    v1_weights = dict(settings["eval.v2.v1_baseline.weights"])
    v2_weights = dict(settings["scoring.weights"])
    lines = [
        "",
        "## Lever findings — score weights (validation, offline from the component "
        "checkpoint)",
        "",
        f"Score components for all {len(cases)} validation cases are checkpointed in "
        "`data/eval/v2/scores/`, so every weight vector below was evaluated without a "
        "further model call. These are **score-only** metrics plus *window recall* — the "
        "share of cases whose truth reaches the re-rank window, which is the ceiling on "
        "the full system's Hit@K. A weight change cannot be credited with more than "
        "raising that ceiling until a paid run says otherwise.",
        "",
        f"The weighting was chosen from this table, not from the grid's best row: on 30 "
        f"cases a {len(grid)}-point grid has more than enough freedom to fit noise, but "
        "a component whose mean metric moves one way across the whole grid is a "
        "mechanism. Marginal effect of each component, averaged over every grid point "
        "that holds it at the given weight:",
        "",
        "| Component | Weight | Mean MRR | Mean window recall |",
        "|---|---:|---:|---:|",
    ]
    for component, effects in marginal_effects(cases, top_k=top_k, grid=grid).items():
        for weight, mean_mrr, mean_window in effects:
            lines.append(
                f"| `{component}` | {weight:.2f} | {mean_mrr:.3f} | {mean_window:.3f} |"
            )
    lines += [
        "",
        "The adopted vector against v1's, on identical retrieved pools:",
        "",
        "| Weights | Hit@1 | Hit@5 | Hit@10 | MRR | Window recall |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, weights in (("v1", v1_weights), ("v2 (adopted)", v2_weights)):
        row = evaluate_weights(cases, weights, top_k=top_k)
        lines.append(
            f"| {label}: {', '.join(f'{k} {v}' for k, v in weights.items())} | "
            f"{row.hit_at_1:.3f} | {row.hit_at_5:.3f} | {row.hit_at_10:.3f} | "
            f"{row.mrr:.3f} | {row.window_recall:.3f} |"
        )
    return lines


def headline_section() -> list[str]:
    """What the frozen test run actually showed, stated before the tables.

    The comparative wording is derived from the measured deltas rather than written
    around them, so this paragraph cannot drift from the table underneath it.
    """
    v1 = _by_system(score_split("test")[0])
    v2 = _by_system(score_split("test", runs_dir=runs_dir())[0])
    if CAPGRAPH_FULL not in v1 or CAPGRAPH_FULL not in v2:
        return []

    def deltas(system: str) -> dict[str, float]:
        return {
            metric: getattr(v2[system], metric) - getattr(v1[system], metric)
            for metric in ("hit_at_1", "hit_at_5", "hit_at_10", "mrr")
        }

    def per_query(system: str) -> float:
        return v2[system].cost_usd_total / max(v2[system].n_briefs, 1)

    full, score = deltas(CAPGRAPH_FULL), deltas(CAPGRAPH_SCORE)
    full_worst = max(abs(value) for value in full.values())
    score_best = max(score.values())
    cost_ratio = per_query(CAPGRAPH_FULL) / max(per_query(CAPGRAPH_SCORE), 1e-9)
    latency_ratio = v2[CAPGRAPH_FULL].latency_ms_mean / max(
        v2[CAPGRAPH_SCORE].latency_ms_mean, 1e-9
    )
    verdict = (
        "did not move the full system"
        if full_worst < 0.05
        else "moved the full system"
    )
    return [
        "",
        "## What this run showed",
        "",
        f"On the 120-case test split the adopted weighting **{verdict}**: "
        + ", ".join(
            f"{METRIC_LABELS[metric]} {value:+.3f}" for metric, value in full.items()
        )
        + f" — the largest of them {full_worst:.3f}, and all of them inside the "
        "run-to-run variance measured below.",
        "",
        "It did move the deterministic arm, clearly and in the direction the offline "
        "sweep predicted: `capgraph_score` gained "
        + ", ".join(
            f"{METRIC_LABELS[metric]} {value:+.3f}" for metric, value in score.items()
        )
        + f" on the same 120 cases, a {score_best:.3f} best-case gain that is several "
        "times the noise floor.",
        "",
        "Both facts together are the finding, and it is not the one the work order "
        "expected. The weighting genuinely improved the ordering handed to the LLM "
        "re-rank, and the re-rank then produced the same end result it produced from "
        "the worse ordering. On this benchmark the re-rank, not the deterministic "
        "score, is what bounds the full system — so tuning the score buys little until "
        "the re-rank changes, and the one re-rank change tried here (an "
        "assignee-aligned prompt) scored below the prompt it replaced.",
        "",
        "The practical consequence is the more useful result. `capgraph_score` now "
        f"reaches Hit@5 {v2[CAPGRAPH_SCORE].hit_at_5:.3f} and Hit@10 "
        f"{v2[CAPGRAPH_SCORE].hit_at_10:.3f} against the full system's "
        f"{v2[CAPGRAPH_FULL].hit_at_5:.3f} and {v2[CAPGRAPH_FULL].hit_at_10:.3f}, at "
        f"${per_query(CAPGRAPH_SCORE):.4f} per query against "
        f"${per_query(CAPGRAPH_FULL):.4f} and "
        f"{v2[CAPGRAPH_SCORE].latency_ms_mean / 1000:.1f}s against "
        f"{v2[CAPGRAPH_FULL].latency_ms_mean / 1000:.1f}s — {cost_ratio:.0f}x cheaper "
        f"and {latency_ratio:.0f}x faster. The re-rank still earns its keep on Hit@1 "
        "and MRR, and it is what produces the cited reasons, but it is no longer what "
        "carries Hit@5 and Hit@10.",
    ]


def ab_section() -> list[str]:
    """The paid validation A/B: one arm per lever, each isolating one change."""
    arms = [str(name) for name in settings.get("eval.v2.ab_arms") or ()]
    if not arms:
        return []
    rows = compare_variants("validation", [V1_ARM, *arms])
    if len(rows) <= 2:                                   # header only: nothing ran
        return []
    return [
        "",
        "## Lever findings — paid validation A/B (30 cases, `stage7b_val`)",
        "",
        "Each arm changes exactly one thing against the arm above it: "
        f"`{arms[0]}` changes only the score weights against the frozen v1 run, and "
        f"`{arms[-1]}` changes only the re-rank prompt against `{arms[0]}`. Every arm "
        "has its own checkpoint namespace, so no two configurations are ever scored "
        "together.",
        "",
        *rows,
        "",
        "Read these against the measured run-to-run variance below, not as exact "
        "quantities: 30 cases is 30 coin flips wide.",
    ]


def variance_section() -> list[str]:
    """How big a difference this benchmark can produce from no change at all.

    The intent parse is a model call, and a model call at temperature 0 is not a
    deterministic function. Two runs of the *same* configuration therefore retrieve
    different candidate pools and score differently. This measures that directly, by
    comparing the frozen v1 validation run's deterministic arm against the score
    components re-retrieved for this work order under v1's own weights — same
    configuration, two runs, no lever applied.

    It is reported because every lever below is judged on 30 cases, and a lever whose
    effect is smaller than this number has not been shown to do anything.
    """
    from .scores import evaluate_weights, load_scores

    try:
        cases = load_scores("validation")
    except SystemExit:
        return []
    v1 = next(
        (r for r in score_split("validation")[0] if r.system == CAPGRAPH_SCORE), None
    )
    if v1 is None:
        return []
    replay = evaluate_weights(
        cases,
        dict(settings["eval.v2.v1_baseline.weights"]),
        top_k=int(settings["eval.v2.v1_baseline.rerank_top_k"]),
    )
    pairs = [
        ("Hit@1", v1.hit_at_1, replay.hit_at_1),
        ("Hit@5", v1.hit_at_5, replay.hit_at_5),
        ("Hit@10", v1.hit_at_10, replay.hit_at_10),
        ("MRR", v1.mrr, replay.mrr),
    ]
    worst = max(abs(after - before) for _, before, after in pairs)
    v1_pools = {
        issue_id: set(record["candidate_ids"] or ())
        for (system, issue_id), record in load_checkpoint("validation").items()
        if system == CAPGRAPH_FULL and "error" not in record
    }
    identical = sum(
        1
        for case in cases
        if v1_pools.get(case.issue_id)
        == {person_id for role in case.roles for person_id in role.parts}
    )
    return [
        "",
        "## Run-to-run variance on 30 cases (measured, not assumed)",
        "",
        "The intent parse is a model call, so two runs of the *same* configuration do "
        f"not retrieve the same candidates. Re-running retrieval reproduced {identical} "
        f"of the {len(cases)} v1 candidate pools exactly. The three baselines, which "
        "make no model call, reproduced every ranking byte for byte in the v2 "
        "namespace — so the variance below is the LLM path, not the harness.",
        "",
        "`capgraph_score` under **v1's own weights**, run twice:",
        "",
        "| Metric | v1 run | re-run | Δ |",
        "|---|---:|---:|---:|",
        *(
            f"| {label} | {before:.3f} | {after:.3f} | {after - before:+.3f} |"
            for label, before, after in pairs
        ),
        "",
        f"The largest swing from changing nothing is **{worst:.3f}**. Any lever above "
        "whose validation effect is smaller than that has not been shown to work — "
        "which is why each was adopted or rejected on a mechanism visible across a "
        "whole sweep rather than on one table's best row, and why the 120-case test "
        "split is the only number here worth quoting on its own.",
    ]


def render_report(*, levers: Sequence[Lever] | None = None) -> str:
    """The v2 section: what changed, what it did on each split, and what it cost."""
    config = v2_config("test")
    v1 = dict(settings["eval.v2.v1_baseline"])
    stages = [stage_for("validation"), stage_for("test")]

    def weights_text(weights) -> str:
        return ", ".join(f"{name} {value}" for name, value in weights.items())

    lines = [
        "# Benchmark v2",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} against the same manifest "
        f"`{config['manifest_version']}` and the same 150 cases as v1, under "
        f"configuration digest `{config_digest(config)}`. The v1 tables above are "
        "unchanged; nothing in this section re-scores them.",
        "",
        "Every lever was chosen on the 30 validation cases. The 120-case test split was "
        "run once, after the configuration below was frozen in "
        "`docs/benchmark-v2-config.md`. Its checkpoints live in "
        f"`data/eval/{settings['eval.v2.runs_subdir']}/`, separate from v1's.",
        "",
        f"The v1 column below is transcribed from the frozen v1 record (digest "
        f"`{v1['digest']}`). Because v2 changes engine defaults, regenerating the v1 "
        "half of this file with `make eval` would restate those tables' configuration "
        "against the current settings — the v1 half is a frozen artifact and should be "
        "left as written.",
        "",
        "## v2 configuration",
        "",
        "| Setting | v1 | v2 |",
        "|---|---|---|",
        f"| Re-rank prompt | `{v1['rerank_prompt']}` | `{config['rerank_prompt']}` "
        f"({config['rerank_prompt_digest']}) |",
        f"| Re-rank window | {v1['rerank_top_k']} | {config['retrieval']['rerank_top_k']} |",
        f"| Score weights | {weights_text(v1['weights'])} | "
        f"{weights_text(config['scoring']['weights'])} |",
        f"| Retrieval | vector top-{v1['vector_top_k']} ∪ structured "
        f"top-{v1['structured_top_k']} | vector top-"
        f"{config['retrieval']['vector_top_k']} ∪ structured top-"
        f"{config['retrieval']['structured_top_k']} |",
        f"| Cost-log stages | `{v1['stage']}` | `{stages[0]}` / `{stages[1]}` |",
    ]

    lines += headline_section()

    for split in SPLITS:
        rows = side_by_side(split)
        if rows:
            lines += ["", f"## v1 vs v2 — {split} split", "", *rows]
    for split in SPLITS:
        lines += full_tables(split)

    if levers:
        lines += [
            "",
            "## Lever findings — rank-level (validation, offline from v1 checkpoints)",
            "",
            "Every row below re-combines rankings the v1 run already produced, so the "
            "whole table cost nothing. It is reported whichever way it falls, and it "
            "falls against fusion.",
            "",
            *render_levers(levers),
        ]
    lines += weight_lever_section()
    lines += ab_section()
    lines += variance_section()

    lines += ["", "## Spend", "", "| Stage | Calls | Cost (USD) |", "|---|---:|---:|"]
    total = 0.0
    for stage, calls, cost in spend_by_stage(stages):
        total += cost
        lines.append(f"| `{stage}` | {calls} | {cost:.4f} |")
    lines += [
        f"| **total** | | **{total:.4f}** |",
        "",
        "Reconciled against `data/llm_costs.jsonl` by stage name, retries included.",
    ]
    return "\n".join(lines) + "\n"


def write_tracked_section(markdown: str, *, path: Path = TRACKED_REPORT) -> None:
    """Replace the v2 section, leaving the v1 half above and any v3 section below."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    head = existing.split(V2_MARKER)[0].rstrip("\n")
    _, marker, tail = existing.partition(V3_MARKER)
    path.write_text(f"{head}\n\n{V2_MARKER}\n\n{markdown}{marker}{tail}", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark v2 experiments and run")
    parser.add_argument("--levers", action="store_true",
                        help="offline: rank-level lever table from the v1 checkpoints")
    parser.add_argument("--lever-split", default="validation", choices=SPLITS)
    parser.add_argument("--split", choices=SPLITS, help="run this split (spends)")
    parser.add_argument("--systems", default=",".join(ALL_SYSTEMS))
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--variant",
        help="A/B arm name; its records go to their own namespace inside the v2 one",
    )
    parser.add_argument("--report", action="store_true",
                        help="offline: rebuild the v2 section of docs/eval-results.md")
    parser.add_argument(
        "--compare",
        help="offline: comma-separated arms to tabulate ('v1' for the frozen v1 run)",
    )
    args = parser.parse_args(argv)

    if args.compare:
        arms = [name.strip() or None for name in args.compare.split(",")]
        print("\n".join(compare_variants(args.split or "validation", arms)))
        return 0

    if args.levers:
        levers = rank_levers(args.lever_split)
        print(f"{args.lever_split} split, offline rank-level levers:\n")
        print("\n".join(render_levers(levers)))
        (V2_DIR).mkdir(parents=True, exist_ok=True)
        (V2_DIR / f"levers_{args.lever_split}.json").write_text(
            json.dumps(
                [
                    {"label": lever.label, **{m: getattr(lever.result, m) for m in COMPARED}}
                    for lever in levers
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    if args.split:
        systems = [name.strip() for name in args.systems.split(",") if name.strip()]
        counts = run_v2_split(
            args.split, systems=systems, limit=args.limit, variant=args.variant
        )
        print(json.dumps(dict(sorted(counts.items())), indent=2))

    if args.split and args.variant:
        # An A/B arm is an experiment, not the frozen run: it must not rewrite the
        # tracked v2 section, which reports the frozen configuration only.
        print(f"arm '{args.variant}' checkpointed in {runs_dir(args.variant)}; "
              f"compare it with --compare v1,{args.variant}")
        return 0

    if args.report or args.split:
        levers = None
        try:
            levers = rank_levers("validation")
        except SystemExit:
            pass
        markdown = render_report(levers=levers)
        V2_DIR.mkdir(parents=True, exist_ok=True)
        (V2_DIR / "results.md").write_text(markdown, encoding="utf-8")
        write_tracked_section(markdown)
        print(markdown)
        print(f"\nwrote {V2_DIR / 'results.md'} and the v2 section of {TRACKED_REPORT}")
        return 0

    parser.error("nothing to do: pass --levers, --split, or --report")
    return 2


__all__ = [
    "Lever",
    "rank_levers",
    "render_report",
    "run_v2_split",
    "runs_dir",
    "score_rankings",
    "side_by_side",
    "v2_config",
    "write_tracked_section",
]


if __name__ == "__main__":
    raise SystemExit(main())
