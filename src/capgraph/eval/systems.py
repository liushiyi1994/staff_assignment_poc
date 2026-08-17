"""The graph system under test, scored twice from a single run.

One :func:`capgraph.query.engine.query` call per case produces both reported systems:

* ``capgraph_score`` — the deterministic weighted score alone, no re-rank.
* ``capgraph_full`` — the same pool with the LLM re-rank applied to its top-K.

The two lists are identical past the re-ranked prefix, so their difference isolates
exactly what the LLM adds. Be precise about the label: ``capgraph_score`` still needs
the intent parse (one LLM call) to know what to retrieve — the ablation removes the
re-rank, not every model call. Cost is reported per call type from the cost log, so
the intent share and the re-rank share are separable rather than asserted.

Where the re-rank returns fewer entries than it was given — a rejected citation, an
omitted person — the remaining shortlisted candidates are appended in deterministic
score order. Dropping them would shrink the pool the metric sees and turn a re-rank
refusal into a coverage difference; the ablation would then no longer be measuring
ordering alone. The rejects are counted and reported separately.

Both temporal guards are applied here and nowhere else in the eval path: the case's
frozen roster restricts both retrieval arms, and the case's as-of time is what recency
decay is measured from.
"""
from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

from ..models import QueryResult
from ..query.engine import query
from ..settings import settings
from .contracts import BenchmarkQueryContext, RankingOutput
from .costs import CostMeter

CAPGRAPH_FULL = "capgraph_full"
CAPGRAPH_SCORE = "capgraph_score"
GRAPH_SYSTEMS = (CAPGRAPH_FULL, CAPGRAPH_SCORE)

INTENT_PURPOSE = "intent"
RERANK_PURPOSE = "rerank"


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def round_robin(orderings: Sequence[Sequence[str]]) -> list[str]:
    """Interleave several role rankings into one list, keeping each role's order.

    A brief that parses into two roles produces two shortlists; the benchmark's truth
    is a single person, so they have to become one ranking. Round robin is the only
    merge that does not privilege whichever role the parser happened to emit first.
    """
    merged: list[str] = []
    for position in range(max((len(order) for order in orderings), default=0)):
        for order in orderings:
            if position < len(order):
                merged.append(str(order[position]))
    return _dedupe(merged)


def full_ordering(result: QueryResult) -> list[str]:
    """Re-rank order per role, each padded with its own deterministic remainder."""
    return round_robin(
        [
            _dedupe([person.person_id for person in shortlist.ranking])
            + _dedupe(shortlist.scored_person_ids)
            for shortlist in result.shortlists
        ]
    )


def score_ordering(result: QueryResult) -> list[str]:
    """Deterministic weighted-score order per role, merged the same way."""
    return round_robin([_dedupe(s.scored_person_ids) for s in result.shortlists])


def candidate_pool(result: QueryResult) -> list[str]:
    """Every person either arm retrieved, across roles: the pre-ranking pool."""
    return _dedupe(
        person_id
        for shortlist in result.shortlists
        for person_id in shortlist.candidate_person_ids
    )


def run_detail(result: QueryResult) -> dict[str, object]:
    """Per-case diagnostics kept in the checkpoint: what retrieval and the LLM did."""
    return {
        "roles": [shortlist.role.role for shortlist in result.shortlists],
        "candidate_counts": [shortlist.candidate_counts for shortlist in result.shortlists],
        "rejected": [problem for s in result.shortlists for problem in s.rejected],
        "n_ranked_by_rerank": sum(len(s.ranking) for s in result.shortlists),
        "timings_ms": result.timings_ms,
    }


class CapGraphSystem:
    """Runs one benchmark case through the query engine under its temporal guards."""

    def __init__(
        self,
        driver,
        *,
        stage: str | None = None,
        meter: CostMeter | None = None,
        lexical_index=None,
    ):
        self.driver = driver
        self.stage = stage or str(settings["eval.stage_name"])
        self.meter = CostMeter() if meter is None else meter
        # Built once by the runner, like the embedding model, so the lexical arm's
        # corpus load is not billed to the first case's latency.
        self.lexical_index = lexical_index

    def run(self, context: BenchmarkQueryContext) -> tuple[dict[str, RankingOutput], dict]:
        """Return {system: RankingOutput} plus diagnostics for one case."""
        self.meter.mark()
        started = time.perf_counter()
        result = query(
            context.query_text,
            self.driver,
            stage=self.stage,
            roster=context.eligible_roster,
            as_of=context.as_of_time,
            lexical_index=self.lexical_index,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        spend = self.meter.spend_since(stage=self.stage)

        pool = candidate_pool(result)
        # The score-only arm carries the same retrieval latency but not the re-rank's,
        # so the ablation reports the wall clock a re-rank-free system would have. Every
        # LLM ordering step is named "rerank_*" for exactly this subtraction — the
        # benchmark-v3 finisher included.
        rerank_ms = sum(
            value for key, value in result.timings_ms.items() if key.startswith("rerank_")
        )
        outputs = {
            CAPGRAPH_FULL: RankingOutput(
                ranked_ids=full_ordering(result),
                candidate_ids=pool,
                latency_ms=elapsed_ms,
                cost_usd=spend.total,
            ),
            CAPGRAPH_SCORE: RankingOutput(
                ranked_ids=score_ordering(result),
                candidate_ids=pool,
                latency_ms=max(elapsed_ms - rerank_ms, 0.0),
                cost_usd=spend.by_purpose.get(INTENT_PURPOSE, 0.0),
            ),
        }
        detail = run_detail(result)
        detail["cost_usd_by_purpose"] = dict(sorted(spend.by_purpose.items()))
        detail["n_llm_calls"] = spend.n_calls
        return outputs, detail
