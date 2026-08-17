"""Intent parsing and end-to-end wiring. Offline: call_json and the driver are fakes."""
from __future__ import annotations

import json

import pytest

from capgraph.llm import UnroutableModelError
from capgraph.models import CandidateProfile, Intent, RankedPerson, RoleSpec
from capgraph.query import engine, intent as intent_module
from capgraph.query.retrieve import TermResolution
from capgraph.settings import settings

BRIEF = "Need a backend engineer with deep container orchestration experience"


# ---------- intent parsing ----------

def _parse_with(monkeypatch, payload):
    seen = {}

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        seen.update(prompt=prompt, model=model, stage=stage)
        return payload

    monkeypatch.setattr(intent_module, "call_json", fake_call_json)
    return intent_module.parse_intent(BRIEF, ["Cluster orchestration"]), seen


def test_parse_intent_renders_the_prompt_and_uses_the_configured_query_stage(monkeypatch):
    parsed, seen = _parse_with(monkeypatch, {
        "roles": [{"role": "backend engineer", "specializations": ["Cluster orchestration"],
                   "skills": ["Docker"], "count": 2}],
        "domain": "cluster platform",
    })

    assert parsed.roles[0].count == 2
    assert parsed.domain == "cluster platform"
    assert BRIEF in seen["prompt"] and "- Cluster orchestration" in seen["prompt"]
    assert seen["model"] == settings["llm.intent_model"]
    assert seen["stage"] == settings["llm.query_stage"]


def test_parse_intent_honours_an_explicit_stage(monkeypatch):
    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        assert stage == "eval_run"
        return {"roles": [{"role": "engineer"}]}

    monkeypatch.setattr(intent_module, "call_json", fake_call_json)
    intent_module.parse_intent(BRIEF, [], stage="eval_run")


def test_a_parse_with_no_roles_falls_back_to_the_brief(monkeypatch):
    parsed, _ = _parse_with(monkeypatch, {"roles": [], "domain": ""})

    assert len(parsed.roles) == 1
    assert parsed.roles[0].role.startswith("Need a backend engineer")
    assert parsed.roles[0].specializations == [] and parsed.roles[0].skills == []


# ---------- end-to-end wiring ----------

class StubDriver:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.closed = False

    def verify_connectivity(self):
        if self.fail is not None:
            raise self.fail

    def close(self):
        self.closed = True


def _wire(monkeypatch, *, ranking=None, rejected=(), candidates=None):
    role = RoleSpec(role="backend engineer", specializations=["Cluster orchestration"],
                    skills=["Docker"])
    if candidates is None:
        candidates = [
            CandidateProfile(person_id="p1", person_name="Person p1",
                             retrieval_sources=["vector"]),
            CandidateProfile(person_id="p2", person_name="Person p2",
                             retrieval_sources=["vector", "structured"]),
            CandidateProfile(person_id="p3", person_name="Person p3",
                             retrieval_sources=["structured"]),
        ]
    if ranking is None:
        ranking = [RankedPerson(person_id="p1", person_name="Person p1", fit="strong",
                                reason="MESOS-1 containerizer work", score=0.8,
                                found_by=["vector"], evidence_ticket_keys=["MESOS-1"])]

    monkeypatch.setattr(engine, "known_specializations", lambda driver: ["Cluster orchestration"])
    monkeypatch.setattr(engine, "parse_intent",
                        lambda brief, specs, stage=None: Intent(roles=[role], domain="cluster"))
    resolution = TermResolution(
        specializations={"cluster orchestration": ["Cluster orchestration"]},
        skills={"docker": ["Docker image build tooling"]},
    )
    monkeypatch.setattr(engine, "resolve_role_terms", lambda role, driver: resolution)
    monkeypatch.setattr(
        engine, "generate_candidates",
        lambda role, brief, driver, resolution=None, roster=None, as_of=None,
        lexical_index=None: candidates,
    )
    monkeypatch.setattr(engine, "expand",
                        lambda candidates, driver, resolution=None, as_of=None: candidates)
    monkeypatch.setattr(engine, "score_candidate",
                        lambda candidate, role, resolution: candidate)
    monkeypatch.setattr(engine, "rerank",
                        lambda brief, role, candidates, stage=None: (ranking, list(rejected)))
    monkeypatch.setattr(engine, "finish",
                        lambda brief, role, ranking, candidates, stage=None: (ranking, []))
    return role


def test_query_reports_arm_provenance_and_timings(monkeypatch):
    _wire(monkeypatch)

    result = engine.query(BRIEF, StubDriver())

    shortlist = result.shortlists[0]
    assert shortlist.candidate_counts == {
        "vector": 2, "structured": 2, "lexical": 0, "vector_only": 1, "lexical_only": 0,
        "union": 3, "reranked": 3, "shortlisted": 1,
    }
    assert shortlist.ranking[0].found_by == ["vector"]
    assert "intent_ms" in result.timings_ms and "total_ms" in result.timings_ms
    assert result.timings_ms["total_ms"] >= result.timings_ms["intent_ms"]


def test_query_carries_rejected_rerank_entries_into_the_result(monkeypatch):
    _wire(monkeypatch, ranking=[], rejected=["p2: cites no evidence ticket key"])

    result = engine.query(BRIEF, StubDriver())

    assert result.shortlists[0].ranking == []
    assert result.shortlists[0].rejected == ["p2: cites no evidence ticket key"]


def test_query_result_serializes_for_the_json_output(monkeypatch):
    _wire(monkeypatch)

    payload = json.loads(engine.query(BRIEF, StubDriver()).model_dump_json())

    assert payload["brief"] == BRIEF
    assert payload["shortlists"][0]["ranking"][0]["evidence_ticket_keys"] == ["MESOS-1"]


# ---------- failure modes ----------

def test_a_down_database_fails_with_an_actionable_message(monkeypatch):
    driver = StubDriver(fail=OSError("connection refused"))
    monkeypatch.setattr(engine, "get_driver", lambda: driver)

    with pytest.raises(engine.GraphUnavailableError, match="make db-up"):
        engine.connected_driver()
    assert driver.closed


def test_main_exits_non_zero_instead_of_raising_when_neo4j_is_down(monkeypatch, capsys):
    monkeypatch.setattr(engine, "get_driver", lambda: StubDriver(fail=OSError("refused")))

    assert engine.main([BRIEF]) == 2
    assert "not reachable" in capsys.readouterr().err


def test_main_exits_non_zero_when_a_call_is_refused_before_it_is_made(monkeypatch, capsys):
    monkeypatch.setattr(engine, "get_driver", lambda: StubDriver())
    monkeypatch.setattr(
        engine, "query",
        lambda *args, **kwargs: (_ for _ in ()).throw(UnroutableModelError("no route")),
    )

    assert engine.main([BRIEF]) == 3
    assert "refused before calling the model" in capsys.readouterr().err


def test_main_prints_json_on_request(monkeypatch, capsys):
    driver = StubDriver()
    monkeypatch.setattr(engine, "get_driver", lambda: driver)
    _wire(monkeypatch)

    assert engine.main([BRIEF, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["brief"] == BRIEF
    assert driver.closed


def test_main_prints_a_readable_shortlist_by_default(monkeypatch, capsys):
    monkeypatch.setattr(engine, "get_driver", lambda: StubDriver())
    _wire(monkeypatch)

    assert engine.main([BRIEF]) == 0
    out = capsys.readouterr().out
    assert "Person p1" in out and "MESOS-1" in out and "vector" in out
