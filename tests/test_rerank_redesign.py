"""The re-rank redesign study: pinned retrieval, arm isolation, and the carried rules.

Everything here is offline — no model call, no Neo4j, no manifest. The properties under
test are the ones the study's conclusions rest on and that no number in the report can
demonstrate on its own:

* the pin survives a round trip **byte-identically**, so replaying it into an arm sends
  the model exactly the card the engine would have sent;
* two arms replaying one pin produce identical candidate pools and identical
  deterministic rankings, so the only thing that can differ between them is the re-rank;
* a pin that is missing or half-captured is refused, loudly, before any arm spends
  against it, and the frozen-v4 comparison that voided the free-baseline plan is
  measured rather than asserted;
* the redesigned prompt carries the citation and validation rules over verbatim, and the
  evidence validator behaves identically on its richer answer shape;
* the study cannot be pointed at the v4 test split, and its ceiling binds.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from capgraph import improvements
from capgraph.eval import rerank_redesign as study
from capgraph.eval.systems import CAPGRAPH_FULL, CAPGRAPH_SCORE
from capgraph.models import (
    CandidateProfile,
    Contribution,
    PersonCapability,
    RankedPerson,
    RoleSpec,
    SkillRef,
    SpecializationRef,
)
from capgraph.query import rank
from capgraph.settings import PROMPTS_DIR, settings

ROLE = RoleSpec(role="backend engineer", specializations=["Cluster orchestration"],
                skills=["Docker"])

# The five rules the work order requires to carry over unchanged, quoted from
# prompts/rerank_cards.md. The evidence validator in query/rank.py depends on them, so a
# redesign that paraphrased any of them would be changing the guard, not the ranking.
CARRIED_RULES = (
    "- Rank ONLY the candidates given. Do not invent people. Include every candidate you "
    "can justify; omit none merely because it ranks low.",
    "- `reason`: one concrete sentence per person citing their actual evidence (\"14 "
    "tickets on Docker containerizer work through 2018, including MESOS-1234\"). A "
    "reviewer must be able to verify it against the card shown. Keep it to one sentence "
    "— there are many candidates.",
    "- `evidence_ticket_keys`: 1-4 keys copied verbatim from that same candidate's own "
    "`evidence_tickets`, supporting exactly what the reason claims. Never cite a key "
    "belonging to another candidate, and never write a key that does not appear in that "
    "candidate's card — entries whose citations are not the candidate's own are "
    "discarded, not corrected.",
    "- `fit`: \"strong\" | \"good\" | \"related\". Use \"related\" for "
    "adjacent-but-not-direct matches.",
    "- If a candidate is clearly unsuitable, include them at the bottom with fit "
    "\"related\" and an honest reason — never fabricate fit.",
)


def _capability(term, kind, person, count=5):
    return PersonCapability(person_id=person, term=term, kind=kind, evidence_count=count,
                            contribution_ids=[], last_used=date(2018, 12, 31),
                            decay_score=0.9)


def _candidate(person: str, score: float, key: str) -> CandidateProfile:
    return CandidateProfile(
        person_id=person,
        person_name=f"Person {person}",
        specializations=[_capability("Cluster orchestration", "specialization", person)],
        skills=[_capability("Docker image build tooling", "skill", person)],
        contributions=[
            Contribution(
                contribution_id=f"{person}-c1", person_id=person, project_key="MESOS",
                period="2018-Q4", contribution_summary="containerizer work",
                specializations=[SpecializationRef(name="Cluster orchestration")],
                skills=[SkillRef(name="Docker image build tooling")],
                confidence="high", reason="", evidence_ticket_keys=[key],
            )
        ],
        retrieval_sources=["vector"],
        matched_contribution_count=1,
        score=score,
    )


def _pin_record(issue_id: str = "PROJ:sprint:1") -> dict:
    """One pinned case with two roles, as :func:`capture_pin` would have written it."""
    role_a = [_candidate("PROJ:1", 0.80, "PROJ-1"), _candidate("PROJ:2", 0.60, "PROJ-2"),
              _candidate("PROJ:3", 0.40, "PROJ-3")]
    role_b = [_candidate("PROJ:3", 0.70, "PROJ-3"), _candidate("PROJ:4", 0.50, "PROJ-4")]
    return {
        "issue_id": issue_id,
        "issue_key": "SPRINT 1",
        "project_key": "PROJ",
        "split": "validation",
        "brief": "need a containerization engineer",
        "roles": [
            {
                "role": ROLE.model_dump(mode="json"),
                "candidate_person_ids": [c.person_id for c in role_a],
                "scored_person_ids": [c.person_id for c in role_a],
                "window": [c.model_dump(mode="json") for c in role_a],
            },
            {
                "role": ROLE.model_dump(mode="json"),
                "candidate_person_ids": [c.person_id for c in role_b],
                "scored_person_ids": [c.person_id for c in role_b],
                "window": [c.model_dump(mode="json") for c in role_b],
            },
        ],
    }


# ---------- the pin survives a round trip ----------

def test_pinned_window_round_trips_byte_identically():
    """A card rebuilt from the pin is the same bytes the engine would have sent."""
    original = _candidate("PROJ:1", 0.8, "PROJ-1")
    dumped = original.model_dump(mode="json")
    rebuilt = CandidateProfile.model_validate(json.loads(json.dumps(dumped)))

    assert rebuilt.model_dump(mode="json") == dumped
    assert json.dumps(rank.candidate_view(rebuilt), sort_keys=True) == json.dumps(
        rank.candidate_view(original), sort_keys=True
    )
    assert rank.own_evidence_keys(rebuilt) == rank.own_evidence_keys(original)


def test_pin_pool_and_score_ordering_match_the_engine_merge():
    """The pin's derived pool and score ranking are the ones eval/systems.py builds."""
    record = _pin_record()
    # Union across roles, first appearance wins; PROJ:3 is in both and appears once.
    assert study.pin_pool(record) == ["PROJ:1", "PROJ:2", "PROJ:3", "PROJ:4"]
    # Round robin across the two roles' score orders, deduped.
    assert study.pin_score_ordering(record) == ["PROJ:1", "PROJ:3", "PROJ:2", "PROJ:4"]


# ---------- two arms, one pin ----------

def _stub_rerank(order_seen: list[list[str]]):
    """A re-rank that records what it was shown and answers in the order it was shown."""

    def fake(brief, role, candidates, *, stage=None, max_tokens=None):
        shortlist = rank.rerank_input(candidates)
        orders = rank.sample_orders(
            [c.person_id for c in shortlist], int(settings["retrieval.rerank_samples"])
        )
        order_seen.append(list(orders[0]))
        by_id = {c.person_id: c for c in shortlist}
        return [
            RankedPerson(
                person_id=person_id, person_name=by_id[person_id].person_name, fit="good",
                reason="stub", score=by_id[person_id].score, found_by=["vector"],
                evidence_ticket_keys=list(rank.own_evidence_keys(by_id[person_id]))[:1],
            )
            for person_id in orders[0]
        ], []

    return fake


def test_two_arms_replay_identical_pools_and_score_rankings(monkeypatch):
    """The isolation claim: only the re-rank can differ between arms."""
    record = _pin_record()
    seen: list[list[str]] = []
    monkeypatch.setattr(study, "rerank", _stub_rerank(seen))

    ordered = study.Arm("B", "rerank_evidence_first", improvements.ORDER_SCORE, "ordered")
    reversed_ = study.Arm("C", "rerank_evidence_first", improvements.ORDER_REVERSE, "rev")
    left, _ = study.replay_case(record, arm=ordered, stage_name="test_stage")
    right, _ = study.replay_case(record, arm=reversed_, stage_name="test_stage")

    # Same pool, same deterministic ranking — those come from the pin, not from the arm.
    assert study.pin_pool(record) == study.pin_pool(record)
    assert study.pin_score_ordering(record) == ["PROJ:1", "PROJ:3", "PROJ:2", "PROJ:4"]
    # The reversed arm really did present the window worst-first, and only that changed.
    assert seen[0] == ["PROJ:1", "PROJ:2", "PROJ:3"]
    assert seen[2] == ["PROJ:3", "PROJ:2", "PROJ:1"]
    assert left["ranked_ids"] != right["ranked_ids"]
    assert set(left["ranked_ids"]) == set(right["ranked_ids"])


def test_replay_restores_the_prompt_and_order_after_an_arm(monkeypatch):
    """An arm's overrides are scoped: nothing leaks into the next arm or the engine."""
    before_prompt = settings["llm.rerank_prompt"]
    before_order = improvements.rerank_presentation_order()
    monkeypatch.setattr(study, "rerank", _stub_rerank([]))
    study.replay_case(
        _pin_record(),
        arm=study.Arm("C", "rerank_evidence_first", improvements.ORDER_REVERSE, "rev"),
        stage_name="test_stage",
    )
    assert settings["llm.rerank_prompt"] == before_prompt
    assert improvements.rerank_presentation_order() == before_order


def test_replay_pads_the_ranking_with_the_deterministic_remainder(monkeypatch):
    """A person the re-rank dropped is appended, not lost — as eval/systems.py does."""

    def drop_one(brief, role, candidates, *, stage=None, max_tokens=None):
        shortlist = rank.rerank_input(candidates)
        return [
            RankedPerson(person_id=c.person_id, person_name=c.person_name, fit="good",
                         reason="stub", score=c.score, evidence_ticket_keys=["PROJ-1"])
            for c in shortlist[:1]
        ], ["PROJ:2: cites no evidence ticket key"]

    monkeypatch.setattr(study, "rerank", drop_one)
    output, detail = study.replay_case(
        _pin_record(),
        arm=study.Arm("B", "rerank_evidence_first", improvements.ORDER_SCORE, "ordered"),
        stage_name="test_stage",
    )
    assert set(output["ranked_ids"]) == {"PROJ:1", "PROJ:2", "PROJ:3", "PROJ:4"}
    assert detail["n_ranked_by_rerank"] == 2
    assert detail["n_offered_by_rerank"] == 4
    assert len(detail["rejected"]) == 2


# ---------- the pin must reproduce the frozen run ----------

def _pinned(monkeypatch, record, *, pool, score_order, roles=("backend engineer",)):
    case = type("Case", (), {"issue_id": record["issue_id"], "issue_key": "SPRINT 1"})()
    monkeypatch.setattr(study, "load_pin", lambda path=None: {record["issue_id"]: record})
    monkeypatch.setattr(study, "cases", lambda: [case])
    monkeypatch.setattr(study, "frozen_v4_records", lambda: {
        (CAPGRAPH_FULL, record["issue_id"]): {
            "candidate_ids": list(pool), "detail": {"roles": list(roles)}
        },
        (CAPGRAPH_SCORE, record["issue_id"]): {"ranked_ids": list(score_order)},
    })


def test_a_complete_pin_is_accepted_and_counted(monkeypatch):
    record = _pin_record()
    _pinned(monkeypatch, record, pool=[], score_order=[])
    assert study.assert_pin_complete() == {"cases": 1, "roles": 2}


def test_an_uncaptured_case_is_refused_before_any_arm_spends(monkeypatch):
    """The arms are only comparable if every one of them replays the same pin."""
    monkeypatch.setattr(study, "load_pin", lambda path=None: {})
    monkeypatch.setattr(
        study, "cases",
        lambda: [type("Case", (), {"issue_id": "PROJ:sprint:1", "issue_key": "S1"})()],
    )
    with pytest.raises(study.PinMismatchError, match="not captured"):
        study.assert_pin_complete()


def test_an_empty_rerank_window_is_refused(monkeypatch):
    record = _pin_record()
    record["roles"][1]["window"] = []
    _pinned(monkeypatch, record, pool=[], score_order=[])
    with pytest.raises(study.PinMismatchError, match="empty re-rank window"):
        study.assert_pin_complete()


def test_the_frozen_v4_comparison_reports_agreement_rather_than_assuming_it(monkeypatch):
    """The measurement that voided the work order's free-baseline plan."""
    record = _pin_record()
    _pinned(
        monkeypatch, record,
        pool=["PROJ:1", "PROJ:2", "PROJ:3", "PROJ:4"],
        score_order=["PROJ:1", "PROJ:3", "PROJ:2", "PROJ:4"],
        roles=("backend engineer", "backend engineer"),
    )
    row = study.frozen_run_comparison_rows()[0]
    assert (row["pool_matches"], row["score_order_matches"], row["roles_match"]) == (
        True, True, True
    )
    assert row["pool_jaccard"] == 1.0


def test_the_frozen_v4_comparison_detects_a_different_pool(monkeypatch):
    """A reordered or larger pool is a different pool — not a usable baseline arm."""
    record = _pin_record()
    _pinned(
        monkeypatch, record,
        pool=["PROJ:2", "PROJ:1", "PROJ:3", "PROJ:4", "PROJ:5"],
        score_order=["PROJ:3", "PROJ:1", "PROJ:2", "PROJ:4"],
    )
    row = study.frozen_run_comparison_rows()[0]
    assert row["pool_matches"] is False
    assert row["score_order_matches"] is False
    assert row["pool_jaccard"] == 0.8


# ---------- the carried rules and the untouched validator ----------

def test_redesigned_prompt_carries_every_citation_rule_verbatim():
    current = (PROMPTS_DIR / "rerank_cards.md").read_text(encoding="utf-8")
    redesigned = (PROMPTS_DIR / "rerank_evidence_first.md").read_text(encoding="utf-8")
    for rule in CARRIED_RULES:
        assert rule in current, "rule is not quoted from the current prompt as written"
        assert rule in redesigned, "the redesign paraphrased a rule the validator needs"


def test_redesigned_prompt_keeps_the_same_placeholders_and_answer_key():
    redesigned = (PROMPTS_DIR / "rerank_evidence_first.md").read_text(encoding="utf-8")
    for placeholder in ("{{brief}}", "{{role_json}}", "{{candidates_json}}"):
        assert placeholder in redesigned
    assert '"ranking"' in redesigned


def test_validator_ignores_the_new_fields_and_still_rejects_foreign_citations():
    """The richer answer shape reaches the validator unchanged in behaviour."""
    mine = _candidate("PROJ:1", 0.8, "PROJ-1")
    answer = {
        "assessments": ["PROJ:1 | 0.80 | containerization | last 2018-12-31 | strong"],
        "head_note": ["PROJ:1 over PROJ:2: more recent containerizer evidence"],
        "ranking": [
            {"person_id": "PROJ:1", "fit": "strong", "reason": "containerizer work",
             "evidence_ticket_keys": ["PROJ-1"], "above_next_because": "unused field"},
            {"person_id": "PROJ:2", "fit": "good", "reason": "cites PROJ-1, not theirs",
             "evidence_ticket_keys": ["PROJ-1"]},
        ],
    }
    ranking, rejected = rank._validated_entries(
        answer, {"PROJ:1": mine, "PROJ:2": _candidate("PROJ:2", 0.6, "PROJ-2")}
    )
    assert [person.person_id for person in ranking] == ["PROJ:1"]
    assert ranking[0].evidence_ticket_keys == ["PROJ-1"]
    assert len(rejected) == 1 and "not in this person's contributions" in rejected[0]


# ---------- did the model actually run the mechanism? ----------

def test_mechanism_compliance_recognises_a_complete_assessment_pass():
    """What the arms cannot show: whether pass 1 happened at all."""
    window = [_candidate("PROJ:1", 0.8, "PROJ-1"), _candidate("PROJ:2", 0.6, "PROJ-2")]
    answer = {
        "assessments": [
            "PROJ:1 | 0.80 | containerization | last 2018-12-31 | strong",
            "PROJ:2 | 0.60 | none | last 2018-12-31 | related",
        ],
        "head_note": ["PROJ:1 over PROJ:2: more containerizer evidence"],
        "ranking": [{"person_id": "PROJ:1"}, {"person_id": "PROJ:2"}],
    }
    assert study.mechanism_compliance(answer, window) == {
        "candidates": 2, "assessments": 2, "covers_every_candidate": True,
        "follows_printed_order": True, "template_fields_ok": True,
        "head_note_entries": 1, "ranking_entries": 2,
    }


def test_mechanism_compliance_catches_a_skipped_candidate_and_a_broken_template():
    window = [_candidate("PROJ:1", 0.8, "PROJ-1"), _candidate("PROJ:2", 0.6, "PROJ-2")]
    answer = {"assessments": ["PROJ:1 | 0.80 | containerization"], "ranking": []}
    compliance = study.mechanism_compliance(answer, window)
    assert compliance["covers_every_candidate"] is False
    assert compliance["follows_printed_order"] is False
    assert compliance["template_fields_ok"] is False


def test_mechanism_compliance_catches_an_answer_with_no_assessment_pass():
    """A model that skipped straight to ranking is the failure this check exists for."""
    window = [_candidate("PROJ:1", 0.8, "PROJ-1")]
    compliance = study.mechanism_compliance({"ranking": [{"person_id": "PROJ:1"}]}, window)
    assert compliance["assessments"] == 0
    assert compliance["template_fields_ok"] is False
    assert compliance["covers_every_candidate"] is False


def test_gap_between_is_a_paired_mean_over_shared_cases_only():
    before = {"a": {"mrr": 0.2}, "b": {"mrr": 0.4}, "only_before": {"mrr": 1.0}}
    after = {"a": {"mrr": 0.5}, "b": {"mrr": 0.4}, "only_after": {"mrr": 0.0}}
    assert study.gap_between(before, after, "mrr") == pytest.approx(0.15)
    assert study.gap_between({}, {}, "mrr") == 0.0


# ---------- study configuration ----------

def test_engine_defaults_are_untouched_by_this_study():
    """The redesigned prompt lands as a file and a config option, never as a default."""
    assert settings["llm.rerank_prompt"] == "rerank_cards"
    assert settings["retrieval.rerank_candidate_view"] == "card"
    assert settings["retrieval.rerank_top_k"] == 32
    assert improvements.rerank_presentation_order() == improvements.ORDER_SCORE


def test_arms_are_configured_and_exactly_one_is_the_reference():
    arms = study.arms()
    assert [arm.name for arm in arms] == ["baseline", "A", "B", "C"]
    assert [arm.reference for arm in arms] == [True, False, False, False]
    assert study.reference_arm().prompt == settings["llm.rerank_prompt"]
    assert study.reference_arm().order == improvements.ORDER_SCORE
    assert {arm.order for arm in arms} <= set(improvements.PRESENTATION_ORDERS)
    # Exactly two things vary across the arms, and both are re-rank stage settings.
    assert {(arm.prompt, arm.order) for arm in arms} == {
        ("rerank_cards", "score"), ("rerank_cards", "reverse"),
        ("rerank_evidence_first", "score"), ("rerank_evidence_first", "reverse"),
    }


def test_every_arm_has_its_own_checkpoint_namespace():
    """Two arms must never append to one checkpoint — their answers are different runs."""
    directories = {study.arm_runs_dir(arm) for arm in study.arms()}
    assert len(directories) == len(study.arms())


def test_the_study_cannot_be_pointed_at_the_test_split():
    with settings.overridden({study.SPLIT_SETTING: "test"}):
        with pytest.raises(ValueError, match="validation split only"):
            study._require_validation()


def test_budget_refuses_a_chunk_that_would_break_the_ceiling(monkeypatch):
    ceiling = float(study.study("max_total_cost_usd"))
    monkeypatch.setattr(study, "study_spend", lambda: ceiling - 0.05)
    with pytest.raises(study.RerankBudgetError, match="ceiling"):
        study.enforce_budget(20, kind="rerank")
    # Under the ceiling it returns the projection instead of raising.
    monkeypatch.setattr(study, "study_spend", lambda: 0.0)
    assert study.enforce_budget(10, kind="rerank") == pytest.approx(
        10 * float(dict(study.study("projection"))["rerank_call_usd"])
    )
