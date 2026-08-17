"""Stage 5: buckets.jsonl + normalized.jsonl + terms.jsonl + capabilities.jsonl -> Neo4j.

Builds the persistent capability graph of `docs/tech-design.md` §4: Person, Project,
Contribution, Skill and Specialization nodes, the edges between them, and a native
vector index over contribution summaries.

Three properties of this loader are load-bearing:

- **Raw tickets never reach Neo4j.** `buckets.jsonl` is read for bucket *metadata*
  only (`_bucket_refs` drops the ticket list on the way in), and a Contribution
  carries `evidence_ticket_keys` — pointers back into `data/parquet/` — not ticket
  text. `assert_no_ticket_payload` makes that a checked invariant, not a convention.
- **Every write is a MERGE on a stable key**, so a second run updates in place:
  identical node and relationship counts, no duplicates. The graph is the checkpoint;
  only the embeddings have a separate on-disk cache (`--force` recomputes them).
- **Row building is pure and offline.** Every `build_*` function maps parsed
  artifacts to plain dicts that are handed to UNWIND, so batch construction and
  property mapping are testable without a database.

`COLLABORATED_WITH` is derived from bucket co-occurrence (same project, same quarter)
and is deliberately named `basis="co_presence_same_project_period"` on the edge: two
people appearing in the same project-quarter is co-presence, not verified
collaboration. Nothing in scoring reads it.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from ..models import CanonicalTerm, Contribution, PersonCapability
from ..settings import DATA_DIR, REPO_ROOT, settings
from .stage4_project import PERIOD_PATTERN, period_end

BUCKETS_PATH = DATA_DIR / "buckets" / "buckets.jsonl"
NORM_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
TERMS_PATH = DATA_DIR / "contributions" / "terms.jsonl"
CAPS_PATH = DATA_DIR / "contributions" / "capabilities.jsonl"
EMBEDDINGS_PATH = DATA_DIR / "contributions" / "contribution_embeddings.npz"
SCHEMA_PATH = REPO_ROOT / "src" / "capgraph" / "graph" / "schema.cypher"

BATCH_SIZE = 500
VECTOR_INDEX = "contribution_embedding"

NODE_LABELS = ("Person", "Project", "Contribution", "Skill", "Specialization")
RELATIONSHIP_TYPES = (
    "MADE",
    "ON",
    "DEMONSTRATES",
    "HAS_SKILL",
    "HAS_SPECIALIZATION",
    "COLLABORATED_WITH",
)

# Co-presence is not collaboration. The edge says so in the data, not only in docs.
COLLABORATION_BASIS = "co_presence_same_project_period"

# Ticket-shaped keys that must never appear in a row handed to Neo4j.
TICKET_PAYLOAD_KEYS = frozenset(
    {"tickets", "summary_text", "description", "components", "labels", "resolution"}
)


# ---------- Cypher ----------

MERGE_PERSON = """
UNWIND $rows AS row
MERGE (p:Person {id: row.id})
SET p.pseudonym = row.pseudonym,
    p.project_key = row.project_key,
    p.active_from = row.active_from,
    p.active_to = row.active_to
"""

MERGE_PROJECT = """
UNWIND $rows AS row
MERGE (pr:Project {key: row.key})
SET pr.domain = row.domain
"""

MERGE_CONTRIBUTION = """
UNWIND $rows AS row
MERGE (c:Contribution {id: row.id})
SET c.summary = row.summary,
    c.period = row.period,
    c.confidence = row.confidence,
    c.evidence_ticket_keys = row.evidence_ticket_keys
WITH c, row
CALL db.create.setNodeVectorProperty(c, 'embedding', row.embedding)
"""

MERGE_TERM = """
UNWIND $rows AS row
MERGE (t:{label} {{name: row.name}})
SET t.aliases = row.aliases
"""

MERGE_MADE = """
UNWIND $rows AS row
MATCH (p:Person {id: row.person_id})
MATCH (c:Contribution {id: row.contribution_id})
MERGE (p)-[:MADE]->(c)
"""

MERGE_ON = """
UNWIND $rows AS row
MATCH (c:Contribution {id: row.contribution_id})
MATCH (pr:Project {key: row.project_key})
MERGE (c)-[:ON]->(pr)
"""

# strength is set unconditionally (null for skills) so a re-run can never leave a
# stale value behind on an edge whose source record changed.
MERGE_DEMONSTRATES = """
UNWIND $rows AS row
MATCH (c:Contribution {{id: row.contribution_id}})
MATCH (t:{label} {{name: row.term}})
MERGE (c)-[d:DEMONSTRATES]->(t)
SET d.strength = row.strength
"""

# primary_evidence_count (backlog G6) is set unconditionally, and is 0 for skills, so a
# re-run cannot leave a stale value on an edge whose source contributions changed.
MERGE_CAPABILITY = """
UNWIND $rows AS row
MATCH (p:Person {{id: row.person_id}})
MATCH (t:{label} {{name: row.term}})
MERGE (p)-[h:{rel_type}]->(t)
SET h.evidence_count = row.evidence_count,
    h.last_used = row.last_used,
    h.decay_score = row.decay_score,
    h.primary_evidence_count = row.primary_evidence_count
"""

MERGE_COLLABORATED_WITH = """
UNWIND $rows AS row
MATCH (a:Person {id: row.person_id})
MATCH (b:Person {id: row.other_person_id})
MERGE (a)-[r:COLLABORATED_WITH]->(b)
SET r.periods_count = row.periods_count,
    r.basis = row.basis
"""

VECTOR_PROBE = """
CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score
MATCH (person:Person)-[:MADE]->(node)-[:ON]->(project:Project)
RETURN score, node.id AS contribution_id, person.pseudonym AS person,
       project.key AS project, node.period AS period, node.summary AS summary
ORDER BY score DESC
"""


# ---------- driver / schema ----------

def get_driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def schema_statements(cypher: str) -> list[str]:
    """Split schema.cypher into executable statements, dropping comment-only lines."""
    statements = []
    for block in cypher.split(";"):
        body = "\n".join(
            line for line in block.splitlines() if not line.strip().startswith("//")
        ).strip()
        if body:
            statements.append(body)
    return statements


def check_vector_index_dims(cypher: str) -> int:
    """Fail loudly if schema.cypher and embedding.dims have drifted apart.

    The index dimension is fixed at creation time, so a mismatch here would surface
    as an opaque write failure (or a silently unindexed property) thousands of rows
    into a load.
    """
    dims = int(settings["embedding.dims"])
    if f"`vector.dimensions`: {dims}" not in cypher:
        raise ValueError(
            f"{SCHEMA_PATH.name} does not declare `vector.dimensions`: {dims} "
            "(embedding.dims in config/settings.yaml). Changing the embedding model "
            "requires dropping and recreating the vector index."
        )
    return dims


def apply_schema(driver) -> None:
    cypher = SCHEMA_PATH.read_text(encoding="utf-8")
    check_vector_index_dims(cypher)
    with driver.session() as session:
        for statement in schema_statements(cypher):
            session.run(statement)


def reset(driver) -> None:
    """Drop every node (and therefore every relationship). Constraints/indexes stay."""
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


# ---------- inputs ----------

@dataclass(frozen=True)
class BucketRef:
    """Bucket metadata only. The ticket list is deliberately not carried here."""

    bucket_id: str
    person_id: str
    person_name: str
    project_key: str
    period: str


def _bucket_refs(lines: Iterable[str]) -> list[BucketRef]:
    """Parse bucket metadata, dropping ticket payloads at the boundary."""
    refs = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        refs.append(
            BucketRef(
                bucket_id=str(row["bucket_id"]),
                person_id=str(row["person_id"]),
                person_name=str(row["person_name"]),
                project_key=str(row["project_key"]),
                period=str(row["period"]),
            )
        )
    return refs


def read_buckets(path: Path | None = None) -> list[BucketRef]:
    with (BUCKETS_PATH if path is None else path).open(encoding="utf-8") as handle:
        return _bucket_refs(handle)


def read_contributions(path: Path | None = None) -> tuple[list[Contribution], int]:
    """Return loadable contributions and the number of skipped rows dropped."""
    with (NORM_PATH if path is None else path).open(encoding="utf-8") as handle:
        rows = [Contribution.model_validate_json(line) for line in handle if line.strip()]
    kept = [c for c in rows if not c.skip]
    return kept, len(rows) - len(kept)


def read_terms(path: Path | None = None) -> list[CanonicalTerm]:
    with (TERMS_PATH if path is None else path).open(encoding="utf-8") as handle:
        return [CanonicalTerm.model_validate_json(line) for line in handle if line.strip()]


def read_capabilities(path: Path | None = None) -> list[PersonCapability]:
    with (CAPS_PATH if path is None else path).open(encoding="utf-8") as handle:
        return [PersonCapability.model_validate_json(line) for line in handle if line.strip()]


# ---------- row building (pure) ----------

def period_start(period: str) -> date:
    """First calendar day represented by ``YYYY-QN`` (mirror of stage 4's period_end)."""
    match = PERIOD_PATTERN.match(period)
    if match is None:
        raise ValueError(f"expected a YYYY-QN quarter period, got {period!r}")
    year, quarter = int(match.group(1)), int(match.group(2))
    return date(year, quarter * 3 - 2, 1)


def build_people(buckets: Sequence[BucketRef]) -> list[dict[str, Any]]:
    """One row per person: pseudonym, project, and the span their buckets cover.

    Person IDs are project-qualified (`<project_key>:<user_id>`), so a person spanning
    two projects means the upstream identity contract broke; refuse rather than pick.
    """
    periods: dict[str, set[str]] = defaultdict(set)
    pseudonyms: dict[str, str] = {}
    projects: dict[str, set[str]] = defaultdict(set)
    for bucket in buckets:
        periods[bucket.person_id].add(bucket.period)
        pseudonyms[bucket.person_id] = bucket.person_name
        projects[bucket.person_id].add(bucket.project_key)

    rows = []
    for person_id in sorted(periods):
        keys = projects[person_id]
        if len(keys) > 1:
            raise ValueError(
                f"person {person_id} appears in multiple projects {sorted(keys)}; "
                "project-qualified identities must not be merged across projects"
            )
        quarters = sorted(periods[person_id])
        rows.append(
            {
                "id": person_id,
                "pseudonym": pseudonyms[person_id],
                "project_key": next(iter(keys)),
                "active_from": period_start(quarters[0]),
                "active_to": period_end(quarters[-1]),
            }
        )
    return rows


def build_projects(project_keys: Iterable[str]) -> list[dict[str, Any]]:
    """Project rows with the configured domain text (settings is the only source)."""
    domains = settings["dataset.project_domains"]
    if not isinstance(domains, Mapping):
        raise TypeError("dataset.project_domains must be a mapping of project key to domain")
    rows = []
    for key in sorted(set(project_keys)):
        domain = str(domains.get(key, "")).strip()
        if not domain:
            raise ValueError(f"missing non-empty dataset.project_domains entry for {key}")
        rows.append({"key": key, "domain": domain})
    return rows


def build_contributions(
    contribs: Sequence[Contribution], embeddings: Mapping[str, Sequence[float]]
) -> list[dict[str, Any]]:
    """Contribution rows carrying provenance pointers — never ticket text."""
    dims = int(settings["embedding.dims"])
    rows = []
    for c in contribs:
        vector = embeddings.get(c.contribution_id)
        if vector is None:
            raise KeyError(f"no embedding for contribution {c.contribution_id}")
        if len(vector) != dims:
            raise ValueError(
                f"contribution {c.contribution_id} embedding has {len(vector)} dims, "
                f"expected {dims} (embedding.dims)"
            )
        rows.append(
            {
                "id": c.contribution_id,
                "summary": c.contribution_summary,
                "period": c.period,
                "confidence": c.confidence,
                "evidence_ticket_keys": list(c.evidence_ticket_keys),
                "embedding": [float(value) for value in vector],
            }
        )
    return rows


def build_terms(terms: Sequence[CanonicalTerm], kind: str) -> list[dict[str, Any]]:
    return [
        {"name": term.canonical, "aliases": list(term.aliases)}
        for term in sorted(terms, key=lambda t: t.canonical)
        if term.kind == kind
    ]


def build_made(contribs: Sequence[Contribution]) -> list[dict[str, Any]]:
    return [
        {"person_id": c.person_id, "contribution_id": c.contribution_id} for c in contribs
    ]


def build_on(contribs: Sequence[Contribution]) -> list[dict[str, Any]]:
    return [
        {"contribution_id": c.contribution_id, "project_key": c.project_key} for c in contribs
    ]


def build_demonstrates(contribs: Sequence[Contribution], kind: str) -> list[dict[str, Any]]:
    """DEMONSTRATES rows for one term kind; skills carry a null strength."""
    rows = []
    for c in contribs:
        if kind == "specialization":
            rows += [
                {
                    "contribution_id": c.contribution_id,
                    "term": ref.name,
                    "strength": ref.strength,
                }
                for ref in c.specializations
            ]
        elif kind == "skill":
            rows += [
                {"contribution_id": c.contribution_id, "term": ref.name, "strength": None}
                for ref in c.skills
            ]
        else:
            raise ValueError(f"unknown term kind {kind!r}")
    return rows


def build_capabilities(caps: Sequence[PersonCapability], kind: str) -> list[dict[str, Any]]:
    """HAS_SKILL / HAS_SPECIALIZATION rows. Evidence stays traversable via MADE."""
    return [
        {
            "person_id": cap.person_id,
            "term": cap.term,
            "evidence_count": cap.evidence_count,
            "last_used": cap.last_used,
            "decay_score": cap.decay_score,
            "primary_evidence_count": cap.primary_evidence_count,
        }
        for cap in caps
        if cap.kind == kind
    ]


def build_collaborations(buckets: Sequence[BucketRef]) -> list[dict[str, Any]]:
    """Co-presence edges: two people with buckets in the same project and quarter.

    Buckets are Stage 1 output, so every period here is already wholly before the
    holdout cutoff. One row per unordered pair (lexicographically ordered ids) keeps
    the edge count stable across runs; read it undirected.
    """
    people_by_period: dict[tuple[str, str], set[str]] = defaultdict(set)
    for bucket in buckets:
        people_by_period[(bucket.project_key, bucket.period)].add(bucket.person_id)

    shared: dict[tuple[str, str], int] = defaultdict(int)
    for people in people_by_period.values():
        for pair in combinations(sorted(people), 2):
            shared[pair] += 1

    return [
        {
            "person_id": a,
            "other_person_id": b,
            "periods_count": count,
            "basis": COLLABORATION_BASIS,
        }
        for (a, b), count in sorted(shared.items())
    ]


def assert_no_ticket_payload(rows: Iterable[Mapping[str, Any]]) -> None:
    """Guard non-negotiable #2: no ticket-shaped payload crosses into Neo4j."""
    for row in rows:
        leaked = TICKET_PAYLOAD_KEYS.intersection(row)
        if leaked:
            raise ValueError(
                f"raw ticket payload must not be loaded into Neo4j (keys: {sorted(leaked)})"
            )


def batched(rows: Sequence[Any], size: int | None = None) -> Iterator[list[Any]]:
    """Yield fixed-size row batches; one transaction per batch."""
    size = BATCH_SIZE if size is None else size
    if size < 1:
        raise ValueError("batch size must be at least 1")
    for start in range(0, len(rows), size):
        yield list(rows[start:start + size])


# ---------- embeddings ----------

def _cached_embeddings(ids: Sequence[str], path: Path) -> dict[str, np.ndarray] | None:
    """Return the cache only if it matches these ids, the model, and the dimensions."""
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as archive:
        if str(archive["model"]) != str(settings["embedding.model"]):
            return None
        cached_ids = [str(value) for value in archive["ids"]]
        vectors = archive["vectors"]
        if vectors.ndim != 2 or vectors.shape[1] != int(settings["embedding.dims"]):
            return None
        cache = dict(zip(cached_ids, vectors, strict=True))
    return cache if set(ids) <= set(cache) else None


def embed_contributions(
    contribs: Sequence[Contribution],
    *,
    path: Path | None = None,
    force: bool = False,
    embed_fn=None,
) -> tuple[dict[str, np.ndarray], bool]:
    """Embed contribution summaries, reusing the on-disk cache when it still applies.

    Returns the vectors by contribution id and whether they were recomputed. The
    embedding model is deterministic, so the cache is a speed checkpoint, not a
    correctness one — ``--force`` discards it.
    """
    path = EMBEDDINGS_PATH if path is None else path
    ids = [c.contribution_id for c in contribs]
    if not force:
        cached = _cached_embeddings(ids, path)
        if cached is not None:
            return {contribution_id: cached[contribution_id] for contribution_id in ids}, False

    if embed_fn is None:
        from ..embeddings import embed as embed_fn

    vectors = np.asarray(embed_fn([c.contribution_summary for c in contribs]), dtype=np.float32)
    if vectors.shape != (len(ids), int(settings["embedding.dims"])):
        raise ValueError(
            f"embedding matrix has shape {vectors.shape}, expected "
            f"({len(ids)}, {int(settings['embedding.dims'])})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        ids=np.asarray(ids, dtype=str),
        vectors=vectors,
        model=np.asarray(str(settings["embedding.model"])),
    )
    return dict(zip(ids, vectors, strict=True)), True


# ---------- load ----------

def run_batches(driver, statement: str, rows: Sequence[Mapping[str, Any]]) -> int:
    """Execute one UNWIND statement over `rows`, BATCH_SIZE rows per transaction."""
    assert_no_ticket_payload(rows)
    written = 0
    with driver.session() as session:
        for batch in batched(rows):
            session.execute_write(lambda tx, batch=batch: tx.run(statement, rows=batch).consume())
            written += len(batch)
    return written


def graph_counts(driver) -> dict[str, int]:
    """Node counts per label and relationship counts per type, for the run log."""
    counts: dict[str, int] = {}
    with driver.session() as session:
        for label in NODE_LABELS:
            counts[label] = session.run(
                f"MATCH (n:{label}) RETURN count(n) AS total"
            ).single()["total"]
        for rel_type in RELATIONSHIP_TYPES:
            counts[rel_type] = session.run(
                f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS total"
            ).single()["total"]
    return counts


def format_counts(counts: Mapping[str, int]) -> str:
    lines = ["| element | kind | count |", "|---|---|---|"]
    lines += [f"| {label} | node | {counts[label]} |" for label in NODE_LABELS]
    lines += [f"| {rel} | relationship | {counts[rel]} |" for rel in RELATIONSHIP_TYPES]
    return "\n".join(lines)


def load(
    driver,
    *,
    force_embeddings: bool = False,
    buckets_path: Path | None = None,
    contributions_path: Path | None = None,
    terms_path: Path | None = None,
    capabilities_path: Path | None = None,
    embeddings_path: Path | None = None,
) -> dict[str, int]:
    """Upsert the whole graph. Idempotent: every write MERGEs on a stable key.

    Every input path defaults to the production artifact, so an ordinary ``make stage5``
    is unchanged. They are parameters so a study can load a *second* vocabulary into a
    *separate* database through this exact loader — the G3a sweep of
    ``docs/work-orders/deterministic-sweeps.md`` does that, and a hand-rolled copy of
    the step list below is precisely the drift the pinning rule exists to prevent.
    ``embeddings_path`` normally stays the production cache: Stage 3 rewrites term names
    and never the contribution summaries, so the vectors are the same vectors and
    sharing them keeps the vector arm identical across vocabularies.
    """
    embeddings_path = EMBEDDINGS_PATH if embeddings_path is None else embeddings_path
    buckets = read_buckets(buckets_path)
    contribs, n_skipped = read_contributions(contributions_path)
    terms = read_terms(terms_path)
    caps = read_capabilities(capabilities_path)
    print(
        f"inputs: {len(buckets)} buckets, {len(contribs)} contributions "
        f"({n_skipped} skipped), {len(terms)} canonical terms, {len(caps)} capability edges"
    )

    vectors, recomputed = embed_contributions(
        contribs, path=embeddings_path, force=force_embeddings
    )
    print(
        f"embeddings: {len(vectors)} x {int(settings['embedding.dims'])} dims "
        f"({'computed' if recomputed else 'reused from'} {embeddings_path.name})"
    )

    people = build_people(buckets)
    steps: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("Person", MERGE_PERSON, people),
        ("Project", MERGE_PROJECT, build_projects(row["project_key"] for row in people)),
        ("Contribution", MERGE_CONTRIBUTION, build_contributions(contribs, vectors)),
        ("Skill", MERGE_TERM.format(label="Skill"), build_terms(terms, "skill")),
        (
            "Specialization",
            MERGE_TERM.format(label="Specialization"),
            build_terms(terms, "specialization"),
        ),
        ("MADE", MERGE_MADE, build_made(contribs)),
        ("ON", MERGE_ON, build_on(contribs)),
        (
            "DEMONSTRATES (skill)",
            MERGE_DEMONSTRATES.format(label="Skill"),
            build_demonstrates(contribs, "skill"),
        ),
        (
            "DEMONSTRATES (specialization)",
            MERGE_DEMONSTRATES.format(label="Specialization"),
            build_demonstrates(contribs, "specialization"),
        ),
        (
            "HAS_SKILL",
            MERGE_CAPABILITY.format(label="Skill", rel_type="HAS_SKILL"),
            build_capabilities(caps, "skill"),
        ),
        (
            "HAS_SPECIALIZATION",
            MERGE_CAPABILITY.format(label="Specialization", rel_type="HAS_SPECIALIZATION"),
            build_capabilities(caps, "specialization"),
        ),
        ("COLLABORATED_WITH", MERGE_COLLABORATED_WITH, build_collaborations(buckets)),
    ]
    for name, statement, rows in steps:
        print(f"  {name}: {run_batches(driver, statement, rows)} rows upserted")

    counts = graph_counts(driver)
    print(format_counts(counts))
    return counts


def probe(driver, text: str, k: int = 5) -> list[dict[str, Any]]:
    """Hand-written vector probe against the Contribution index (report evidence)."""
    from ..embeddings import embed

    vector = [float(value) for value in embed([text])[0]]
    with driver.session() as session:
        return [
            dict(record)
            for record in session.run(VECTOR_PROBE, index=VECTOR_INDEX, k=k, vec=vector)
        ]


def format_probe(text: str, results: Sequence[Mapping[str, Any]]) -> str:
    lines = [f'probe: "{text}"', ""]
    for rank, row in enumerate(results, start=1):
        lines += [
            f"{rank}. score={row['score']:.4f} {row['contribution_id']} "
            f"({row['person']}, {row['project']} {row['period']})",
            f"   {row['summary']}",
        ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Load the capability graph into Neo4j")
    ap.add_argument("--reset", action="store_true", help="delete all nodes before loading")
    ap.add_argument("--force", action="store_true", help="recompute embeddings, ignoring cache")
    ap.add_argument("--probe", metavar="TEXT", help="run a vector probe instead of loading")
    ap.add_argument("--probe-k", type=int, default=5, help="probe neighbours to return")
    args = ap.parse_args()

    driver = get_driver()
    try:
        if args.probe:
            print(format_probe(args.probe, probe(driver, args.probe, args.probe_k)))
            return
        apply_schema(driver)
        if args.reset:
            reset(driver)
        load(driver, force_embeddings=args.force)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
