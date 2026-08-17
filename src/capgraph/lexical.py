"""BM25 over per-person pre-cutoff evidence documents: one implementation, two callers.

The benchmark's ``bm25`` baseline and the query engine's lexical retrieval arm must be
the *same* retriever, not two that happen to agree today. Both are built from this
module, so the arm the engine unions into its candidate pool is exactly the ranking the
baseline is scored on, tokenizer, parameters, corpus, and tie-breaks included.

Temporal discipline comes from the corpus itself, not from this module: the
:class:`~capgraph.evidence.EvidenceView` is the Stage 1 bucket view, whose tickets are
resolved strictly before the holdout cutoff, and every benchmark query time is after
that cutoff. There is therefore no as-of parameter here — the whole index is admissible
for every case, which is the same argument the baselines rely on.

An index is built per roster, so IDF is computed inside whatever comparison set the
caller is ranking within. That is the property the baseline documents, and the arm
inherits it.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from .evidence import EvidenceView, fingerprint
from .settings import settings

# Words, versions, and the punctuation that carries meaning in this corpus ("c++",
# "node.js", "log4j-2"). Deliberately plain: a cleverer analyzer would make the
# baseline harder to reproduce without making it a better control.
TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9+.#_-]*")


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


class PersonBm25Index:
    """BM25 Okapi over one concatenated pre-cutoff evidence document per person.

    Indexes are cached per roster fingerprint: a benchmark run touches one roster per
    project, so the corpus is tokenized five times rather than once per case.
    """

    def __init__(self, view: EvidenceView):
        self.view = view
        self._indexes: dict[tuple[str, ...], tuple[object, list[str]]] = {}

    def _index(self, roster: Sequence[str] | None):
        key = fingerprint(self.view.person_ids if roster is None else roster)
        cached = self._indexes.get(key)
        if cached is None:
            from rank_bm25 import BM25Okapi

            documents = self.view.documents
            people = [person_id for person_id in key if documents.get(person_id)]
            corpus = [tokenize(documents[person_id]) for person_id in people]
            index = (
                BM25Okapi(
                    corpus,
                    k1=float(settings["eval.baselines.bm25_k1"]),
                    b=float(settings["eval.baselines.bm25_b"]),
                )
                if corpus
                else None
            )
            cached = (index, people)
            self._indexes[key] = cached
        return cached

    def scores(self, query: str, *, roster: Sequence[str] | None = None) -> dict[str, float]:
        """BM25 score per person who has an evidence document in the index."""
        index, people = self._index(roster)
        if index is None:
            return {}
        return {
            person_id: float(score)
            for person_id, score in zip(people, index.get_scores(tokenize(query)), strict=True)
        }

    def top_people(
        self, query: str, *, k: int, roster: Sequence[str] | None = None
    ) -> list[tuple[str, float]]:
        """The ``k`` best-scoring people, ties broken on person id.

        People with no evidence document are absent rather than scored zero: BM25 scores
        can legitimately be negative, so an unmatchable person must not overtake a weakly
        matched one on that technicality. Same rule as the baseline's ordering.
        """
        if k < 1:
            return []
        scored = self.scores(query, roster=roster)
        ordered = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        return ordered[:k]


@lru_cache(maxsize=1)
def default_person_index() -> PersonBm25Index:
    """The process-wide index over the Stage 1 evidence view.

    Loading the bucket file takes a few seconds, so this is a once-per-process startup
    cost like the embedding model. Callers that time queries should warm it first.
    """
    return PersonBm25Index(EvidenceView.load())


__all__ = [
    "TOKEN_PATTERN",
    "PersonBm25Index",
    "default_person_index",
    "tokenize",
]
