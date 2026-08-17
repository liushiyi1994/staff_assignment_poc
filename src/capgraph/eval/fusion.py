"""Rank combinators for the benchmark v2 experiments: RRF fusion and roster backstop.

Both operate on *rankings* — sequences of person ids — and nothing else, so they can be
applied to checkpointed v1 output without re-running or re-spending anything. Keeping
them pure is what makes the v2 experiments auditable: the same function that produced a
reported number can be exercised on three-element toy lists in a unit test.

* :func:`reciprocal_rank_fusion` — standard RRF, ``score(p) = sum_r w_r / (k + rank_r(p))``
  over the rankings that contain ``p``. ``k`` damps the head of each list: small ``k``
  makes rank 1 dominate, large ``k`` flattens the lists toward equal votes. Weights let
  one arm count for more than another, which matters when the arms are not equally good.
* :func:`roster_backstop` — append the roster members a system never retrieved, so its
  candidate pool is the whole roster by construction. It can only ever *extend* a
  ranking: everything already ranked keeps its position, so Hit@K can only rise, and
  rises only when the appended tail reaches into the first K.

Ties break on person id everywhere, so a re-run reproduces a ranking exactly.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

__all__ = ["dedupe", "reciprocal_rank_fusion", "roster_backstop"]


def dedupe(values: Iterable[str]) -> list[str]:
    """First occurrence wins, order preserved — the ranking convention everywhere."""
    return list(dict.fromkeys(str(value) for value in values))


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    k: int,
    weights: Sequence[float] | None = None,
) -> list[str]:
    """Fuse several rankings into one by reciprocal rank.

    ``k`` must be positive: at ``k = 0`` the first-placed item of any list would score
    ``1/0``. A person absent from a ranking scores nothing from it rather than being
    charged a worst-possible rank — the lists are of different lengths by nature (a
    retrieval union is not a roster permutation), and imputing a rank would penalise
    exactly the candidates fusion exists to rescue.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights must have one entry per ranking")
    if any(weight < 0 for weight in weights):
        raise ValueError("RRF weights must not be negative")

    scores: dict[str, float] = defaultdict(float)
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, person_id in enumerate(dedupe(ranking), 1):
            scores[person_id] += weight / (k + rank)
    return [person_id for person_id, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def roster_backstop(
    ranked: Sequence[str],
    roster: Sequence[str],
    *,
    tail_order: Sequence[str] | None = None,
) -> list[str]:
    """Append every roster member the ranking omitted, below everything it ranked.

    ``tail_order`` gives the appended members their order — pass a deterministic
    ranking (a score order, a baseline's permutation) to make the tail informative.
    Anyone it does not mention follows in person-id order, so the result is a full
    roster permutation whatever the caller supplies.

    A ranking containing someone outside the roster is a leakage failure elsewhere;
    this function does not silently drop them, it refuses.
    """
    eligible = {str(person_id) for person_id in roster}
    head = dedupe(ranked)
    outside = sorted(set(head) - eligible)
    if outside:
        raise ValueError(f"ranking leaves the roster: {outside}")

    missing = eligible - set(head)
    preferred = [p for p in dedupe(tail_order or ()) if p in missing]
    return head + preferred + sorted(missing - set(preferred))
