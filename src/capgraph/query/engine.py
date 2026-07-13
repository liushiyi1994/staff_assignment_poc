"""End-to-end query: brief -> QueryResult. Also the CLI smoke test.

    uv run python -m capgraph.query.engine "Need two backend engineers with streaming experience"
"""
from __future__ import annotations

import sys
import time

from ..models import QueryResult, ShortlistResult
from ..pipeline.stage5_graph import get_driver
from .intent import parse_intent
from .rank import rerank, score_candidate
from .retrieve import expand, generate_candidates, known_specializations


def query(brief: str) -> QueryResult:
    driver = get_driver()
    timings: dict[str, float] = {}

    t0 = time.time()
    intent = parse_intent(brief, known_specializations(driver))
    timings["intent_ms"] = (time.time() - t0) * 1000

    shortlists = []
    for role in intent.roles:
        t1 = time.time()
        candidates = expand(generate_candidates(role, brief, driver), driver)
        candidates = [score_candidate(c, role) for c in candidates]
        timings[f"retrieve_{role.role}_ms"] = (time.time() - t1) * 1000

        t2 = time.time()
        ranking = rerank(brief, role, candidates)
        timings[f"rerank_{role.role}_ms"] = (time.time() - t2) * 1000
        shortlists.append(ShortlistResult(role=role, ranking=ranking))

    return QueryResult(brief=brief, intent=intent, shortlists=shortlists, timings_ms=timings)


def print_result(result: QueryResult) -> None:
    from rich.console import Console
    from rich.table import Table
    console = Console()
    for sl in result.shortlists:
        table = Table(title=f"Role: {sl.role.role} (need {sl.role.count})")
        for col in ["#", "Person", "Fit", "Score", "Reason", "Evidence"]:
            table.add_column(col)
        for i, p in enumerate(sl.ranking, 1):
            table.add_row(str(i), p.person_name, p.fit, f"{p.score:.2f}",
                          p.reason, ", ".join(p.evidence_ticket_keys[:4]))
        console.print(table)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('usage: python -m capgraph.query.engine "<brief>"')
    print_result(query(sys.argv[1]))
