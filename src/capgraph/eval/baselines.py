"""Baselines for the eval. Each returns a ranked list of person_ids for a brief.

All baselines use ONLY pre-cutoff data (same information budget as the real system).

  bm25_rank(brief)      — BM25 over one concatenated pre-cutoff ticket-text doc per person
  vector_rank(brief)    — embed brief, cosine vs mean contribution embedding per person
                          (no graph, no structured filters, no rerank)
  most_active_rank()    — people by pre-cutoff resolved ticket count (query-independent)

TODO(claude-code): implement per implementation plan Task 7. Cache per-person docs and
embeddings to data/eval/ so eval runs are fast.
"""
from __future__ import annotations


def bm25_rank(brief: str) -> list[str]:
    raise NotImplementedError


def vector_rank(brief: str) -> list[str]:
    raise NotImplementedError


def most_active_rank() -> list[str]:
    raise NotImplementedError
