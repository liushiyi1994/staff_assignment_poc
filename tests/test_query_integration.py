"""Neo4j-gated checks on the retrieval half of the query engine. No LLM calls.

Everything here runs against whatever `make stage5` last loaded and stops before the
re-rank, so the retrieval contract (alias-aware resolution, the two arms, expansion,
and the union) is verified against real Cypher rather than a fake driver — without
spending a cent. The suite-wide network block is narrowed to loopback, exactly as
tests/test_stage5_graph_integration.py does.
"""
from __future__ import annotations

import socket

import pytest

from capgraph.models import RoleSpec
from capgraph.query import rank, retrieve
from capgraph.settings import settings

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

BRIEF = "Need a backend engineer with deep container orchestration and Docker experience"


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
    with instance.session() as session:
        if not session.run("MATCH (c:Contribution) RETURN count(c) AS n").single()["n"]:
            instance.close()
            pytest.skip("graph is empty; run `make stage5` first")
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def embed_fn():
    """The local embedding model, pinned offline: cached weights or a clean skip."""
    from capgraph import embeddings

    patch = pytest.MonkeyPatch()
    patch.setenv("HF_HUB_OFFLINE", "1")
    patch.setenv("TRANSFORMERS_OFFLINE", "1")
    try:
        embeddings.embed(["warm up"])
    except Exception as error:                       # weights not cached locally
        patch.undo()
        pytest.skip(f"embedding model unavailable offline: {error}")
    yield embeddings.embed
    patch.undo()


@pytest.fixture(scope="module")
def role():
    return RoleSpec(role="backend engineer", specializations=["container orchestration"],
                    skills=["Docker", "containerizer"])


@pytest.fixture(scope="module")
def resolution(driver, role):
    return retrieve.resolve_role_terms(role, driver)


@pytest.fixture(scope="module")
def candidates(driver, role, resolution, embed_fn):
    """The real union, expanded once and shared: retrieval is read-only."""
    return retrieve.expand(
        retrieve.generate_candidates(
            role, BRIEF, driver, resolution=resolution, embed_fn=embed_fn
        ),
        driver,
        resolution=resolution,
    )


def test_known_specializations_come_back_from_the_graph(driver):
    names = retrieve.known_specializations(driver)
    assert names and names == sorted(names)


def test_resolution_maps_free_text_onto_canonical_terms(driver, resolution):
    assert resolution.canonical(retrieve.SKILL), "no skill resolved for Docker/containerizer"
    for requested, canonical in {**resolution.specializations, **resolution.skills}.items():
        assert canonical, requested


def test_resolution_matches_an_alias_not_only_the_canonical_name(driver):
    """Stage 3 folded synonyms into aliases; retrieval must see through them."""
    with driver.session() as session:
        row = session.run(
            """
            MATCH (s:Skill) WHERE size(s.aliases) > 0
            RETURN s.name AS name, s.aliases[0] AS alias ORDER BY s.name LIMIT 1
            """
        ).single()

    resolved = retrieve.resolve_terms([row["alias"]], retrieve.SKILL, driver)

    assert row["name"] in resolved[retrieve.normalize_term(row["alias"])]


def test_both_arms_return_people_and_the_union_is_at_least_as_large(
    driver, role, resolution, embed_fn
):
    vector = retrieve.vector_candidates(role, BRIEF, driver, embed_fn=embed_fn)
    structured = retrieve.structured_candidates(resolution, driver)
    union = retrieve.union_candidates(vector, structured)

    assert vector and structured
    assert len(union) >= max(len(vector), len(structured))
    assert len(union) <= len(vector) + len(structured)
    assert all(candidate.retrieval_sources for candidate in union)


def test_the_structured_arm_respects_its_top_k(driver, resolution):
    rows = retrieve.structured_candidates(resolution, driver)
    assert len(rows) <= int(settings["retrieval.structured_top_k"])
    strengths = [row["strength"] for row in rows]
    assert strengths == sorted(strengths, reverse=True)


def test_expansion_fills_capabilities_evidence_and_bounded_contributions(candidates):
    per_person = int(settings["retrieval.contributions_per_person"])
    assert candidates
    for candidate in candidates:
        assert candidate.specializations or candidate.skills
        assert 0 < len(candidate.contributions) <= per_person
        assert all(c.person_id == candidate.person_id for c in candidate.contributions)
        assert rank.own_evidence_keys(candidate), candidate.person_id


def test_a_vector_hit_is_carried_into_that_persons_expanded_contributions(candidates):
    checked = 0
    for candidate in candidates:
        if not candidate.vector_hit_contribution_ids:
            continue
        checked += 1
        held = {c.contribution_id for c in candidate.contributions}
        assert set(candidate.vector_hit_contribution_ids[:1]) <= held
    assert checked, "the vector arm surfaced nobody"


def test_scoring_the_real_union_keeps_vector_only_people_in_play(
    candidates, role, resolution
):
    for candidate in candidates:
        rank.score_candidate(candidate, role, resolution)

    vector_only = [c for c in candidates if c.retrieval_sources == [retrieve.ARM_VECTOR]]
    assert vector_only, "this brief found nobody by vector alone"
    assert all(0.0 <= c.score <= 1.0 for c in candidates)
    assert max(c.score for c in vector_only) > 0.0


def test_the_expanded_subgraph_carries_no_ticket_text(candidates):
    """Non-negotiable #2 seen from the query side: pointers only, never ticket bodies."""
    for candidate in candidates:
        for contribution in candidate.contributions:
            assert contribution.evidence_ticket_keys
            assert all(key.count("-") >= 1 for key in contribution.evidence_ticket_keys)
