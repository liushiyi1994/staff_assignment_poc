"""End-to-end query: brief -> QueryResult. Also the CLI smoke test.

    uv run python -m capgraph.query.engine "Need two backend engineers with streaming experience"

Every call the engine makes is logged and budgeted under ``llm.query_stage`` (or
``--stage``), so a smoke run's spend is separable from an eval run's in
``data/llm_costs.jsonl``.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime

from ..llm import CostControlError
from ..models import CandidateProfile, QueryResult, RoleSpec, ShortlistResult
from ..pipeline.stage5_graph import get_driver
from ..settings import settings
from .intent import parse_intent
from .rank import finish, rerank, rerank_input, score_candidate, split_by_count
from .retrieve import (
    ARM_LEXICAL,
    ARM_STRUCTURED,
    ARM_VECTOR,
    TermResolution,
    expand,
    generate_candidates,
    known_specializations,
    resolve_role_terms,
)


class GraphUnavailableError(RuntimeError):
    """Neo4j could not be reached — a service problem, not a query problem."""


def connected_driver():
    """A driver that has actually reached the database, or a legible failure."""
    driver = get_driver()
    try:
        driver.verify_connectivity()
    except Exception as error:                       # any driver/service failure
        driver.close()
        raise GraphUnavailableError(
            f"Neo4j is not reachable at {settings.neo4j_uri} ({error}). "
            "Start it with `make db-up`, then load the graph with `make stage5`."
        ) from error
    return driver


def retrieve_role(
    role: RoleSpec,
    brief: str,
    driver,
    *,
    roster: Sequence[str] | None = None,
    as_of: date | datetime | None = None,
    lexical_index=None,
) -> tuple[TermResolution, list[CandidateProfile]]:
    """One role's retrieval: resolve its terms, union the arms, expand the survivors.

    Factored out of :func:`query` so an offline experiment can capture exactly what the
    engine retrieves without re-implementing the sequence and drifting from it.
    """
    resolution = resolve_role_terms(role, driver)
    candidates = generate_candidates(
        role,
        brief,
        driver,
        resolution=resolution,
        roster=roster,
        as_of=as_of,
        lexical_index=lexical_index,
    )
    return resolution, expand(candidates, driver, resolution=resolution, as_of=as_of)


def query(
    brief: str,
    driver,
    *,
    stage: str | None = None,
    roster: Sequence[str] | None = None,
    as_of: date | datetime | None = None,
    lexical_index=None,
) -> QueryResult:
    """Brief -> intent -> candidates (vector ∪ structured ∪ lexical) -> score -> re-rank.

    ``roster`` and ``as_of`` exist for the temporal benchmark and are inert otherwise:
    they restrict every retrieval arm to a frozen candidate set and measure recency at
    the case's query time rather than at the graph's cutoff (see query/retrieve.py).

    ``lexical_index`` lets a caller that times queries build the BM25 index once up
    front instead of paying its load inside the first case; when omitted the arm
    builds (and caches) the process-wide default the first time it is asked for.
    """
    timings: dict[str, float] = {}

    started = time.perf_counter()
    intent = parse_intent(brief, known_specializations(driver), stage=stage)
    timings["intent_ms"] = round((time.perf_counter() - started) * 1000, 1)

    shortlists = []
    for index, role in enumerate(intent.roles):
        label = f"{index}:{role.role}"

        started = time.perf_counter()
        resolution, candidates = retrieve_role(
            role, brief, driver, roster=roster, as_of=as_of, lexical_index=lexical_index
        )
        for candidate in candidates:
            score_candidate(candidate, role, resolution)
        scored = sorted(candidates, key=lambda c: (-c.score, c.person_id))
        shortlist = rerank_input(candidates)
        timings[f"retrieve_{label}_ms"] = round((time.perf_counter() - started) * 1000, 1)

        started = time.perf_counter()
        ranking, rejected = rerank(brief, role, candidates, stage=stage)
        timings[f"rerank_{label}_ms"] = round((time.perf_counter() - started) * 1000, 1)

        # Named under the same "rerank_" prefix as the call above: both are LLM
        # ordering work, and the benchmark's score-only ablation subtracts that whole
        # prefix to report the latency a re-rank-free system would have had.
        started = time.perf_counter()
        ranking, finisher_rejected = finish(brief, role, ranking, candidates, stage=stage)
        timings[f"rerank_finisher_{label}_ms"] = round(
            (time.perf_counter() - started) * 1000, 1
        )
        rejected = [*rejected, *finisher_rejected]

        # Backlog G8: the role asked for `count` people and until now nothing read it.
        # Partitioning the final ranking is presentation, not ranking — every person is
        # still there, in the same order.
        proposed, alternates = split_by_count(ranking, role.count)

        shortlists.append(
            ShortlistResult(
                role=role,
                ranking=ranking,
                rejected=rejected,
                proposed_person_ids=proposed,
                alternate_person_ids=alternates,
                candidate_counts={
                    "vector": sum(ARM_VECTOR in c.retrieval_sources for c in candidates),
                    "structured": sum(
                        ARM_STRUCTURED in c.retrieval_sources for c in candidates
                    ),
                    "lexical": sum(ARM_LEXICAL in c.retrieval_sources for c in candidates),
                    "vector_only": sum(c.retrieval_sources == [ARM_VECTOR] for c in candidates),
                    "lexical_only": sum(
                        c.retrieval_sources == [ARM_LEXICAL] for c in candidates
                    ),
                    "union": len(candidates),
                    "reranked": len(shortlist),
                    "shortlisted": len(ranking),
                },
                resolved_terms={**resolution.specializations, **resolution.skills},
                candidate_person_ids=[c.person_id for c in candidates],
                scored_person_ids=[c.person_id for c in scored],
            )
        )

    timings["total_ms"] = round(sum(timings.values()), 1)
    return QueryResult(brief=brief, intent=intent, shortlists=shortlists, timings_ms=timings)


def print_result(result: QueryResult) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print(f"[bold]brief:[/bold] {result.brief}")
    console.print(
        f"[dim]domain:[/dim] {result.intent.domain or '-'}  "
        f"[dim]roles:[/dim] {len(result.intent.roles)}"
    )
    for shortlist in result.shortlists:
        role = shortlist.role
        console.print(
            f"\n[bold]role:[/bold] {role.role} (need {role.count})  "
            f"[dim]specializations:[/dim] {', '.join(role.specializations) or '-'}  "
            f"[dim]skills:[/dim] {', '.join(role.skills) or '-'}"
        )
        counts = shortlist.candidate_counts
        console.print(
            "[dim]candidates:[/dim] "
            + ", ".join(f"{key}={value}" for key, value in counts.items())
        )
        # Backlog G8: the role asked for `count` people, so say which ones those are.
        by_id = {person.person_id: person for person in shortlist.ranking}
        console.print(
            "[dim]proposed:[/dim] "
            + (
                ", ".join(
                    by_id[person_id].person_name
                    for person_id in shortlist.proposed_person_ids
                    if person_id in by_id
                )
                or "-"
            )
            + f"  [dim]alternates:[/dim] {len(shortlist.alternate_person_ids)}"
        )
        table = Table(show_lines=True)
        for column in ["#", "Person", "Score", "Fit", "Found by", "Matched", "Reason",
                       "Evidence"]:
            table.add_column(column)
        for rank, person in enumerate(shortlist.ranking, 1):
            matched = person.matched_specializations + person.matched_skills
            table.add_row(
                str(rank),
                person.person_name,
                f"{person.score:.3f}",
                person.fit,
                "+".join(person.found_by),
                ", ".join(matched) or "-",
                person.reason,
                ", ".join(person.evidence_ticket_keys),
            )
        console.print(table)
        if shortlist.rejected:
            console.print("[yellow]rejected re-rank entries:[/yellow]")
            for problem in shortlist.rejected:
                console.print(f"  - {problem}")
    console.print(
        "\n[dim]timings:[/dim] "
        + ", ".join(f"{key}={value}" for key, value in result.timings_ms.items())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the capability graph")
    parser.add_argument("brief", help="natural-language staffing brief")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--stage",
        default=None,
        help="cost-log stage name (default: llm.query_stage in settings)",
    )
    args = parser.parse_args(argv)

    try:
        driver = connected_driver()
    except GraphUnavailableError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        result = query(args.brief, driver, stage=args.stage)
    except CostControlError as error:
        print(f"refused before calling the model: {error}", file=sys.stderr)
        return 3
    finally:
        driver.close()

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
