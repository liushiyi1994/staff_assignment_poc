"""What a benchmark ranker is handed, and what it may hand back.

Kept in its own module so the metric code, the baselines, and the graph-system runner
can share one definition without importing each other.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RankingOutput:
    """Optional rich output from a ranker used by the benchmark harness."""

    ranked_ids: Sequence[str]
    candidate_ids: Sequence[str] | None = None
    latency_ms: float | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class BenchmarkQueryContext:
    """Truth-free inputs a temporally aware ranker is allowed to consume."""

    issue_id: str
    query_text: str
    as_of_time: datetime
    project_key: str
    eligible_roster: tuple[str, ...]
