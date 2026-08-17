"""Metric and reporting foundations for the temporal benchmark.

This module intentionally does not invoke the CapGraph query pipeline or an LLM.
Rankers are wired in through :func:`evaluate`; each receives a query context
containing the as-of time and frozen roster.  Returning an ineligible ID is an error,
not something the harness silently repairs after future evidence may have influenced
the ranking.

``Hit@K`` answers whether *any* truth ID appears in the first K results.  True
``Recall@K`` is also retained and divides the number of retrieved truth IDs by the
number of truth IDs.  They are identical only for the current single-assignee cases.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median

from ..settings import DATA_DIR
from .contracts import BenchmarkQueryContext, RankingOutput
from .holdout import MANIFEST_PATH, MANIFEST_VERSION, BenchmarkManifestEntry

RESULTS_PATH = DATA_DIR / "eval" / "results.md"
REQUIRED_HIT_KS = (1, 5, 10)
RECALL_KS = (5, 10)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def hit_at_k(ranked: Sequence[str], truth: set[str], k: int) -> float:
    """Return 1 when any truth ID occurs in the first ``k`` unique results."""
    if k < 1:
        raise ValueError("k must be at least 1")
    if not truth:
        return 0.0
    return float(bool(truth.intersection(_dedupe(ranked)[:k])))


def recall_at_k(ranked: Sequence[str], truth: set[str], k: int) -> float:
    """Return mathematically correct set recall over the first ``k`` results."""
    if k < 1:
        raise ValueError("k must be at least 1")
    if not truth:
        return 0.0
    retrieved = set(_dedupe(ranked)[:k])
    return len(truth.intersection(retrieved)) / len(truth)


def candidate_recall(candidates: Sequence[str], truth: set[str]) -> float:
    """Recall of truth IDs before final ranking/truncation."""
    if not truth:
        return 0.0
    return len(truth.intersection(set(candidates))) / len(truth)


def mrr(ranked: Sequence[str], truth: set[str]) -> float:
    """Reciprocal rank of the first unique relevant result."""
    for index, person_id in enumerate(_dedupe(ranked), 1):
        if person_id in truth:
            return 1.0 / index
    return 0.0


@dataclass(frozen=True)
class MetricSummary:
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    candidate_recall: float | None
    n_briefs: int
    latency_ms_mean: float
    latency_ms_median: float
    latency_ms_p95: float
    cost_usd_total: float


@dataclass(frozen=True)
class EvaluationResult(MetricSummary):
    system: str
    per_project: dict[str, MetricSummary] = field(default_factory=dict)


@dataclass(frozen=True)
class _CaseMetric:
    project_key: str
    hits: dict[int, float]
    recalls: dict[int, float]
    mrr: float
    candidate_recall: float | None
    latency_ms: float
    cost_usd: float


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[rank])


def _summarize(metrics: Sequence[_CaseMetric]) -> MetricSummary:
    if not metrics:
        return MetricSummary(
            hit_at_1=0.0,
            hit_at_5=0.0,
            hit_at_10=0.0,
            recall_at_5=0.0,
            recall_at_10=0.0,
            mrr=0.0,
            candidate_recall=None,
            n_briefs=0,
            latency_ms_mean=0.0,
            latency_ms_median=0.0,
            latency_ms_p95=0.0,
            cost_usd_total=0.0,
        )
    latencies = [metric.latency_ms for metric in metrics]
    candidate_recalls = [
        metric.candidate_recall
        for metric in metrics
        if metric.candidate_recall is not None
    ]
    # A partial value would silently change the denominator across systems.  Report
    # candidate recall only when every case supplied its pre-ranking candidate pool.
    summarized_candidate_recall = (
        mean(candidate_recalls) if len(candidate_recalls) == len(metrics) else None
    )
    return MetricSummary(
        hit_at_1=mean(metric.hits[1] for metric in metrics),
        hit_at_5=mean(metric.hits[5] for metric in metrics),
        hit_at_10=mean(metric.hits[10] for metric in metrics),
        recall_at_5=mean(metric.recalls[5] for metric in metrics),
        recall_at_10=mean(metric.recalls[10] for metric in metrics),
        mrr=mean(metric.mrr for metric in metrics),
        candidate_recall=summarized_candidate_recall,
        n_briefs=len(metrics),
        latency_ms_mean=mean(latencies),
        latency_ms_median=median(latencies),
        latency_ms_p95=_percentile(latencies, 0.95),
        cost_usd_total=sum(metric.cost_usd for metric in metrics),
    )


def query_context(
    case: BenchmarkManifestEntry, *, expected_version: str = MANIFEST_VERSION
) -> BenchmarkQueryContext:
    """Validate selected-case invariants and construct a truth-free ranker input.

    ``expected_version`` is the manifest the caller believes it is scoring. It defaults
    to v1's so an unqualified call cannot silently accept a case from a different
    benchmark; benchmark v4 passes its own version (see :mod:`capgraph.eval.packages`).
    """
    if case.manifest_version != expected_version:
        raise ValueError(
            f"manifest version {case.manifest_version!r} does not match {expected_version!r}"
        )
    if case.split == "excluded" or case.exclusion_reason is not None:
        raise ValueError(f"issue {case.issue_id} is excluded from evaluation")
    if case.as_of_time is None:
        raise ValueError(f"issue {case.issue_id} has no benchmark as-of time")
    if not case.issue_id or not case.project_key or not case.query_text.strip():
        raise ValueError(f"issue {case.issue_id!r} has incomplete query context")
    roster = tuple(_dedupe(case.eligible_roster))
    if len(roster) != len(case.eligible_roster):
        raise ValueError(f"issue {case.issue_id} has duplicate eligible-roster IDs")
    truth = set(case.truth_person_ids)
    if not truth:
        raise ValueError(f"issue {case.issue_id} has no ground-truth assignee")
    if not truth.issubset(roster):
        raise ValueError(f"issue {case.issue_id} truth is outside its frozen roster")
    return BenchmarkQueryContext(
        issue_id=case.issue_id,
        query_text=case.query_text,
        as_of_time=case.as_of_time,
        project_key=case.project_key,
        eligible_roster=roster,
    )


def adapt_text_ranker(
    rank_fn: Callable[[str], Sequence[str] | RankingOutput],
) -> Callable[[BenchmarkQueryContext], Sequence[str] | RankingOutput]:
    """Explicitly adapt a text-only ranker whose index is already snapshot-frozen.

    This adapter cannot enforce evidence time by itself.  Callers must use it only
    when the wrapped ranker's corpus/index was independently frozen to the manifest
    snapshot.  Requiring an explicit adapter prevents accidental legacy use.
    """

    def adapted(context: BenchmarkQueryContext) -> Sequence[str] | RankingOutput:
        return rank_fn(context.query_text)

    return adapted


def evaluate(
    system: str,
    rank_fn: Callable[[BenchmarkQueryContext], Sequence[str] | RankingOutput],
    briefs: Sequence[BenchmarkManifestEntry],
    *,
    expected_version: str = MANIFEST_VERSION,
) -> EvaluationResult:
    """Evaluate a temporally aware ranker with strict manifest invariants.

    Rich :class:`RankingOutput` lets a future system report pre-ranking candidate
    recall, measured latency, and cost.  When ``candidate_ids`` is absent, candidate
    recall is reported as unavailable rather than inferred from the final ranking.
    """
    metrics: list[_CaseMetric] = []
    for brief in briefs:
        context = query_context(brief, expected_version=expected_version)
        truth = set(brief.truth_person_ids)
        started = time.perf_counter()
        raw_output = rank_fn(context)
        measured_ms = (time.perf_counter() - started) * 1000
        if isinstance(raw_output, (str, bytes)):
            raise TypeError("ranker output must be a sequence of person IDs, not text")
        output = (
            raw_output
            if isinstance(raw_output, RankingOutput)
            else RankingOutput(ranked_ids=raw_output)
        )

        ranked = _dedupe(output.ranked_ids)
        candidates = (
            None if output.candidate_ids is None else _dedupe(output.candidate_ids)
        )
        eligible = set(context.eligible_roster)
        ineligible_ranked = set(ranked).difference(eligible)
        if ineligible_ranked:
            raise ValueError(
                f"ranker returned IDs outside frozen roster for {brief.issue_id}: "
                f"{sorted(ineligible_ranked)}"
            )
        if candidates is not None:
            ineligible_candidates = set(candidates).difference(eligible)
            if ineligible_candidates:
                raise ValueError(
                    f"candidate generator returned IDs outside frozen roster for "
                    f"{brief.issue_id}: {sorted(ineligible_candidates)}"
                )
            if not set(ranked).issubset(candidates):
                raise ValueError(
                    f"ranking for {brief.issue_id} contains IDs absent from candidate pool"
                )
        if output.latency_ms is not None and output.latency_ms < 0:
            raise ValueError("ranker latency cannot be negative")
        if output.cost_usd < 0:
            raise ValueError("ranker cost cannot be negative")

        metrics.append(
            _CaseMetric(
                project_key=str(brief.project_key),
                hits={k: hit_at_k(ranked, truth, k) for k in REQUIRED_HIT_KS},
                recalls={k: recall_at_k(ranked, truth, k) for k in RECALL_KS},
                mrr=mrr(ranked, truth),
                candidate_recall=(
                    None if candidates is None else candidate_recall(candidates, truth)
                ),
                latency_ms=(
                    output.latency_ms if output.latency_ms is not None else measured_ms
                ),
                cost_usd=output.cost_usd,
            )
        )

    overall = _summarize(metrics)
    grouped: dict[str, list[_CaseMetric]] = defaultdict(list)
    for metric in metrics:
        grouped[metric.project_key].append(metric)
    return EvaluationResult(
        **overall.__dict__,
        system=system,
        per_project={project: _summarize(rows) for project, rows in sorted(grouped.items())},
    )


def load_manifest(
    path: Path = MANIFEST_PATH,
    *,
    splits: tuple[str, ...] | None = ("validation", "test"),
) -> list[BenchmarkManifestEntry]:
    """Load and validate a manifest, optionally retaining only named splits."""
    entries: list[BenchmarkManifestEntry] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = BenchmarkManifestEntry.model_validate_json(line)
            if splits is None or entry.split in splits:
                entries.append(entry)
    return entries


def write_results(results: Sequence[EvaluationResult], path: Path = RESULTS_PATH) -> None:
    """Write overall and per-project benchmark metrics as reviewable Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Temporal benchmark results",
        "",
        HEADER_ROW,
        HEADER_RULE,
    ]
    for result in results:
        lines.append(summary_row(result.system, result))

    if results:
        lines.extend(["", "## Per-project results", "", HEADER_ROW, HEADER_RULE])
        for result in results:
            for project_key, summary in result.per_project.items():
                lines.append(summary_row(f"{result.system} / {project_key}", summary))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


HEADER_ROW = (
    "| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | "
    "Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | "
    "Cost (USD) |"
)
HEADER_RULE = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"


def summary_row(label: str, summary: MetricSummary) -> str:
    return (
        f"| {label} | {summary.n_briefs} | {summary.hit_at_1:.3f} | "
        f"{summary.hit_at_5:.3f} | {summary.hit_at_10:.3f} | "
        f"{summary.recall_at_5:.3f} | {summary.recall_at_10:.3f} | "
        f"{summary.mrr:.3f} | {_optional_metric(summary.candidate_recall)} | "
        f"{summary.latency_ms_mean:.1f} | {summary.latency_ms_median:.1f} | "
        f"{summary.latency_ms_p95:.1f} | {summary.cost_usd_total:.4f} |"
    )


def _optional_metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def manifest_summary() -> dict[str, object]:
    """Reconcile the manifest's selected and excluded counts without running anything."""
    all_entries = load_manifest(splits=None)
    selected = [entry for entry in all_entries if entry.split != "excluded"]
    exclusions: dict[str, int] = defaultdict(int)
    for entry in all_entries:
        if entry.exclusion_reason:
            exclusions[entry.exclusion_reason] += 1
    splits: dict[str, int] = defaultdict(int)
    for entry in selected:
        splits[entry.split] += 1
    return {
        "manifest": str(MANIFEST_PATH),
        "version": selected[0].manifest_version if selected else None,
        "selected": len(selected),
        "splits": dict(sorted(splits.items())),
        "excluded": len(all_entries) - len(selected),
        "exclusion_reasons": dict(sorted(exclusions.items())),
    }


def print_manifest_summary() -> None:
    print(json.dumps(manifest_summary(), indent=2))
