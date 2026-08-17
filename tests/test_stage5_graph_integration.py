"""Neo4j-gated checks on the loaded capability graph. Skipped when the service is down.

These run against whatever `make stage5` last loaded, so they are cheap: they assert
what the graph must look like (counts reconciled against the Stage 3/4 artifacts, a
384-dim embedding on every Contribution, a working vector index, no ticket payload,
no duplicated MERGE keys) rather than re-running the load.

The suite-wide network block is overridden here — narrowed, not removed: loopback is
allowed so the local Docker service is reachable, and any other host still fails.
"""
from __future__ import annotations

import json
import socket

import pytest

from capgraph.pipeline import stage5_graph as stage5
from capgraph.settings import settings

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

CONTRIBUTION_PROPERTIES = {"id", "summary", "period", "confidence", "evidence_ticket_keys",
                           "embedding"}
NODE_PROPERTIES = {
    "Person": {"id", "pseudonym", "project_key", "active_from", "active_to"},
    "Project": {"key", "domain"},
    "Contribution": CONTRIBUTION_PROPERTIES,
    "Skill": {"name", "aliases"},
    "Specialization": {"name", "aliases"},
}


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch):
    """Narrow the suite-wide block to "loopback only" for this module."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _host(address):
        return address[0] if isinstance(address, tuple) else address

    def _guard(real):
        def wrapper(self, address, *args, **kwargs):
            if _host(address) not in LOOPBACK:
                raise AssertionError(f"tests must not leave localhost (attempted {address})")
            return real(self, address, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(socket.socket, "connect", _guard(connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(connect_ex))


@pytest.fixture(scope="module")
def driver():
    from neo4j import GraphDatabase

    instance = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=3,
        max_transaction_retry_time=3,
    )
    try:
        instance.verify_connectivity()
    except Exception as error:                       # service down: not a test failure
        instance.close()
        pytest.skip(f"Neo4j not available at {settings.neo4j_uri}: {error}")
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def counts(driver):
    loaded = stage5.graph_counts(driver)
    if not loaded["Contribution"]:
        pytest.skip("graph is empty; run `make stage5` first")
    return loaded


def _query(driver, statement, **params):
    with driver.session() as session:
        return [dict(record) for record in session.run(statement, **params)]


def _artifact_counts():
    """Expected counts read straight from the Stage 1/3/4 artifacts."""
    paths = [stage5.BUCKETS_PATH, stage5.NORM_PATH, stage5.TERMS_PATH, stage5.CAPS_PATH]
    if not all(path.exists() for path in paths):
        pytest.skip("pipeline artifacts are not present locally")

    buckets = stage5.read_buckets()
    contribs, _ = stage5.read_contributions()
    terms = stage5.read_terms()
    caps = stage5.read_capabilities()
    return {
        "Person": len({bucket.person_id for bucket in buckets}),
        "Project": len({bucket.project_key for bucket in buckets}),
        "Contribution": len(contribs),
        "Skill": sum(term.kind == "skill" for term in terms),
        "Specialization": sum(term.kind == "specialization" for term in terms),
        "MADE": len(contribs),
        "ON": len(contribs),
        "DEMONSTRATES": sum(len(c.skills) + len(c.specializations) for c in contribs),
        "HAS_SKILL": sum(cap.kind == "skill" for cap in caps),
        "HAS_SPECIALIZATION": sum(cap.kind == "specialization" for cap in caps),
        "COLLABORATED_WITH": len(stage5.build_collaborations(buckets)),
    }


def test_graph_counts_reconcile_with_the_pipeline_artifacts(counts):
    assert counts == _artifact_counts()


def test_no_ticket_payload_reached_any_node(driver):
    for label, expected in NODE_PROPERTIES.items():
        used = _query(
            driver, f"MATCH (n:{label}) UNWIND keys(n) AS key RETURN DISTINCT key ORDER BY key"
        )
        assert {row["key"] for row in used} <= expected, label


def test_every_contribution_carries_a_correctly_sized_embedding(driver, counts):
    rows = _query(
        driver,
        """
        MATCH (c:Contribution)
        RETURN count(c) AS total,
               count(c.embedding) AS embedded,
               min(size(c.embedding)) AS min_dims,
               max(size(c.embedding)) AS max_dims
        """,
    )

    assert rows[0]["embedded"] == counts["Contribution"]
    assert rows[0]["min_dims"] == rows[0]["max_dims"] == int(settings["embedding.dims"])


def test_the_vector_index_returns_the_contribution_its_own_embedding_came_from(driver):
    seed = _query(
        driver,
        "MATCH (c:Contribution) RETURN c.id AS id, c.embedding AS vec ORDER BY c.id LIMIT 1",
    )[0]

    hits = _query(driver, stage5.VECTOR_PROBE, index=stage5.VECTOR_INDEX, k=5, vec=seed["vec"])

    assert len(hits) == 5
    assert hits[0]["contribution_id"] == seed["id"]
    # The index searches a quantized copy of the vector, so an exact self-hit lands
    # just below the 1.0 a raw cosine would give (observed 0.9995 on Neo4j 5.26).
    assert hits[0]["score"] > 0.99
    assert all(hit["person"] and hit["project"] for hit in hits)


def test_every_contribution_is_attached_to_its_person_and_project(driver):
    orphans = _query(
        driver,
        """
        MATCH (c:Contribution)
        WHERE NOT (:Person)-[:MADE]->(c) OR NOT (c)-[:ON]->(:Project)
        RETURN c.id AS id LIMIT 5
        """,
    )

    assert orphans == []


def test_merge_keys_are_stable_so_a_re_run_cannot_duplicate_edges(driver, counts):
    for rel_type in ("MADE", "ON", "DEMONSTRATES", "HAS_SKILL", "COLLABORATED_WITH"):
        distinct = _query(
            driver,
            f"MATCH (a)-[r:{rel_type}]->(b) RETURN count(DISTINCT [a, b]) AS pairs",
        )[0]["pairs"]
        assert distinct == counts[rel_type], rel_type


def test_evidence_keys_are_pointers_that_match_the_source_artifact(driver):
    if not stage5.NORM_PATH.exists():
        pytest.skip("pipeline artifacts are not present locally")
    with stage5.NORM_PATH.open(encoding="utf-8") as handle:
        expected = json.loads(next(line for line in handle if line.strip()))

    stored = _query(
        driver,
        "MATCH (c:Contribution {id: $id}) RETURN c.evidence_ticket_keys AS keys, c.summary AS s",
        id=expected["contribution_id"],
    )[0]

    assert stored["keys"] == expected["evidence_ticket_keys"]
    assert stored["s"] == expected["contribution_summary"]


def test_collaboration_edges_are_labelled_as_co_presence(driver):
    bases = _query(
        driver, "MATCH ()-[r:COLLABORATED_WITH]->() RETURN DISTINCT r.basis AS basis"
    )

    assert [row["basis"] for row in bases] == [stage5.COLLABORATION_BASIS]
