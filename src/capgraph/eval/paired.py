"""Paired per-query statistics: what changed case by case, not on average.

Two configurations of this benchmark are run over the *same* cases, so comparing their
aggregates throws away the pairing and with it most of the power. On 120 cases a 0.03
difference in Hit@1 is four cases; whether those four are a real improvement or a
reshuffle is a question about which cases moved, and the aggregate cannot answer it.

Two tests, both exact and both offline:

* **McNemar** for the binary metrics (Hit@1, Hit@5, Hit@10). Only the discordant
  cases carry information — the ones one arm got right and the other got wrong — and
  the exact two-sided binomial test on those is the textbook answer at this sample
  size. No normal approximation, no continuity correction to argue about.
* **Bootstrap** for MRR, which is not binary. Resampling *cases* (not queries within
  cases) preserves the pairing: each resample draws the same case index for both arms,
  so the interval is on the paired difference.

Nothing here decides anything on its own. A significant McNemar result on 30 validation
cases still sits inside the measured 0.10 run-to-run noise floor; these numbers exist so
a reader can see how many cases moved and in which direction, which is what the v3 work
order asks for alongside the aggregates.
"""
from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import comb

from ..settings import settings


@dataclass(frozen=True)
class PairedBinary:
    """One binary metric compared case by case."""

    metric: str
    n: int
    wins: int              # baseline wrong, variant right
    losses: int            # baseline right, variant wrong
    ties: int
    baseline_rate: float
    variant_rate: float
    p_value: float

    @property
    def delta(self) -> float:
        return self.variant_rate - self.baseline_rate


@dataclass(frozen=True)
class PairedContinuous:
    """One continuous metric compared case by case."""

    metric: str
    n: int
    better: int
    worse: int
    ties: int
    baseline_mean: float
    variant_mean: float
    mean_delta: float
    ci_low: float
    ci_high: float


def mcnemar_exact_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial p for the discordant pairs.

    With ``n = wins + losses`` discordant cases and no effect, each is a fair coin.
    The two-sided p is the total probability of a split at least as lopsided as the
    observed one. Returns 1.0 when nothing was discordant, which is the honest answer:
    the two arms disagreed about nothing, so there is no evidence either way.
    """
    n = wins + losses
    if n == 0:
        return 1.0
    observed = min(wins, losses)
    tail = sum(comb(n, k) for k in range(observed + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_binary(
    metric: str, baseline: Mapping[str, float], variant: Mapping[str, float]
) -> PairedBinary:
    """Compare one 0/1 metric over the cases both arms scored."""
    shared = sorted(set(baseline) & set(variant))
    wins = sum(1 for case in shared if variant[case] > baseline[case])
    losses = sum(1 for case in shared if variant[case] < baseline[case])
    n = len(shared)
    return PairedBinary(
        metric=metric,
        n=n,
        wins=wins,
        losses=losses,
        ties=n - wins - losses,
        baseline_rate=sum(baseline[case] for case in shared) / n if n else 0.0,
        variant_rate=sum(variant[case] for case in shared) / n if n else 0.0,
        p_value=mcnemar_exact_p(wins, losses),
    )


def paired_bootstrap(
    metric: str,
    baseline: Mapping[str, float],
    variant: Mapping[str, float],
    *,
    resamples: int | None = None,
    seed: int | None = None,
    confidence: float = 0.95,
) -> PairedContinuous:
    """Percentile bootstrap confidence interval on the paired mean difference."""
    shared = sorted(set(baseline) & set(variant))
    n = len(shared)
    deltas = [variant[case] - baseline[case] for case in shared]
    mean_delta = sum(deltas) / n if n else 0.0
    resamples = int(settings["eval.v3.bootstrap_resamples"]) if resamples is None else resamples
    seed = int(settings["eval.v3.bootstrap_seed"]) if seed is None else seed
    low = high = mean_delta
    if n and resamples > 0:
        rng = random.Random(seed)
        means = sorted(
            sum(rng.choice(deltas) for _ in range(n)) / n for _ in range(resamples)
        )
        tail = (1.0 - confidence) / 2
        low = means[int(tail * (resamples - 1))]
        high = means[int((1.0 - tail) * (resamples - 1))]
    return PairedContinuous(
        metric=metric,
        n=n,
        better=sum(1 for delta in deltas if delta > 0),
        worse=sum(1 for delta in deltas if delta < 0),
        ties=sum(1 for delta in deltas if delta == 0),
        baseline_mean=sum(baseline[case] for case in shared) / n if n else 0.0,
        variant_mean=sum(variant[case] for case in shared) / n if n else 0.0,
        mean_delta=mean_delta,
        ci_low=low,
        ci_high=high,
    )


def render_paired(
    binary: Sequence[PairedBinary], continuous: Sequence[PairedContinuous]
) -> list[str]:
    """The paired table: per-case wins and losses beside the aggregate delta."""
    lines = [
        "| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in binary:
        lines.append(
            f"| {row.metric} | {row.n} | {row.baseline_rate:.3f} | {row.variant_rate:.3f} | "
            f"{row.delta:+.3f} | {row.wins} | {row.losses} | {row.ties} | "
            f"McNemar exact p = {row.p_value:.3f} |"
        )
    for row in continuous:
        lines.append(
            f"| {row.metric} | {row.n} | {row.baseline_mean:.3f} | {row.variant_mean:.3f} | "
            f"{row.mean_delta:+.3f} | {row.better} | {row.worse} | {row.ties} | "
            f"95% bootstrap CI [{row.ci_low:+.3f}, {row.ci_high:+.3f}] |"
        )
    return lines


__all__ = [
    "PairedBinary",
    "PairedContinuous",
    "mcnemar_exact_p",
    "paired_binary",
    "paired_bootstrap",
    "render_paired",
]
