"""Three baselines on the identical historical information budget.

Each ranks **the whole frozen roster** of its case and nothing else — a permutation,
not a shortlist — so candidate recall is 1.0 by construction and every Hit@K
difference against the graph system is a ranking difference, never a coverage one.
All three are deterministic and make no model call:

* :class:`Bm25Baseline` — BM25 Okapi over one concatenated pre-cutoff evidence
  document per person (the "aggregate to people" formulation). The index is built per
  roster, so IDF is computed inside the same comparison set the metric scores. The
  index itself lives in :mod:`capgraph.lexical`, shared with the query engine's
  lexical retrieval arm so the arm and the baseline cannot drift apart.
* :class:`VectorBaseline` — plain RAG: the same evidence text embedded per ticket with
  the same local model the graph uses, a person scored by their single nearest ticket.
  No graph, no structured filter, no re-rank.
* :class:`MostActiveBaseline` — pre-cutoff evidence-ticket count in the case's project.
  Query-independent by design: it answers "who does the most work here?", which is the
  heuristic a shortlist has to beat to be worth anything.

Ties break on person id everywhere, and people with no evidence document still appear
(scored zero, ordered last), so the output is always a full roster permutation.

The corpus is `capgraph.evidence.EvidenceView` — the Stage 1 buckets that Stage 2 read.
See that module for why "pre-cutoff resolved tickets" means retained evidence tickets.
"""
from __future__ import annotations

import time
from collections.abc import Sequence

import numpy as np

from ..evidence import EvidenceView, fingerprint
from ..lexical import PersonBm25Index
from .contracts import BenchmarkQueryContext, RankingOutput

BM25 = "bm25"
VECTOR_ONLY = "vector_only"
MOST_ACTIVE = "most_active"
BASELINE_SYSTEMS = (BM25, VECTOR_ONLY, MOST_ACTIVE)


def _ranked(scores: dict[str, float], roster: Sequence[str]) -> list[str]:
    """Every roster member, best score first, ties broken on person id.

    A roster member with no evidence at all ranks below every scored person rather
    than at zero: BM25 scores can legitimately be negative (a term present in every
    document carries no information), and an unmatchable person must not overtake a
    weakly matched one on that technicality.
    """
    unscored = float("-inf")
    return sorted(roster, key=lambda person_id: (-scores.get(person_id, unscored), person_id))


class Bm25Baseline:
    """BM25 over per-person pre-cutoff evidence documents, restricted to the roster."""

    name = BM25

    def __init__(self, view: EvidenceView, *, index: PersonBm25Index | None = None):
        self.view = view
        self.index = PersonBm25Index(view) if index is None else index

    def rank(self, context: BenchmarkQueryContext) -> RankingOutput:
        started = time.perf_counter()
        scores = self.index.scores(context.query_text, roster=context.eligible_roster)
        ranked = _ranked(scores, context.eligible_roster)
        return RankingOutput(
            ranked_ids=ranked,
            candidate_ids=list(context.eligible_roster),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class VectorBaseline:
    """Pure vector search over the same evidence text, aggregated to people by max."""

    name = VECTOR_ONLY

    def __init__(self, view: EvidenceView, *, embed_fn=None, vectors: np.ndarray | None = None):
        self.view = view
        self._embed_fn = embed_fn
        self._vectors = view.embeddings(embed_fn=embed_fn) if vectors is None else vectors
        self._slices: dict[tuple[str, ...], tuple[np.ndarray, list[str]]] = {}

    def _embed(self, text: str) -> np.ndarray:
        embed_fn = self._embed_fn
        if embed_fn is None:
            from ..embeddings import embed as embed_fn
        return np.asarray(embed_fn([text])[0], dtype=np.float32)

    def _slice(self, roster: Sequence[str]) -> tuple[np.ndarray, list[str]]:
        """The roster's ticket vectors and the owner of each, cached per roster."""
        key = fingerprint(roster)
        cached = self._slices.get(key)
        if cached is None:
            indexes = self.view.ticket_indexes_for(key)
            owners = [self.view.tickets[index].person_id for index in indexes]
            cached = (self._vectors[indexes], owners)
            self._slices[key] = cached
        return cached

    def rank(self, context: BenchmarkQueryContext) -> RankingOutput:
        started = time.perf_counter()
        vectors, owners = self._slice(context.eligible_roster)
        scores: dict[str, float] = {}
        if len(owners):
            # Both sides are L2-normalized by embeddings.embed, so a dot product is
            # the cosine similarity.
            similarities = vectors @ self._embed(context.query_text)
            for person_id, similarity in zip(owners, similarities, strict=True):
                value = float(similarity)
                if value > scores.get(person_id, float("-inf")):
                    scores[person_id] = value
        ranked = _ranked(scores, context.eligible_roster)
        return RankingOutput(
            ranked_ids=ranked,
            candidate_ids=list(context.eligible_roster),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class MostActiveBaseline:
    """Roster members by pre-cutoff evidence-ticket count. Ignores the brief entirely."""

    name = MOST_ACTIVE

    def __init__(self, view: EvidenceView):
        self.view = view

    def rank(self, context: BenchmarkQueryContext) -> RankingOutput:
        started = time.perf_counter()
        counts = self.view.ticket_counts
        scores = {person_id: float(counts.get(person_id, 0)) for person_id in
                  context.eligible_roster}
        return RankingOutput(
            ranked_ids=_ranked(scores, context.eligible_roster),
            candidate_ids=list(context.eligible_roster),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def build_baselines(
    view: EvidenceView, *, names: Sequence[str] = BASELINE_SYSTEMS, embed_fn=None
) -> dict[str, object]:
    """Instantiate the requested baselines, building only the indexes they need."""
    builders = {
        BM25: lambda: Bm25Baseline(view),
        VECTOR_ONLY: lambda: VectorBaseline(view, embed_fn=embed_fn),
        MOST_ACTIVE: lambda: MostActiveBaseline(view),
    }
    unknown = sorted(set(names) - set(builders))
    if unknown:
        raise ValueError(f"unknown baseline(s): {', '.join(unknown)}")
    return {name: builders[name]() for name in names}
