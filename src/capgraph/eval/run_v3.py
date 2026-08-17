"""Benchmark v3: recall arm, compact cards, wider window, re-rank hardening.

    uv run python -m capgraph.eval.run_v3 --scores --split validation   # spends (intent only)
    uv run python -m capgraph.eval.run_v3 --pool-levers                 # offline, $0
    uv run python -m capgraph.eval.run_v3 --split validation --variant ab_cards   # spends
    uv run python -m capgraph.eval.run_v3 --split test                  # spends, once
    uv run python -m capgraph.eval.run_v3 --report                      # offline

The rules this module exists to keep are v2's, extended by one:

* **v1 and v2 are immutable.** Every v3 record is written under ``eval.v3.runs_subdir``
  with its own configuration digest, and the v3 section of ``docs/eval-results.md`` is
  written below its own marker, leaving both earlier halves byte-identical.
* **Levers are decided on validation.** ``--pool-levers`` measures the lexical arm and
  the window width from the score-component checkpoint, before any re-rank is paid for.
* **The test split is run once**, after the configuration is frozen in
  ``docs/benchmark-v3-config.md``, and it is the third and last exposure of this
  manifest's 120 test cases.
* **Effects are reported as paired per-query statistics**, not only as aggregates. The
  levers here are expected to move fewer cases than the 30-case noise floor can resolve,
  so how many cases moved and in which direction is the reportable quantity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..settings import DATA_DIR, PROMPTS_DIR, settings
from .labelnoise import (
    CHANGE_LOG_LABEL,
    SNAPSHOT_LABEL,
    load_label_audit,
    summarize,
)
from .metrics import (
    HEADER_RULE,
    EvaluationResult,
    hit_at_k,
    load_manifest,
    mrr,
    summary_row,
)
from .paired import paired_binary, paired_bootstrap, render_paired
from .run_eval import (
    ALL_SYSTEMS,
    SPLITS,
    TRACKED_REPORT,
    V3_MARKER,
    V4_MARKER,
    config_digest,
    load_checkpoint,
    run_config,
    run_diagnostics,
    run_split,
    score_split,
)
from .systems import CAPGRAPH_FULL, CAPGRAPH_SCORE

V3_DIR = DATA_DIR / "eval" / "v3"
FROZEN_CONFIG = Path(__file__).resolve().parents[3] / "docs" / "benchmark-v3-config.md"

ARM_LEXICAL = "lexical"

COMPARED = ("hit_at_1", "hit_at_5", "hit_at_10", "mrr", "candidate_recall")
METRIC_LABELS = {
    "hit_at_1": "Hit@1",
    "hit_at_5": "Hit@5",
    "hit_at_10": "Hit@10",
    "mrr": "MRR",
    "candidate_recall": "Candidate recall",
}


# ---------- namespaces and configuration ----------

def runs_dir(variant: str | None = None) -> Path:
    """The v3 checkpoint namespace, or one A/B arm's namespace inside it."""
    base = DATA_DIR / "eval" / str(settings["eval.v3.runs_subdir"])
    return base if variant is None else base / variant


def scores_dir() -> str:
    return str(settings["eval.v3.scores_subdir"])


def frozen_validation_dir() -> Path:
    variant = settings.get("eval.v3.frozen_validation_variant")
    return runs_dir(str(variant) if variant else None)


def stage_for(split: str) -> str:
    key = "eval.v3.validation_stage" if split == "validation" else "eval.v3.test_stage"
    return str(settings[key])


def v3_stages() -> list[str]:
    return [stage_for("validation"), stage_for("test")]


def prompt_digest(name: str) -> str:
    """Content hash of a prompt file: a prompt revision must change the digest."""
    return hashlib.sha256((PROMPTS_DIR / f"{name}.md").read_bytes()).hexdigest()[:12]


def v3_config(split: str) -> dict[str, object]:
    """The run configuration plus everything v3 can change about the ranking."""
    prompt = str(settings["llm.rerank_prompt"])
    config: dict[str, object] = {
        **run_config(),
        "stage": stage_for(split),
        "rerank_prompt": prompt,
        "rerank_prompt_digest": prompt_digest(prompt),
        "benchmark_version": "v3",
    }
    if int(settings["retrieval.finisher_top_k"]) > 0:
        finisher = str(settings["llm.finisher_prompt"])
        config["finisher_model"] = str(settings["llm.finisher_model"])
        config["finisher_prompt"] = finisher
        config["finisher_prompt_digest"] = prompt_digest(finisher)
    return config


# ---------- spend control ----------

class V3BudgetError(RuntimeError):
    """The v3 track's own ceiling, checked before a split starts."""


def project_case_cost() -> float:
    """Projected spend for one case under the *current* configuration.

    Built from the configuration rather than from a single measured per-case constant,
    because a v3 arm can triple the number of re-rank calls or add a call on a model
    five times the price. Per-call figures come from ``eval.v3.projection`` and are
    measured and rounded up, so this over-projects rather than under-projects.
    """
    projection = dict(settings["eval.v3.projection"])
    roles = float(projection["roles_per_case"])
    view = str(settings["retrieval.rerank_candidate_view"])
    rerank = float(
        projection["rerank_card_call_usd" if view == "card" else "rerank_profile_call_usd"]
    )
    samples = int(settings["retrieval.rerank_samples"])
    finisher = (
        float(projection["finisher_call_usd"])
        if int(settings["retrieval.finisher_top_k"]) > 0
        else 0.0
    )
    return float(projection["intent_call_usd"]) + roles * (samples * rerank + finisher)


def enforce_v3_budget(pending_cases: int) -> float:
    """Refuse a split whose projected spend breaks the owner's $25 authorization.

    ``llm.max_stage_cost_usd`` is a per-stage ceiling, so it cannot see the two v3
    stages together. This checks the projection against the logged spend of both.
    """
    ceiling = float(settings["eval.v3.max_total_cost_usd"])
    projected = pending_cases * project_case_cost()
    spent = sum(cost for _, _, cost in spend_by_stage(v3_stages()))
    if spent + projected > ceiling:
        raise V3BudgetError(
            f"projected v3 spend ${spent + projected:.2f} (logged ${spent:.4f} + "
            f"projected ${projected:.2f} for {pending_cases} cases at "
            f"${project_case_cost():.4f}/case) exceeds the eval.v3.max_total_cost_usd "
            f"ceiling of ${ceiling:.2f} — escalate to the orchestrator before running "
            "this split"
        )
    return projected


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


def spend_by_purpose(stages: Sequence[str]) -> dict[str, tuple[int, float]]:
    """The same ledger split by call type, so an arm's cost driver is visible."""
    from ..llm import cost_log_path

    path = cost_log_path()
    totals: dict[str, list[float]] = {}
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if str(record.get("stage")) not in set(stages):
                continue
            bucket = totals.setdefault(str(record.get("purpose") or "unlabelled"), [0, 0.0])
            bucket[0] += 1
            bucket[1] += float(record["cost_usd"])
    return {name: (int(count), round(cost, 4)) for name, (count, cost) in sorted(totals.items())}


# ---------- offline lever analysis, from the score-component checkpoint ----------

@dataclass(frozen=True)
class PoolRow:
    """One pool configuration measured offline: what it reaches, and how wide it is."""

    label: str
    n: int
    candidate_recall: float
    window_recall: float
    median_pool: int
    median_window: int


def _window(case, weights: Mapping[str, float], top_k: int) -> set[str]:
    return set(case.window(dict(weights), top_k))


def pool_levers(
    split: str = "validation", *, widths: Sequence[int] = (15, 20, 32, 40)
) -> list[PoolRow]:
    """Candidate recall and window recall, with and without the lexical arm.

    Costs nothing beyond the one intent-parse dump: every row re-scores checkpointed
    components. Window recall is the ceiling on the full system's Hit@K — a candidate
    the re-rank is never shown cannot be ranked — so this is where levers 1 and 3 are
    decided, before a re-rank is paid for.
    """
    from .scores import load_scores, scores_path

    cases = load_scores(split, path=scores_path(split, subdir=scores_dir()))
    weights = dict(settings["scoring.weights"])
    rows: list[PoolRow] = []
    for arm_label, variants in (
        ("with lexical arm", [case for case in cases]),
        ("without lexical arm", [case.without_arm(ARM_LEXICAL) for case in cases]),
    ):
        pools = sorted(len(case.pool()) for case in variants)
        recall = sum(bool(case.truth & case.pool()) for case in variants) / len(variants)
        for width in widths:
            windows = [_window(case, weights, width) for case in variants]
            sizes = sorted(len(window) for window in windows)
            rows.append(
                PoolRow(
                    label=f"{arm_label}, window {width}",
                    n=len(variants),
                    candidate_recall=recall,
                    window_recall=sum(
                        bool(case.truth & window)
                        for case, window in zip(variants, windows, strict=True)
                    )
                    / len(variants),
                    median_pool=pools[len(pools) // 2],
                    median_window=sizes[len(sizes) // 2],
                )
            )
    return rows


def render_pool_levers(rows: Sequence[PoolRow]) -> list[str]:
    lines = [
        "| Pool | N | Candidate recall | Window recall | Median pool | Median window |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.label} | {row.n} | {row.candidate_recall:.3f} | "
            f"{row.window_recall:.3f} | {row.median_pool} | {row.median_window} |"
        )
    return lines


# ---------- runs ----------

def run_v3_split(
    split: str,
    *,
    systems: Sequence[str] = ALL_SYSTEMS,
    limit: int | None = None,
    variant: str | None = None,
) -> dict[str, int]:
    """Run one split into the v3 namespace under the v3 stage name and digest."""
    config = v3_config(split)
    target = runs_dir(variant)
    if any(name in systems for name in (CAPGRAPH_FULL, CAPGRAPH_SCORE)):
        done = load_checkpoint(split, runs_dir=target)
        cases = load_manifest(splits=(split,))[:limit]
        pending = sum(1 for case in cases if (CAPGRAPH_FULL, case.issue_id) not in done)
        projected = enforce_v3_budget(pending)
        print(
            f"pre-flight: {pending} unpaid cases at ${project_case_cost():.4f}/case, "
            f"projected ${projected:.2f}"
        )
    retrieval = config["retrieval"]
    print(
        f"benchmark v3 {split}{f' [{variant}]' if variant else ''}: stage "
        f"{config['stage']}, prompt '{config['rerank_prompt']}' "
        f"({config['rerank_prompt_digest']}), view "
        f"{retrieval['rerank_candidate_view']}, window {retrieval['rerank_top_k']}, "
        f"bm25 arm {retrieval['bm25_top_k']}, samples {retrieval['rerank_samples']}, "
        f"finisher {retrieval['finisher_top_k']}, digest {config_digest(config)}"
    )
    return run_split(
        split,
        systems=systems,
        limit=limit,
        stage=str(config["stage"]),
        runs_dir=target,
        config=config,
    )


# ---------- per-case metrics, for the paired statistics ----------

def per_case_metrics(
    split: str, system: str, *, runs_dir_: Path | None = None
) -> dict[str, dict[str, float]]:
    """Hit@1/5/10 and reciprocal rank per case, from a checkpointed run."""
    cases = {case.issue_id: case for case in load_manifest(splits=(split,))}
    records = load_checkpoint(split, runs_dir=runs_dir_)
    out: dict[str, dict[str, float]] = {}
    for (name, issue_id), record in records.items():
        if name != system or "error" in record or issue_id not in cases:
            continue
        truth = set(cases[issue_id].truth_person_ids)
        ranked = list(record["ranked_ids"])
        out[issue_id] = {
            "hit_at_1": hit_at_k(ranked, truth, 1),
            "hit_at_5": hit_at_k(ranked, truth, 5),
            "hit_at_10": hit_at_k(ranked, truth, 10),
            "mrr": mrr(ranked, truth),
        }
    return out


def paired_rows(
    baseline: Mapping[str, Mapping[str, float]],
    variant: Mapping[str, Mapping[str, float]],
) -> list[str]:
    """The paired table for one (baseline, variant) pair of checkpointed runs."""
    binary = [
        paired_binary(
            METRIC_LABELS[metric],
            {case: values[metric] for case, values in baseline.items()},
            {case: values[metric] for case, values in variant.items()},
        )
        for metric in ("hit_at_1", "hit_at_5", "hit_at_10")
    ]
    continuous = [
        paired_bootstrap(
            "MRR",
            {case: values["mrr"] for case, values in baseline.items()},
            {case: values["mrr"] for case, values in variant.items()},
        )
    ]
    return render_paired(binary, continuous)


# ---------- report ----------

def _by_system(results: Sequence[EvaluationResult]) -> dict[str, EvaluationResult]:
    return {result.system: result for result in results}


def split_dir(split: str) -> Path:
    """Validation is read from the frozen arm; the test split has only the frozen run."""
    return frozen_validation_dir() if split == "validation" else runs_dir()


def v2_dir(split: str) -> Path:
    """Where benchmark v2's frozen run for one split lives. Read-only, always."""
    base = DATA_DIR / "eval" / str(settings["eval.v2.runs_subdir"])
    if split == "validation":
        variant = settings.get("eval.v2.frozen_validation_variant")
        return base / str(variant) if variant else base
    return base


def side_by_side(split: str) -> list[str]:
    """v1, v2 and v3 for one split, per system, with v3's delta against v2."""
    v1 = _by_system(score_split(split)[0])
    v2 = _by_system(score_split(split, runs_dir=v2_dir(split))[0])
    v3 = _by_system(score_split(split, runs_dir=split_dir(split))[0])
    shared = [name for name in ALL_SYSTEMS if name in v1 and name in v2 and name in v3]
    if not shared:
        return []
    lines = ["| System | Metric | v1 | v2 | v3 | Δ v3−v2 |", "|---|---|---:|---:|---:|---:|"]
    for name in shared:
        for metric in COMPARED:
            before, middle, after = (
                getattr(v1[name], metric),
                getattr(v2[name], metric),
                getattr(v3[name], metric),
            )
            if before is None or middle is None or after is None:
                continue
            lines.append(
                f"| `{name}` | {METRIC_LABELS[metric]} | {before:.3f} | {middle:.3f} | "
                f"{after:.3f} | {after - middle:+.3f} |"
            )
    return lines


V2_ARM = "v2"


def arm_verdicts() -> dict[str, str]:
    """Which arms the frozen configuration kept, read off the run plan rather than typed.

    The arms are cumulative: each adds one lever to the one above it, so everything up
    to and including ``eval.v3.frozen_validation_variant`` is in the adopted
    configuration and everything after it was measured and left out.
    """
    arms = [str(name) for name in settings.get("eval.v3.ab_arms") or ()]
    frozen = str(settings.get("eval.v3.frozen_validation_variant") or "")
    cut = arms.index(frozen) if frozen in arms else len(arms) - 1
    return {
        arm: ("adopted" if index == cut else "in the adopted configuration")
        if index <= cut
        else "measured, not adopted"
        for index, arm in enumerate(arms)
    }


def compare_variants(split: str, variants: Sequence[str | None]) -> list[str]:
    """One row per (arm, system): the A/B table the lever decisions are read from."""
    verdicts = arm_verdicts()
    lines = [
        "| Arm | Verdict | System | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | "
        "Cost (USD) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in variants:
        is_v2 = variant == V2_ARM
        label = "v2 (frozen)" if is_v2 else (variant or "v3 (frozen)")
        verdict = "baseline" if is_v2 else verdicts.get(str(variant), "")
        results = score_split(
            split, runs_dir=v2_dir(split) if is_v2 else runs_dir(variant)
        )[0]
        for result in results:
            if result.system not in (CAPGRAPH_FULL, CAPGRAPH_SCORE):
                continue
            recall = (
                "N/A" if result.candidate_recall is None else f"{result.candidate_recall:.3f}"
            )
            lines.append(
                f"| {label} | {verdict} | `{result.system}` | {result.n_briefs} | "
                f"{result.hit_at_1:.3f} | {result.hit_at_5:.3f} | {result.hit_at_10:.3f} | "
                f"{result.mrr:.3f} | {recall} | {result.cost_usd_total:.4f} |"
            )
    return lines


def noise_gauge_section() -> list[str]:
    """How much an arm-to-arm delta moves when the lever provably cannot act.

    Every arm re-runs the pipeline, and the intent parse is a model call, so two arms
    never retrieve the same pools. The deterministic ``capgraph_score`` arm ranks the
    whole pool and never sees a prompt, a window, or a sample, so across the arms that
    change only those three things its movement is pure run-to-run variance. That makes
    it a noise gauge measured inside this study rather than imported from v2's.
    """
    arms = [str(name) for name in settings.get("eval.v3.ab_arms") or ()]
    frozen = str(settings.get("eval.v3.frozen_validation_variant") or "")
    cut = arms.index(frozen) if frozen in arms else len(arms) - 1
    # The run plan's comparisons: cumulative arms against the one above them, and each
    # rejected arm against the frozen one. Every lever in them is re-rank-side, and a
    # re-rank-side lever cannot reach the deterministic arm at all.
    pairs = [(arms[i - 1], arms[i]) for i in range(1, cut + 1)]
    pairs += [(frozen, arm) for arm in arms[cut + 1:]]

    metrics = ("hit_at_1", "hit_at_5", "hit_at_10", "mrr")
    rows: list[str] = []
    widest = 0.0
    for before, after in pairs:
        baseline = per_case_metrics("validation", CAPGRAPH_SCORE, runs_dir_=runs_dir(before))
        variant = per_case_metrics("validation", CAPGRAPH_SCORE, runs_dir_=runs_dir(after))
        shared = set(baseline) & set(variant)
        if not shared:
            continue
        deltas = {
            metric: sum(variant[c][metric] - baseline[c][metric] for c in shared) / len(shared)
            for metric in metrics
        }
        widest = max(widest, *(abs(value) for value in deltas.values()))
        rows.append(
            f"| `{before}` → `{after}` | "
            + " | ".join(f"{deltas[metric]:+.3f}" for metric in metrics)
            + " |"
        )
    if not rows:
        return []
    return [
        "",
        "## The noise gauge, measured inside this study",
        "",
        "Every arm re-runs the whole pipeline and the intent parse is a model call, so no "
        "two arms retrieve the same candidate pools: an arm-to-arm delta is a lever plus "
        "a fresh draw of run-to-run variance. The deterministic `capgraph_score` arm "
        "ranks the entire pool and never sees a prompt, a window, or a sample, so across "
        "the arms below — which change only those three things — whatever it moves by is "
        "noise and nothing else.",
        "",
        "| Comparison (nothing in it can move the score arm) | Hit@1 | Hit@5 | Hit@10 | MRR |",
        "|---|---:|---:|---:|---:|",
        *rows,
        "",
        f"The largest such swing is **{widest:.3f}**, reproducing from a different "
        "direction the 0.100 run-to-run floor the v2 section measured by re-running one "
        "configuration twice. No v3 lever was adopted or rejected on a delta smaller "
        "than that.",
    ]


def full_tables(split: str) -> list[str]:
    results, failures = score_split(split, runs_dir=split_dir(split))
    if not results:
        return []
    expected = len(load_manifest(splits=(split,)))
    lines = [
        "",
        f"### v3 {split} split",
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
            f"v3 {split} run diagnostics (graph system):",
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


def paired_section(split: str) -> list[str]:
    """v3 against v2 case by case, for both graph systems."""
    lines: list[str] = []
    for system in (CAPGRAPH_FULL, CAPGRAPH_SCORE):
        baseline = per_case_metrics(split, system, runs_dir_=v2_dir(split))
        variant = per_case_metrics(split, system, runs_dir_=split_dir(split))
        shared = set(baseline) & set(variant)
        if not shared:
            continue
        lines += [
            "",
            f"`{system}`, v3 against v2 on the {len(shared)} cases both scored:",
            "",
            *paired_rows(baseline, variant),
        ]
    if not lines:
        return []
    return [
        "",
        f"## Paired per-query statistics — {split} split",
        "",
        "Aggregates hide the pairing: these runs answer the same cases, so what matters "
        "is which cases moved. **Wins** are cases v3 got right and v2 did not, "
        "**losses** the reverse. McNemar's exact test uses only those discordant cases; "
        "the MRR row uses a case-level bootstrap, which keeps the pairing because each "
        "resample draws the same case for both arms.",
        *lines,
    ]


def rerank_validity(split: str, *, runs_dir_: Path) -> tuple[int, int]:
    """(rejected entries, entries the re-rank was offered) for one checkpointed run.

    Rejections have to be read as a *rate*: a window of 32 offers the model twice as
    many chances to cite something it should not as a window of 15, so the raw count is
    not comparable across configurations and the count per offered entry is.
    """
    rejected = offered = 0
    for (system, _), record in load_checkpoint(split, runs_dir=runs_dir_).items():
        if system != CAPGRAPH_FULL or "detail" not in record:
            continue
        detail = record["detail"]
        rejected += len(detail.get("rejected", ()))
        offered += sum(int(counts.get("reranked", 0)) for counts in detail["candidate_counts"])
    return rejected, offered


def rerank_validity_section() -> list[str]:
    """What the card view did to citation validity — including where it did not hold.

    This is the one place the frozen record in `docs/benchmark-v3-config.md` is
    corrected rather than repeated. That record adopted the card partly because
    citation rejections went 8 to 0 between two validation arms. They did, at a window
    of 15. They did not stay there once the window widened, and the test split is where
    that becomes unambiguous.
    """
    runs = [
        ("v2 (window 15, profile view)", "validation", v2_dir("validation")),
        ("v2 (window 15, profile view)", "test", v2_dir("test")),
        ("`ab_lexical` (window 15, profile view)", "validation", runs_dir("ab_lexical")),
        ("`ab_cards` (window 15, card view)", "validation", runs_dir("ab_cards")),
        ("v3 frozen (window 32, card view)", "validation", split_dir("validation")),
        ("v3 frozen (window 32, card view)", "test", runs_dir()),
    ]
    rows = []
    rates: dict[str, float] = {}
    for label, split, path in runs:
        rejected, offered = rerank_validity(split, runs_dir_=path)
        if not offered:
            continue
        rate = rejected / offered
        rates[f"{label}|{split}"] = rate
        rows.append(
            f"| {label} | {split} | {rejected} | {offered} | {rate * 100:.2f}% |"
        )
    if not rows:
        return []
    v2_rate = rates.get("v2 (window 15, profile view)|test")
    v3_rate = rates.get("v3 frozen (window 32, card view)|test")
    verdict = ""
    if v2_rate is not None and v3_rate is not None:
        direction = "above" if v3_rate > v2_rate else "below"
        verdict = (
            f"On the test split the adopted configuration rejects {v3_rate * 100:.2f}% "
            f"of the entries it is offered, {direction} v2's {v2_rate * 100:.2f}%. "
        )
    return [
        "",
        "## Re-rank citation validity — a correction to the frozen record",
        "",
        "`docs/benchmark-v3-config.md` adopted the card view partly because citation "
        "rejections fell from 8 to 0 between `ab_lexical` and `ab_cards`. That "
        "measurement is real and is reproduced below — and it did not survive the wider "
        "window. Rejections are counted per entry the re-rank was actually offered, "
        "because a window of 32 gives the model twice as many chances to mis-cite as a "
        "window of 15 and the raw counts are not comparable.",
        "",
        "| Run | Split | Rejected entries | Entries offered | Rate |",
        "|---|---|---:|---:|---:|",
        *rows,
        "",
        verdict
        + "The card removed mis-citation at a window of 15 and did not hold it at 32, so "
        "the honest reading is that the card's validity benefit is a window-15 effect "
        "and the frozen configuration does not inherit it. What the card did deliver, "
        "and what the test split confirms, is cost: the frozen run spent $3.7929 "
        "against v2's $4.3484 while showing the model twice as many candidates. Every "
        "rejected entry is still discarded rather than repaired, and the person is "
        "re-appended in deterministic score order, so no unevidenced claim reaches a "
        "shortlist in either configuration.",
    ]


def label_noise_section() -> list[str]:
    """The audit: how much of the test miss set is the label rather than the system."""
    cases = load_manifest(splits=("test",))
    per_case = per_case_metrics("test", CAPGRAPH_FULL, runs_dir_=runs_dir())
    if not per_case:
        return []
    try:
        audits = load_label_audit(cases)
    except FileNotFoundError:
        return []
    audit = summarize(cases, audits, per_case)
    scored = [case for case in cases if case.issue_id in per_case]
    widened = 0
    for case in scored:
        record = audits.get(case.issue_id)
        if record is None:
            continue
        if record.accepted_ids(case.eligible_roster) != set(case.truth_person_ids):
            widened += 1

    labels = {
        CHANGE_LOG_LABEL: "assignment event recorded at resolution",
        SNAPSHOT_LABEL: "no assignment event recorded (final snapshot, timing unknown)",
        "unknown": "no audit row",
    }
    lines = [
        "",
        "## Label-noise audit (test split, frozen run — reporting, not a lever)",
        "",
        "The triage literature reports the recorded assignee differing from the person "
        "who did the work in roughly a fifth of issues, and label cleaning moving MRR by "
        "a comparable amount. This benchmark already reconstructs its truth at the safe "
        "resolution boundary rather than taking the dump's final assignee snapshot, so "
        "the question here is what is *left*: does the audit-only snapshot name someone "
        "else, and does the system miss disproportionately on the cases whose label is "
        "weaker?",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Test cases scored | {audit.n} |",
        f"| Truth later reassigned (final snapshot names someone else) | {audit.reassigned} |",
        f"| Truth corroborated by a recorded assignment event at resolution | "
        f"{audit.corroborated} |",
        f"| Truth from the final snapshot, assignment time unknown | "
        f"{audit.uncorroborated} |",
        f"| Cases with no audit row | {audit.missing_audit} |",
        f"| Cases where \"truth OR final-snapshot assignee\" widens the accepted set | "
        f"{widened} |",
        "",
        "`capgraph_full` by label provenance:",
        "",
        "| Truth provenance | N | Hit@1 | Hit@5 | Hit@10 | MRR | Hit@5 misses |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, metrics in audit.by_class.items():
        lines.append(
            f"| {labels.get(label, label)} | {int(metrics['n'])} | "
            f"{metrics['hit_at_1']:.3f} | {metrics['hit_at_5']:.3f} | "
            f"{metrics['hit_at_10']:.3f} | {metrics['mrr']:.3f} | "
            f"{audit.misses_by_class.get(label, 0)} |"
        )
    return lines


def headline_section() -> list[str]:
    """What the frozen test run showed, stated before the tables.

    Every comparative word below is derived from the measured deltas rather than
    written around them, so this paragraph cannot drift from the tables underneath it.
    """
    v2 = _by_system(score_split("test", runs_dir=v2_dir("test"))[0])
    v3 = _by_system(score_split("test", runs_dir=runs_dir())[0])
    if CAPGRAPH_FULL not in v2 or CAPGRAPH_FULL not in v3:
        return []

    metrics = ("hit_at_1", "hit_at_5", "hit_at_10", "mrr")

    def deltas(system: str) -> dict[str, float]:
        return {
            metric: getattr(v3[system], metric) - getattr(v2[system], metric)
            for metric in metrics
        }

    def stated(values: dict[str, float]) -> str:
        return ", ".join(f"{METRIC_LABELS[m]} {value:+.3f}" for m, value in values.items())

    def per_query(system: str) -> float:
        return v3[system].cost_usd_total / max(v3[system].n_briefs, 1)

    full, score = deltas(CAPGRAPH_FULL), deltas(CAPGRAPH_SCORE)
    largest = max(abs(value) for value in full.values())
    noise = 0.100                       # the v2 section's measured run-to-run floor
    verdict = (
        "did not move the full system beyond the measured noise floor"
        if largest <= noise
        else "moved the full system past the measured noise floor"
    )
    recall_v2 = v2[CAPGRAPH_FULL].candidate_recall or 0.0
    recall_v3 = v3[CAPGRAPH_FULL].candidate_recall or 0.0

    # The aggregate and the paired test can disagree, and when they do the paired one is
    # the more informative: these runs answered the same 120 cases. Stated here rather
    # than left for the reader to find three tables down.
    before = per_case_metrics("test", CAPGRAPH_FULL, runs_dir_=v2_dir("test"))
    after = per_case_metrics("test", CAPGRAPH_FULL, runs_dir_=runs_dir())
    paired = {
        metric: paired_binary(
            METRIC_LABELS[metric],
            {case: values[metric] for case, values in before.items()},
            {case: values[metric] for case, values in after.items()},
        )
        for metric in ("hit_at_1", "hit_at_5", "hit_at_10")
    }
    sharpest = min(paired.values(), key=lambda row: row.p_value)
    reading = (
        "that is the closest this study comes to a significant result, and it is a "
        "regression"
        if sharpest.delta < 0
        else "that is the strongest evidence of gain in this study"
    )
    return [
        "",
        "## What this run showed",
        "",
        f"On the 120-case test split the adopted configuration **{verdict}**: "
        f"{stated(full)} — the largest of them {largest:.3f}, against the 0.100 "
        "run-to-run variance the v2 section measured from changing nothing.",
        "",
        "The aggregate is not the whole story, and here it is the more forgiving half. "
        f"Case by case, the sharpest movement is **{sharpest.metric} "
        f"{sharpest.delta:+.3f}**, from {sharpest.wins} cases v3 wins and "
        f"{sharpest.losses} it loses (McNemar exact p = {sharpest.p_value:.3f}); "
        f"{reading}. Nothing here was tuned on these cases, so it is a measurement "
        "rather than a fit — but it is one paired comparison on one split, and the "
        "deltas around it are not distinguishable from noise.",
        "",
        f"Candidate recall moved {recall_v2:.3f} -> {recall_v3:.3f}. That is the "
        "lexical arm doing exactly and only what it was adopted for: it raises the "
        "ceiling on what the graph system can reach, and the wider window is what lets "
        "the re-rank see the candidates underneath it. Whether the re-rank then ranks "
        "them well is a separate question, and it is the one the deltas above answer.",
        "",
        f"The deterministic arm moved {stated(score)}. No v3 lever touches it — the "
        "score arm ranks the whole pool and never sees a prompt, a window, or a sample "
        "— so its movement is the lexical arm's extra candidates plus the run-to-run "
        "variance of re-retrieving, and it is the fairest available gauge of how much "
        "of the full system's movement is noise.",
        "",
        f"Cost per query: ${per_query(CAPGRAPH_FULL):.4f} for the full system against "
        f"${per_query(CAPGRAPH_SCORE):.4f} for the deterministic arm, at "
        f"{v3[CAPGRAPH_FULL].latency_ms_mean / 1000:.1f}s against "
        f"{v3[CAPGRAPH_SCORE].latency_ms_mean / 1000:.1f}s.",
    ]


def spend_section() -> list[str]:
    stages = v3_stages()
    lines = ["", "## Spend", "", "| Stage | Calls | Cost (USD) |", "|---|---:|---:|"]
    total = 0.0
    for stage, calls, cost in spend_by_stage(stages):
        total += cost
        lines.append(f"| `{stage}` | {calls} | {cost:.4f} |")
    lines.append(f"| **total** | | **{total:.4f}** |")
    purposes = spend_by_purpose(stages)
    if purposes:
        lines += [
            "",
            "| Call type | Calls | Cost (USD) |",
            "|---|---:|---:|",
            *(
                f"| `{name}` | {count} | {cost:.4f} |"
                for name, (count, cost) in purposes.items()
            ),
        ]
    lines += [
        "",
        f"Reconciled against `data/llm_costs.jsonl` by stage name, retries included, "
        f"against the ${float(settings['eval.v3.max_total_cost_usd']):.2f} ceiling the "
        "owner authorized on 2026-08-12.",
    ]
    return lines


def render_report(*, pool_rows: Sequence[PoolRow] | None = None) -> str:
    """The v3 section: what changed, what it did on each split, and what it cost."""
    config = v3_config("test")
    v2 = dict(settings["eval.v3.v2_baseline"])
    retrieval = dict(config["retrieval"])

    lines = [
        "# Benchmark v3",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')} against the same manifest "
        f"`{config['manifest_version']}` and the same 150 cases as v1 and v2, under "
        f"configuration digest `{config_digest(config)}`. The v1 and v2 tables above are "
        "unchanged; nothing in this section re-scores them.",
        "",
        "Every lever was chosen on the 30 validation cases. The 120-case test split was "
        "run once, after the configuration below was frozen in "
        "`docs/benchmark-v3-config.md`. Its checkpoints live in "
        f"`data/eval/{settings['eval.v3.runs_subdir']}/`, separate from v1's and v2's. "
        "This is the third and last exposure of this manifest's test split.",
        "",
        f"The v2 column is transcribed from the frozen v2 record (digest `{v2['digest']}`) "
        "and re-scored from its checkpoints, not re-run.",
        "",
        "## v3 configuration",
        "",
        "| Setting | v2 | v3 |",
        "|---|---|---|",
        f"| Re-rank prompt | `{v2['rerank_prompt']}` | `{config['rerank_prompt']}` "
        f"({config['rerank_prompt_digest']}) |",
        f"| Candidate view | `{v2['rerank_candidate_view']}` | "
        f"`{retrieval['rerank_candidate_view']}` |",
        f"| Re-rank window | {v2['rerank_top_k']} | {retrieval['rerank_top_k']} |",
        f"| Retrieval | vector top-{v2['vector_top_k']} ∪ structured "
        f"top-{v2['structured_top_k']}"
        + (f" ∪ BM25 top-{v2['bm25_top_k']}" if int(v2["bm25_top_k"]) else "")
        + f" | vector top-{retrieval['vector_top_k']} ∪ structured "
        f"top-{retrieval['structured_top_k']}"
        + (f" ∪ BM25 top-{retrieval['bm25_top_k']}" if int(retrieval["bm25_top_k"]) else "")
        + " |",
        f"| Re-rank samples | {v2['rerank_samples']} | {retrieval['rerank_samples']} |",
        "| Strong-model finisher | "
        + (f"top-{v2['finisher_top_k']}" if int(v2["finisher_top_k"]) else "off")
        + " | "
        + (
            f"top-{retrieval['finisher_top_k']} on `{config.get('finisher_model')}`"
            if int(retrieval["finisher_top_k"])
            else "off"
        )
        + " |",
        "| Score weights | unchanged | "
        + ", ".join(f"{k} {v}" for k, v in dict(config["scoring"])["weights"].items())
        + " |",
        f"| Cost-log stages | `{v2['validation_stage']}` / `{v2['test_stage']}` | "
        f"`{v3_stages()[0]}` / `{v3_stages()[1]}` |",
    ]

    lines += headline_section()

    for split in SPLITS:
        rows = side_by_side(split)
        if rows:
            lines += ["", f"## v1 vs v2 vs v3 — {split} split", "", *rows]
    for split in SPLITS:
        lines += full_tables(split)
    for split in SPLITS:
        lines += paired_section(split)

    if pool_rows:
        lines += [
            "",
            "## Lever findings — pool and window (validation, offline from the score "
            "checkpoint)",
            "",
            "Every row re-scores the same checkpointed score components, so the whole "
            "table cost nothing beyond the one intent-parse dump. *Window recall* is the "
            "share of cases whose truth reaches the re-rank window: it is the ceiling on "
            "the full system's Hit@K, because a candidate the re-rank is never shown "
            "cannot be ranked.",
            "",
            *render_pool_levers(pool_rows),
        ]

    arms = [str(name) for name in settings.get("eval.v3.ab_arms") or ()]
    if arms:
        rows = compare_variants("validation", [V2_ARM, *arms])
        if len(rows) > 2:
            lines += [
                "",
                "## Lever findings — paid validation A/B (30 cases, "
                f"`{v3_stages()[0]}`)",
                "",
                "Each arm changes exactly one thing against the arm above it, and each "
                "has its own checkpoint namespace, so no two configurations are ever "
                "scored together. Read every delta against the 0.100 run-to-run noise "
                "floor measured in the v2 section and re-measured below: on 30 cases "
                "none of these rows is individually significant, which is why adoption "
                "rests on the construction-level mechanisms recorded in "
                "`docs/benchmark-v3-config.md` — candidate recall, window recall, and "
                "citation validity — rather than on this table.",
                "",
                *rows,
            ]
    lines += noise_gauge_section()

    lines += rerank_validity_section()
    lines += label_noise_section()
    lines += spend_section()
    return "\n".join(lines) + "\n"


def write_tracked_section(markdown: str, *, path: Path = TRACKED_REPORT) -> None:
    """Replace the v3 section below its marker, leaving v1, v2 and v4 alone.

    Sections are appended oldest first, so re-reporting v3 must keep whatever follows
    it: benchmark v4 lives below its own marker further down the same file, and
    rebuilding v3's tables is not a reason to delete v4's.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    head, _, rest = existing.partition(V3_MARKER)
    tail = ""
    if V4_MARKER in rest:
        tail = "\n" + V4_MARKER + rest.split(V4_MARKER, 1)[1]
    path.write_text(
        f"{head.rstrip(chr(10))}\n\n{V3_MARKER}\n\n{markdown}{tail}", encoding="utf-8"
    )


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark v3 experiments and run")
    parser.add_argument("--scores", action="store_true",
                        help="checkpoint score components for a split (spends: intent only)")
    parser.add_argument("--pool-levers", action="store_true",
                        help="offline: pool and window recall from the score checkpoint")
    parser.add_argument("--split", choices=SPLITS, help="split to run or dump")
    parser.add_argument("--systems", default=",".join(ALL_SYSTEMS))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--variant", help="A/B arm name; its own namespace inside v3's")
    parser.add_argument("--report", action="store_true",
                        help="offline: rebuild the v3 section of docs/eval-results.md")
    parser.add_argument("--compare",
                        help="offline: comma-separated arms to tabulate ('v2' for v2's run)")
    parser.add_argument("--paired", nargs=2, metavar=("BASELINE", "VARIANT"),
                        help="offline: paired per-query statistics between two arms")
    args = parser.parse_args(argv)
    split = args.split or "validation"

    if args.compare:
        arms = [name.strip() or None for name in args.compare.split(",")]
        print("\n".join(compare_variants(split, arms)))
        return 0

    if args.paired:
        def resolve(name: str) -> Path:
            return v2_dir(split) if name == V2_ARM else runs_dir(None if name == "-" else name)

        baseline, variant = args.paired
        for system in (CAPGRAPH_FULL, CAPGRAPH_SCORE):
            before = per_case_metrics(split, system, runs_dir_=resolve(baseline))
            after = per_case_metrics(split, system, runs_dir_=resolve(variant))
            if not (before and after):
                continue
            print(f"\n{system}: {baseline} -> {variant}, {len(set(before) & set(after))} cases\n")
            print("\n".join(paired_rows(before, after)))
        return 0

    if args.scores:
        from .scores import dump_split, scores_path

        dump_split(
            split,
            stage=stage_for(split),
            limit=args.limit,
            path=scores_path(split, subdir=scores_dir()),
            budget=enforce_v3_budget,
        )
        return 0

    if args.pool_levers:
        rows = pool_levers(split)
        print(f"{split} split, offline pool and window levers:\n")
        print("\n".join(render_pool_levers(rows)))
        V3_DIR.mkdir(parents=True, exist_ok=True)
        (V3_DIR / f"pool_levers_{split}.json").write_text(
            json.dumps([row.__dict__ for row in rows], indent=2) + "\n", encoding="utf-8"
        )
        return 0

    if args.split:
        systems = [name.strip() for name in args.systems.split(",") if name.strip()]
        counts = run_v3_split(
            args.split, systems=systems, limit=args.limit, variant=args.variant
        )
        print(json.dumps(dict(sorted(counts.items())), indent=2))
        if args.variant:
            # An A/B arm is an experiment, not the frozen run: it must not rewrite the
            # tracked v3 section, which reports the frozen configuration only.
            print(f"arm '{args.variant}' checkpointed in {runs_dir(args.variant)}; "
                  f"compare it with --compare v2,{args.variant}")
            return 0

    if args.report or args.split:
        rows = None
        try:
            rows = pool_levers("validation")
        except SystemExit:
            pass
        markdown = render_report(pool_rows=rows)
        V3_DIR.mkdir(parents=True, exist_ok=True)
        (V3_DIR / "results.md").write_text(markdown, encoding="utf-8")
        write_tracked_section(markdown)
        print(markdown)
        print(f"\nwrote {V3_DIR / 'results.md'} and the v3 section of {TRACKED_REPORT}")
        return 0

    parser.error("nothing to do: pass --scores, --pool-levers, --split, or --report")
    return 2


__all__ = [
    "PoolRow",
    "V3BudgetError",
    "arm_verdicts",
    "compare_variants",
    "noise_gauge_section",
    "enforce_v3_budget",
    "headline_section",
    "label_noise_section",
    "paired_rows",
    "per_case_metrics",
    "pool_levers",
    "project_case_cost",
    "render_report",
    "rerank_validity",
    "rerank_validity_section",
    "run_v3_split",
    "runs_dir",
    "side_by_side",
    "spend_by_stage",
    "v3_config",
    "write_tracked_section",
]


if __name__ == "__main__":
    raise SystemExit(main())
