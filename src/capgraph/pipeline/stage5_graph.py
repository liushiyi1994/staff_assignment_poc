"""Stage 5: load Neo4j from normalized.jsonl + capabilities.jsonl + parquet metadata.

- applies graph/schema.cypher (idempotent)
- upserts Person / Project / Contribution / Skill / Specialization nodes and edges
- embeds contribution summaries -> Contribution.embedding (vector index)
- --reset drops all capgraph nodes first

Loading pattern: batched UNWIND upserts (MERGE), 500 rows per transaction.
TODO(claude-code): implement per docs/implementation-plan.md Task 5. Also derive
COLLABORATED_WITH edges: co-occurrence of two people on same project+period, weight =
overlapping periods count (compute in pandas from buckets, load as edge list).
"""
from __future__ import annotations

import argparse

from neo4j import GraphDatabase

from ..settings import REPO_ROOT, settings


def get_driver():
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def apply_schema(driver) -> None:
    cypher = (REPO_ROOT / "src" / "capgraph" / "graph" / "schema.cypher").read_text()
    with driver.session() as session:
        for stmt in [s.strip() for s in cypher.split(";") if s.strip() and not s.strip().startswith("//")]:
            session.run(stmt)


def reset(driver) -> None:
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def load(driver) -> None:
    raise NotImplementedError("See module docstring and implementation plan Task 5")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    drv = get_driver()
    apply_schema(drv)
    if args.reset:
        reset(drv)
    load(drv)
