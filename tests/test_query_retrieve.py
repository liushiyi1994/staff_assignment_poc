"""Candidate generation and subgraph expansion. Offline: no Neo4j, no network.

The driver is faked and records every statement and parameter map, so these tests pin
two things the live database cannot be trusted to reveal: that user text reaches Cypher
only as parameters, and that the union really is a union.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from capgraph.models import RoleSpec
from capgraph.query import retrieve
from capgraph.settings import settings


class FakeSession:
    def __init__(self, driver):
        self._driver = driver

    def run(self, statement, **params):
        self._driver.calls.append((statement, params))
        for fragment, rows in self._driver.responses.items():
            if fragment in statement:
                return rows(params) if callable(rows) else list(rows)
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDriver:
    """Answers from a {statement fragment: rows} table and records every call."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return FakeSession(self)

    def params_for(self, fragment: str) -> dict:
        for statement, params in self.calls:
            if fragment in statement:
                return params
        raise AssertionError(f"no statement containing {fragment!r} was run")

    def ran(self, fragment: str) -> bool:
        return any(fragment in statement for statement, _ in self.calls)


VECTOR = "db.index.vector.queryNodes"
STRUCTURED = "UNION ALL"
RESOLVE_SKILL = "MATCH (t:Skill)"
RESOLVE_SPEC = "MATCH (t:Specialization)"
CAPABILITIES = "HAS_SKILL|HAS_SPECIALIZATION"
SUPPORTING = "count(DISTINCT c) AS supporting"
SELECT = "collect(c.id)"
FETCH = "MATCH (p:Person)-[:MADE]->(c:Contribution {id: contribution_id})"
NAMES = "MATCH (p:Person {id: person_id})"


def fake_embed(texts):
    return np.zeros((len(texts), int(settings["embedding.dims"])), dtype=np.float32)


# ---------- term normalization and patterns ----------

def test_normalize_term_collapses_case_and_whitespace():
    assert retrieve.normalize_term("  Docker   Compose ") == "docker compose"


@pytest.mark.parametrize(
    ("term", "name", "matches"),
    [
        ("ci", "ci pipeline maintenance", True),
        ("ci", "build efficiency analysis", False),      # substring, not a word
        ("docker", "Docker image build tooling", True),
        ("container orchestration", "container orchestration internals", True),
        ("kafka", "kafkaesque reporting", False),
    ],
)
def test_term_pattern_matches_on_word_boundaries(term, name, matches):
    pattern = retrieve.term_pattern(term)
    # Cypher's =~ is a full-string match, and both sides are lower-cased first.
    assert bool(re.fullmatch(pattern, name.lower())) is matches


def test_term_pattern_drops_the_boundary_at_non_alphanumeric_edges():
    pattern = retrieve.term_pattern("c++")
    assert re.fullmatch(pattern, "c++ runtime internals")


def test_single_character_terms_resolve_by_exact_name_only():
    assert retrieve.term_pattern("r") is None
    assert retrieve.term_params(["R"]) == [{"exact": "r", "pattern": None}]


def test_regex_metacharacters_in_a_term_stay_literal():
    pattern = retrieve.term_pattern("node.js")
    assert re.fullmatch(pattern, "node.js tooling")
    assert not re.fullmatch(pattern, "nodexjs tooling")


def test_term_params_deduplicate_and_normalize():
    assert retrieve.term_params(["Kafka", " kafka ", "", "Docker"]) == [
        {"exact": "kafka", "pattern": retrieve.term_pattern("kafka")},
        {"exact": "docker", "pattern": retrieve.term_pattern("docker")},
    ]


# ---------- resolution ----------

def test_resolve_terms_matches_canonical_names_and_aliases():
    driver = FakeDriver({
        RESOLVE_SKILL: [{"term": "docker", "canonical": ["Docker image build tooling",
                                                         "Container image packaging"]}],
    })

    resolved = retrieve.resolve_terms(["Docker"], retrieve.SKILL, driver)

    assert resolved == {"docker": ["Container image packaging", "Docker image build tooling"]}
    params = driver.params_for(RESOLVE_SKILL)
    assert params["terms"] == [{"exact": "docker", "pattern": retrieve.term_pattern("docker")}]


def test_resolve_terms_selects_the_label_for_the_kind():
    driver = FakeDriver()
    retrieve.resolve_terms(["x"], retrieve.SPECIALIZATION, driver)
    assert driver.ran(RESOLVE_SPEC) and not driver.ran(RESOLVE_SKILL)


def test_resolve_terms_refuses_an_unknown_kind():
    with pytest.raises(ValueError, match="unknown term kind"):
        retrieve.resolve_terms(["x"], "seniority", FakeDriver())


def test_resolve_terms_makes_no_query_without_terms():
    driver = FakeDriver()
    assert retrieve.resolve_terms([], retrieve.SKILL, driver) == {}
    assert driver.calls == []


def test_unmatched_terms_are_simply_absent_from_the_resolution():
    driver = FakeDriver({RESOLVE_SKILL: [{"term": "kafka", "canonical": []}]})
    assert retrieve.resolve_terms(["Kafka"], retrieve.SKILL, driver) == {}


def test_user_text_never_reaches_cypher_as_a_statement_fragment():
    """Injection guard: terms and briefs travel as parameters, never as text."""
    role = RoleSpec(role="engineer", specializations=["'; MATCH (n) DETACH DELETE n //"],
                    skills=["Docker"])
    driver = FakeDriver()

    resolution = retrieve.resolve_role_terms(role, driver)
    retrieve.generate_candidates(
        role, "brief text", driver, resolution=resolution, embed_fn=fake_embed
    )

    for statement, _ in driver.calls:
        assert "DETACH DELETE" not in statement
        assert "brief text" not in statement


# ---------- arms ----------

def test_query_text_carries_the_role_alongside_the_brief():
    role = RoleSpec(role="backend engineer", specializations=["Distributed systems"],
                    skills=["Kafka"])
    text = retrieve.query_text(role, "Need streaming help")
    assert "Need streaming help" in text
    assert "backend engineer" in text and "Distributed systems" in text and "Kafka" in text


def test_vector_arm_groups_contributions_by_person_and_keeps_the_best_score():
    driver = FakeDriver({VECTOR: [
        {"person_id": "MESOS:1", "person_name": "Person MESOS-1", "contribution_id": "c1",
         "score": 0.81},
        {"person_id": "MESOS:1", "person_name": "Person MESOS-1", "contribution_id": "c2",
         "score": 0.77},
        {"person_id": "MESOS:2", "person_name": "Person MESOS-2", "contribution_id": "c3",
         "score": 0.90},
    ]})

    hits = retrieve.vector_candidates(RoleSpec(role="eng"), "brief", driver,
                                      embed_fn=fake_embed)

    assert [hit.person_id for hit in hits] == ["MESOS:2", "MESOS:1"]   # best score first
    assert hits[1].contribution_ids == ("c1", "c2")
    assert hits[1].best_score == pytest.approx(0.81)
    params = driver.params_for(VECTOR)
    assert params["k"] == int(settings["retrieval.vector_top_k"])
    assert len(params["vec"]) == int(settings["embedding.dims"])


def _structured_edge(person_id, term, evidence_count=4, last_used="2018-12-31"):
    return {"person_id": person_id, "person_name": f"Person {person_id}",
            "evidence_count": evidence_count, "last_used": last_used, "matched": term}


def test_structured_arm_queries_resolved_canonical_terms_only():
    resolution = retrieve.TermResolution(
        specializations={"orchestration": ["Cluster orchestration"]},
        skills={"docker": ["Docker image build tooling"]},
    )
    driver = FakeDriver({STRUCTURED: [
        _structured_edge("MESOS:1", "Docker image build tooling"),
        _structured_edge("MESOS:1", "Cluster orchestration"),
    ]})

    rows = retrieve.structured_candidates(resolution, driver)

    params = driver.params_for(STRUCTURED)
    assert params["specialization_terms"] == ["Cluster orchestration"]
    assert params["skill_terms"] == ["Docker image build tooling"]
    assert params["roster"] is None
    assert rows[0]["matched_terms"] == ["Cluster orchestration", "Docker image build tooling"]


def test_structured_arm_makes_no_query_when_nothing_resolved():
    driver = FakeDriver()
    assert retrieve.structured_candidates(retrieve.TermResolution(), driver) == []
    assert driver.calls == []


# ---------- union ----------

def _hit(person_id, ids=("c1",), score=0.7):
    return retrieve.VectorHit(person_id=person_id, person_name=f"Person {person_id}",
                              contribution_ids=tuple(ids), best_score=score)


def _structured(person_id, strength=5.0):
    return {"person_id": person_id, "person_name": f"Person {person_id}",
            "strength": strength, "matched_terms": ["Docker image build tooling"]}


def test_union_keeps_people_found_by_either_arm_alone():
    candidates = retrieve.union_candidates([_hit("p1")], [_structured("p2")])

    assert [c.person_id for c in candidates] == ["p1", "p2"]
    assert candidates[0].retrieval_sources == ["vector"]
    assert candidates[1].retrieval_sources == ["structured"]


def test_union_records_both_arms_for_a_person_both_found():
    candidates = retrieve.union_candidates([_hit("p1", score=0.83)], [_structured("p1", 9.0)])

    assert len(candidates) == 1
    assert candidates[0].retrieval_sources == ["vector", "structured"]
    assert candidates[0].vector_score == pytest.approx(0.83)
    assert candidates[0].structured_strength == pytest.approx(9.0)


def test_union_is_not_an_intersection():
    candidates = retrieve.union_candidates(
        [_hit("p1"), _hit("p2")], [_structured("p2"), _structured("p3")]
    )
    assert {c.person_id for c in candidates} == {"p1", "p2", "p3"}


def test_union_carries_the_vector_hit_contribution_ids():
    candidates = retrieve.union_candidates([_hit("p1", ids=("c1", "c2"))], [])
    assert candidates[0].vector_hit_contribution_ids == ["c1", "c2"]


class FakeLexicalIndex:
    """The engine's BM25 arm, without the corpus: (person, score) pairs, best first."""

    def __init__(self, people):
        self.people = list(people)

    def top_people(self, query, *, k, roster=None):
        found = [row for row in self.people if roster is None or row[0] in set(roster)]
        return found[:k]


def test_generate_candidates_runs_every_arm():
    driver = FakeDriver({
        VECTOR: [{"person_id": "p1", "person_name": "Person p1", "contribution_id": "c1",
                  "score": 0.6}],
        STRUCTURED: [_structured_edge("p2", "Docker image build tooling")],
        NAMES: [{"person_id": "p3", "person_name": "Person p3"}],
    })
    resolution = retrieve.TermResolution(skills={"docker": ["Docker image build tooling"]})

    candidates = retrieve.generate_candidates(
        RoleSpec(role="eng", skills=["Docker"]), "brief", driver,
        resolution=resolution, embed_fn=fake_embed,
        lexical_index=FakeLexicalIndex([("p3", 4.2)]),
    )

    assert {c.person_id for c in candidates} == {"p1", "p2", "p3"}
    assert driver.ran(VECTOR) and driver.ran(STRUCTURED)
    by_id = {c.person_id: c for c in candidates}
    assert by_id["p3"].retrieval_sources == ["lexical"]
    assert by_id["p3"].person_name == "Person p3"


def test_generate_candidates_asks_for_no_pseudonym_when_the_other_arms_found_everyone():
    driver = FakeDriver({
        VECTOR: [{"person_id": "p1", "person_name": "Person p1", "contribution_id": "c1",
                  "score": 0.6}],
        STRUCTURED: [],
    })

    retrieve.generate_candidates(
        RoleSpec(role="eng", skills=["Docker"]), "brief", driver,
        resolution=retrieve.TermResolution(), embed_fn=fake_embed,
        lexical_index=FakeLexicalIndex([("p1", 4.2)]),
    )

    assert not driver.ran(NAMES)


def test_the_lexical_arm_is_skipped_entirely_when_its_width_is_zero(monkeypatch):
    monkeypatch.setitem(settings._cfg["retrieval"], "bm25_top_k", 0)

    def forbidden(*args, **kwargs):
        raise AssertionError("a disabled arm must not be queried")

    driver = FakeDriver({VECTOR: [], STRUCTURED: []})
    candidates = retrieve.generate_candidates(
        RoleSpec(role="eng", skills=["Docker"]), "brief", driver,
        resolution=retrieve.TermResolution(), embed_fn=fake_embed,
        lexical_index=type("Boom", (), {"top_people": forbidden})(),
    )

    assert candidates == []
    assert not driver.ran(NAMES)


# ---------- expansion ----------

def _resolution():
    return retrieve.TermResolution(
        specializations={"orchestration": ["Cluster orchestration"]},
        skills={"docker": ["Docker image build tooling"]},
    )


def _expansion_driver():
    return FakeDriver({
        CAPABILITIES: [
            {"person_id": "p1", "term": "Cluster orchestration", "kind": "specialization",
             "evidence_count": 7, "last_used": "2018-12-31", "decay_score": 0.9},
            {"person_id": "p1", "term": "Docker image build tooling", "kind": "skill",
             "evidence_count": 4, "last_used": "2018-09-30", "decay_score": 0.8},
        ],
        SELECT: [{"person_id": "p1", "contribution_ids": ["c9", "c1"]}],
        FETCH: [
            {"id": "c9", "person_id": "p1", "project_key": "MESOS", "period": "2018-Q4",
             "summary": "Containerizer work", "confidence": "high",
             "evidence_ticket_keys": ["MESOS-1", "MESOS-2"],
             "terms": [{"name": "Docker image build tooling", "kind": "skill",
                        "strength": None},
                       {"name": "Cluster orchestration", "kind": "specialization",
                        "strength": "primary"}]},
            {"id": "c1", "person_id": "p1", "project_key": "MESOS", "period": "2017-Q1",
             "summary": "Agent recovery", "confidence": "medium",
             "evidence_ticket_keys": ["MESOS-3"],
             "terms": [{"name": None, "kind": "skill", "strength": None}]},
        ],
    })


def test_expand_fills_capabilities_contributions_and_evidence_keys():
    driver = _expansion_driver()
    candidates = retrieve.union_candidates([_hit("p1", ids=("c9",))], [])

    expanded = retrieve.expand(candidates, driver)

    profile = expanded[0]
    assert [cap.term for cap in profile.specializations] == ["Cluster orchestration"]
    assert [cap.term for cap in profile.skills] == ["Docker image build tooling"]
    assert profile.specializations[0].last_used.isoformat() == "2018-12-31"
    assert [c.contribution_id for c in profile.contributions] == ["c9", "c1"]
    assert profile.contributions[0].evidence_ticket_keys == ["MESOS-1", "MESOS-2"]
    assert [s.name for s in profile.contributions[0].skills] == ["Docker image build tooling"]
    # An OPTIONAL MATCH that found no term must not become a nameless capability.
    assert profile.contributions[1].skills == []


def test_expand_passes_vector_hits_and_matched_terms_to_the_selection():
    driver = _expansion_driver()

    retrieve.expand(retrieve.union_candidates([_hit("p1", ids=("c9",))], []), driver,
                    resolution=_resolution())

    params = driver.params_for(SELECT)
    assert params["rows"] == [{"person_id": "p1", "hit_ids": ["c9"]}]
    assert params["matched_terms"] == ["Cluster orchestration", "Docker image build tooling"]
    assert params["per_person"] == int(settings["retrieval.contributions_per_person"])


def test_expand_counts_the_distinct_contributions_behind_the_match():
    driver = _expansion_driver()
    driver.responses[SUPPORTING] = [{"person_id": "p1", "supporting": 6}]

    expanded = retrieve.expand(
        retrieve.union_candidates([_hit("p1", ids=("c9",))], []), driver,
        resolution=_resolution(),
    )

    assert expanded[0].matched_contribution_count == 6
    assert driver.params_for(SUPPORTING)["matched_terms"] == [
        "Cluster orchestration", "Docker image build tooling"
    ]


def test_expand_does_not_count_supporting_contributions_without_resolved_terms():
    driver = _expansion_driver()

    expanded = retrieve.expand(retrieve.union_candidates([_hit("p1")], []), driver)

    assert expanded[0].matched_contribution_count == 0
    assert not driver.ran(SUPPORTING)


def test_expand_of_nothing_queries_nothing():
    driver = FakeDriver()
    assert retrieve.expand([], driver) == []
    assert driver.calls == []
