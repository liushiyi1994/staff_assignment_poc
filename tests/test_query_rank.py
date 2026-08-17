"""Deterministic scoring and the validated LLM re-rank. Offline: call_json is mocked.

The score is the part of the design that must stay explainable, so these tests pin the
arithmetic itself (component values, weight renormalization, determinism) rather than
only the ordering it happens to produce.
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from capgraph.models import (
    CandidateProfile,
    Contribution,
    PersonCapability,
    RoleSpec,
    SkillRef,
    SpecializationRef,
)
from capgraph.query import rank
from capgraph.query.retrieve import TermResolution
from capgraph.settings import settings

WEIGHTS = settings["scoring.weights"]


def cap(term, kind, count=5, decay=0.9, person="p1"):
    return PersonCapability(person_id=person, term=term, kind=kind, evidence_count=count,
                            contribution_ids=[], last_used=date(2018, 12, 31),
                            decay_score=decay)


def contribution(cid, person="p1", keys=("MESOS-1",), skills=(), specializations=()):
    return Contribution(
        contribution_id=cid, person_id=person, project_key="MESOS", period="2018-Q4",
        contribution_summary=f"summary {cid}",
        specializations=[SpecializationRef(name=s) for s in specializations],
        skills=[SkillRef(name=s) for s in skills],
        confidence="high", reason="", evidence_ticket_keys=list(keys),
    )


def candidate(person="p1", **kwargs):
    return CandidateProfile(person_id=person, person_name=f"Person {person}", **kwargs)


ROLE = RoleSpec(role="backend engineer", specializations=["Cluster orchestration"],
                skills=["Docker", "Kafka"])
RESOLUTION = TermResolution(
    specializations={"cluster orchestration": ["Cluster orchestration"]},
    skills={"docker": ["Docker image build tooling"], "kafka": ["Kafka stream processing"]},
)


# ---------- score components ----------

def test_full_match_scores_every_component():
    scored = rank.score_candidate(
        candidate(
            specializations=[cap("Cluster orchestration", "specialization", 12, 0.9)],
            skills=[cap("Docker image build tooling", "skill", 8, 0.8),
                    cap("Kafka stream processing", "skill", 6, 0.7)],
            matched_contribution_count=14,
        ),
        ROLE, RESOLUTION,
    )

    assert scored.score_parts == {
        "specialization_match": 1.0,
        "skill_overlap": 1.0,
        "recency": 0.9,                       # highest decay among matched edges
        "evidence_strength": 1.0,             # 14 distinct contributions, past 10
    }
    expected = (WEIGHTS["specialization_match"] + WEIGHTS["skill_overlap"]
                + WEIGHTS["recency"] * 0.9 + WEIGHTS["evidence_strength"])
    assert scored.score == pytest.approx(round(expected / sum(WEIGHTS.values()), 4))
    assert 0.0 <= scored.score <= 1.0


def test_partial_skill_overlap_is_a_fraction_of_what_was_asked_for():
    scored = rank.score_candidate(
        candidate(skills=[cap("Docker image build tooling", "skill")]), ROLE, RESOLUTION
    )
    assert scored.score_parts["skill_overlap"] == 0.5      # 1 of 2 requested skills
    assert scored.score_parts["specialization_match"] == 0.0


def test_matching_is_alias_aware_through_the_resolution():
    """The role asked for "Docker"; the graph calls it something else entirely."""
    scored = rank.score_candidate(
        candidate(skills=[cap("Docker image build tooling", "skill")]), ROLE, RESOLUTION
    )
    assert scored.matched_skills == ["Docker image build tooling"]
    assert scored.matched_specializations == []


def test_an_unrelated_candidate_scores_below_a_matching_one():
    strong = rank.score_candidate(
        candidate("p1", specializations=[cap("Cluster orchestration", "specialization")],
                  skills=[cap("Docker image build tooling", "skill")]),
        ROLE, RESOLUTION,
    )
    weak = rank.score_candidate(
        candidate("p2", specializations=[cap("Frontend web development", "specialization")],
                  skills=[cap("CSS", "skill")]),
        ROLE, RESOLUTION,
    )
    assert strong.score > weak.score
    assert weak.score == 0.0


def test_recency_reads_the_stored_decay_and_never_recomputes_it():
    scored = rank.score_candidate(
        candidate(specializations=[cap("Cluster orchestration", "specialization", decay=0.42)]),
        ROLE, RESOLUTION,
    )
    assert scored.score_parts["recency"] == 0.42


@pytest.mark.parametrize(
    ("supporting", "expected"),
    [(0, 0.0), (1, math.sqrt(0.1)), (5, math.sqrt(0.5)), (10, 1.0), (40, 1.0)],
)
def test_evidence_strength_saturates_at_the_configured_count(supporting, expected):
    assert rank.evidence_strength(supporting) == pytest.approx(expected)


def test_evidence_counts_contributions_once_however_many_terms_they_demonstrate():
    """Two matched edges over the same three contributions is three pieces of evidence."""
    caps = [cap("Cluster orchestration", "specialization", 3),
            cap("Docker image build tooling", "skill", 3)]
    counted = rank.score_candidate(
        candidate(specializations=caps[:1], skills=caps[1:], matched_contribution_count=3),
        ROLE, RESOLUTION,
    )
    assert counted.score_parts["evidence_strength"] == pytest.approx(round(math.sqrt(0.3), 4))


def test_without_an_expansion_count_the_matched_edges_are_a_lower_bound():
    scored = rank.score_candidate(
        candidate(specializations=[cap("Cluster orchestration", "specialization", 40)]),
        ROLE, RESOLUTION,
    )
    # One matched edge, so one supporting contribution is assumed — never 40.
    assert scored.score_parts["evidence_strength"] == pytest.approx(round(math.sqrt(0.1), 4))


# ---------- weight handling ----------

def test_components_the_role_does_not_ask_for_are_dropped_and_weights_renormalized():
    role = RoleSpec(role="engineer", skills=["Docker"])       # no specializations asked
    scored = rank.score_candidate(
        candidate(skills=[cap("Docker image build tooling", "skill", 10, 0.9)],
                  matched_contribution_count=10),
        role, TermResolution(skills={"docker": ["Docker image build tooling"]}),
    )

    assert "specialization_match" not in scored.score_parts
    applicable = sum(WEIGHTS[name] for name in scored.score_parts)
    expected = (WEIGHTS["skill_overlap"] * 1.0 + WEIGHTS["recency"] * 0.9
                + WEIGHTS["evidence_strength"] * 1.0) / applicable
    assert scored.score == pytest.approx(round(expected, 4))
    # Renormalization keeps a role that asks for less from being capped below 1.
    assert scored.score > 0.9


def test_a_role_with_no_terms_at_all_still_scores_on_recency_and_evidence():
    scored = rank.score_candidate(
        candidate(
            skills=[cap("Docker image build tooling", "skill", 10, 0.5)],
            contributions=[contribution("c1", skills=["Docker image build tooling"])],
            vector_hit_contribution_ids=["c1"],
        ),
        RoleSpec(role="engineer"), TermResolution(),
    )
    assert set(scored.score_parts) == {"recency", "evidence_strength"}
    assert scored.score > 0


def test_a_missing_weight_is_refused_rather_than_defaulted(monkeypatch):
    monkeypatch.setitem(settings._cfg["scoring"], "weights", {"recency": 1.0})
    with pytest.raises(ValueError, match="specialization_match"):
        rank.score_candidate(candidate(), ROLE, RESOLUTION)


def test_a_negative_weight_is_refused(monkeypatch):
    monkeypatch.setitem(
        settings._cfg["scoring"], "weights",
        {**WEIGHTS, "recency": -0.2},
    )
    with pytest.raises(ValueError, match="negative"):
        rank.weights()


def test_scoring_is_deterministic_across_repeated_runs():
    scores = {
        rank.score_candidate(
            candidate(specializations=[cap("Cluster orchestration", "specialization")],
                      skills=[cap("Docker image build tooling", "skill")]),
            ROLE, RESOLUTION,
        ).score
        for _ in range(5)
    }
    assert len(scores) == 1


def test_identity_resolution_keeps_exact_name_matching_working_without_a_graph():
    role = RoleSpec(role="backend", specializations=["Distributed systems backend"],
                    skills=["Kafka"])
    scored = rank.score_candidate(
        candidate(specializations=[cap("Distributed systems backend", "specialization")],
                  skills=[cap("Kafka", "skill")]),
        role,
    )
    assert scored.score_parts["specialization_match"] == 1.0
    assert scored.score_parts["skill_overlap"] == 1.0


# ---------- the vector-only path ----------

def test_a_vector_only_candidate_scores_from_the_contributions_that_surfaced_it():
    """No parsed term matched, so its relevance evidence is the vector hits themselves."""
    scored = rank.score_candidate(
        candidate(
            skills=[cap("Mesos agent recovery", "skill", 4, 0.75)],
            contributions=[contribution("c9", skills=["Mesos agent recovery"]),
                           contribution("c8", skills=["Unrelated work"])],
            vector_hit_contribution_ids=["c9"],
            retrieval_sources=["vector"],
        ),
        ROLE, RESOLUTION,
    )

    assert scored.score_parts["specialization_match"] == 0.0
    assert scored.score_parts["recency"] == 0.75
    assert scored.score_parts["evidence_strength"] == pytest.approx(round(math.sqrt(0.1), 4))
    assert scored.score > 0
    # ... and still ranks below someone who actually matches the ask.
    matching = rank.score_candidate(
        candidate("p2", specializations=[cap("Cluster orchestration", "specialization")],
                  skills=[cap("Docker image build tooling", "skill")]),
        ROLE, RESOLUTION,
    )
    assert matching.score > scored.score


def test_the_vector_fallback_is_not_used_once_a_term_matches():
    scored = rank.score_candidate(
        candidate(
            specializations=[cap("Cluster orchestration", "specialization", 3, 0.6)],
            skills=[cap("Mesos agent recovery", "skill", 4, 0.99)],
            contributions=[contribution("c9", skills=["Mesos agent recovery"])],
            vector_hit_contribution_ids=["c9"],
        ),
        ROLE, RESOLUTION,
    )
    # 0.99 belongs to an unmatched skill; matched evidence is what recency reports.
    assert scored.score_parts["recency"] == 0.6


# ---------- re-rank input ----------

def test_rerank_input_is_the_top_k_by_score_with_deterministic_ties():
    top_k = int(settings["retrieval.rerank_top_k"])
    candidates = [candidate(f"p{i:02d}") for i in range(top_k + 5)]
    for index, profile in enumerate(candidates):
        profile.score = 0.5 if index % 2 else 0.9

    shortlist = rank.rerank_input(candidates)

    assert len(shortlist) == top_k
    assert shortlist == rank.rerank_input(list(reversed(candidates)))
    assert [c.person_id for c in shortlist[:3]] == ["p00", "p02", "p04"]


# ---------- re-rank validation ----------

def _shortlist():
    first = candidate("p1", contributions=[contribution("c1", keys=("MESOS-1", "MESOS-2"))],
                      retrieval_sources=["vector"])
    second = candidate("p2", contributions=[contribution("c2", person="p2", keys=("MESOS-9",))],
                       retrieval_sources=["structured"])
    first.matched_skills = ["Docker image build tooling"]
    return [first, second]


def _rerank_with(monkeypatch, payload):
    seen = {}

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        seen.update(prompt=prompt, model=model, stage=stage, max_tokens=max_tokens)
        return payload

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    ranking, rejected = rank.rerank("brief", ROLE, _shortlist())
    return ranking, rejected, seen


def test_rerank_returns_people_with_their_validated_evidence(monkeypatch):
    ranking, rejected, seen = _rerank_with(monkeypatch, {"ranking": [
        {"person_id": "p1", "fit": "strong", "reason": "Deep containerizer work",
         "evidence_ticket_keys": ["MESOS-1"]},
    ]})

    assert rejected == []
    assert [p.person_id for p in ranking] == ["p1"]
    assert ranking[0].evidence_ticket_keys == ["MESOS-1"]
    assert ranking[0].found_by == ["vector"]
    assert ranking[0].matched_skills == ["Docker image build tooling"]
    assert seen["model"] == settings["llm.rerank_model"]
    assert seen["stage"] == settings["llm.query_stage"]


def test_a_reason_citing_another_candidates_ticket_is_rejected(monkeypatch):
    ranking, rejected, _ = _rerank_with(monkeypatch, {"ranking": [
        {"person_id": "p1", "fit": "strong", "reason": "Led the work in MESOS-9",
         "evidence_ticket_keys": ["MESOS-1"]},
    ]})

    assert ranking == []
    assert rejected == ["p1: cites evidence not in this person's contributions: MESOS-9"]


def test_a_declared_key_that_is_not_the_candidates_own_is_rejected(monkeypatch):
    ranking, rejected, _ = _rerank_with(monkeypatch, {"ranking": [
        {"person_id": "p1", "fit": "good", "reason": "Solid work",
         "evidence_ticket_keys": ["MESOS-4242"]},
    ]})

    assert ranking == []
    assert "MESOS-4242" in rejected[0]


def test_an_unevidenced_reason_is_rejected(monkeypatch):
    ranking, rejected, _ = _rerank_with(monkeypatch, {"ranking": [
        {"person_id": "p1", "fit": "strong", "reason": "Feels like a good fit."},
    ]})

    assert ranking == []
    assert rejected == ["p1: cites no evidence ticket key"]


def test_an_invented_person_is_rejected(monkeypatch):
    ranking, rejected, _ = _rerank_with(monkeypatch, {"ranking": [
        {"person_id": "MESOS:9999", "fit": "strong", "reason": "Great",
         "evidence_ticket_keys": ["MESOS-1"]},
    ]})

    assert ranking == []
    assert rejected == ["MESOS:9999: not among the ranked candidates"]


def test_a_duplicated_person_is_ranked_once(monkeypatch):
    entry = {"person_id": "p1", "fit": "strong", "reason": "Containerizer work",
             "evidence_ticket_keys": ["MESOS-1"]}
    ranking, rejected, _ = _rerank_with(monkeypatch, {"ranking": [entry, dict(entry)]})

    assert len(ranking) == 1
    assert rejected == ["p1: duplicate entry"]


def test_an_unknown_fit_falls_back_to_related(monkeypatch):
    ranking, _, _ = _rerank_with(monkeypatch, {"ranking": [
        {"person_id": "p1", "fit": "perfect", "reason": "MESOS-1 work",
         "evidence_ticket_keys": ["MESOS-1"]},
    ]})
    assert ranking[0].fit == "related"


def test_only_the_top_k_candidates_reach_the_prompt(monkeypatch):
    top_k = int(settings["retrieval.rerank_top_k"])
    candidates = []
    for index in range(top_k + 3):
        profile = candidate(f"p{index:02d}",
                            contributions=[contribution(f"c{index}", person=f"p{index:02d}")])
        profile.score = 1.0 - index / 100
        candidates.append(profile)
    captured = {}

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        captured["prompt"] = prompt
        return {"ranking": []}

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    rank.rerank("brief", ROLE, candidates)

    assert f'"person_id": "p{top_k - 1:02d}"' in captured["prompt"]
    assert f'"person_id": "p{top_k:02d}"' not in captured["prompt"]


def test_no_candidates_means_no_model_call(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the re-rank must not call the model with an empty shortlist")

    monkeypatch.setattr(rank, "call_json", forbidden)
    assert rank.rerank("brief", ROLE, []) == ([], [])


def test_the_prompt_shows_the_evidence_keys_the_model_is_allowed_to_cite():
    view = rank.profile_view(_shortlist()[0])
    assert view["contributions"][0]["evidence_tickets"] == ["MESOS-1", "MESOS-2"]
    assert rank.own_evidence_keys(_shortlist()[0]) == {"MESOS-1", "MESOS-2"}


def test_the_candidate_view_is_bounded_so_the_prompt_cannot_outgrow_the_ceiling():
    profile = candidate(
        specializations=[cap(f"spec {i}", "specialization", 30 - i) for i in range(20)],
        skills=[cap(f"skill {i}", "skill", 30 - i) for i in range(40)],
        contributions=[
            contribution(f"c{i}", keys=tuple(f"MESOS-{i * 100 + j}" for j in range(20)))
            for i in range(12)
        ],
    )

    view = rank.profile_view(profile)

    assert len(view["specializations"]) == settings["retrieval.rerank_specializations_per_candidate"]
    assert len(view["skills"]) == settings["retrieval.rerank_skills_per_candidate"]
    assert len(view["contributions"]) == settings["retrieval.rerank_contributions_per_candidate"]
    assert all(
        len(c["evidence_tickets"]) == settings["retrieval.rerank_evidence_keys_per_contribution"]
        for c in view["contributions"]
    )
    # Bounded views, but validation still checks a citation against every key owned.
    assert len(rank.own_evidence_keys(profile)) == 12 * 20
    assert view["skills"][0]["term"] == "skill 0"        # highest evidence first


# ---------- temporal contract ----------

def test_the_query_path_contains_no_wall_clock_call():
    """Benchmark leakage guard: recency comes from stored decay, never from today."""
    query_path = Path(__file__).resolve().parents[1] / "src" / "capgraph" / "query"
    for module in sorted(query_path.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for forbidden in ("date.today", "datetime.now", "datetime.utcnow", "date.fromtimestamp"):
            assert forbidden not in source, f"{module.name} uses {forbidden}"
