"""The re-rank probes study: the two new levers, and the discipline around spending.

Everything here is offline — no model call, no Neo4j, no manifest. The properties under
test are the ones the study's conclusions and its authorization rest on, and that no
number in the report can demonstrate on its own:

* the two new re-rank levers are **off by default**, so nothing in the engine's measured
  configuration moves because this study exists;
* the hybrid view details exactly the head of the *deterministic* ordering, and details
  the same people whichever way the window is presented — otherwise the view and the G7
  order flag would be confounded;
* a permutation answer makes no claims at all: no reason, no citation, nothing for the
  evidence validator to accept, and an id that was not in the window is still rejected;
* an arm cannot spend before it is pre-registered, cannot spend past the ceiling, and a
  third arm cannot spend after two produced no signal;
* an arm's overrides — including the work order's scoped per-call ceiling — are scoped
  to that arm and leak into nothing;
* the study cannot be pointed at the v4 test split.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from capgraph import improvements
from capgraph.eval import rerank_probes as study
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

# The five ranking rules the hybrid prompt must carry over unchanged, quoted from
# prompts/rerank_cards.md. The evidence validator in query/rank.py depends on them, so a
# probe that paraphrased any of them would be changing the guard, not the evidence.
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


def _window() -> list[CandidateProfile]:
    return [_candidate("PROJ:1", 0.80, "PROJ-1"), _candidate("PROJ:2", 0.60, "PROJ-2"),
            _candidate("PROJ:3", 0.40, "PROJ-3"), _candidate("PROJ:4", 0.20, "PROJ-4")]


def _pin_record(issue_id: str = "PROJ:sprint:1") -> dict:
    role_a = _window()[:3]
    role_b = [_candidate("PROJ:3", 0.70, "PROJ-3"), _candidate("PROJ:4", 0.50, "PROJ-4")]
    return {
        "issue_id": issue_id, "issue_key": "SPRINT 1", "project_key": "PROJ",
        "split": "validation", "brief": "need a containerization engineer",
        "roles": [
            {"role": ROLE.model_dump(mode="json"),
             "candidate_person_ids": [c.person_id for c in role_a],
             "scored_person_ids": [c.person_id for c in role_a],
             "window": [c.model_dump(mode="json") for c in role_a]},
            {"role": ROLE.model_dump(mode="json"),
             "candidate_person_ids": [c.person_id for c in role_b],
             "scored_person_ids": [c.person_id for c in role_b],
             "window": [c.model_dump(mode="json") for c in role_b]},
        ],
    }


# ---------- the levers are off by default ----------

def test_the_two_new_levers_are_off_in_the_shipped_configuration():
    """Nothing this study adds changes what the engine's measured configuration does."""
    assert settings["retrieval.rerank_candidate_view"] == rank.VIEW_CARD
    assert settings["retrieval.rerank_mode"] == rank.RERANK_LISTWISE
    assert rank.rerank_mode() == rank.RERANK_LISTWISE


def test_by_default_the_window_renderer_is_the_per_candidate_one():
    """candidate_views is a pass-through until the hybrid view is asked for."""
    window = _window()
    assert rank.candidate_views(window) == [rank.candidate_view(c) for c in window]


def test_the_permutation_allowance_only_applies_in_permutation_mode():
    assert rank.rerank_max_tokens(32) == rank.rerank_output_tokens(32)
    with settings.overridden({"retrieval.rerank_mode": rank.RERANK_PERMUTATION}):
        assert rank.rerank_max_tokens(32) == int(
            settings["llm.rerank_permutation_output_tokens"]
        )
        # The whole point: a permutation answer does not grow with the window.
        assert rank.rerank_max_tokens(4) == rank.rerank_max_tokens(32)


def test_an_unknown_rerank_mode_is_refused_rather_than_defaulted():
    with settings.overridden({"retrieval.rerank_mode": "setwise"}):
        with pytest.raises(ValueError, match="rerank_mode"):
            rank.rerank_mode()


# ---------- the hybrid view ----------

def test_the_hybrid_view_details_exactly_the_deterministic_head():
    """Full profiles for the top k by score, cards for everyone else."""
    window = _window()
    with settings.overridden({"retrieval.rerank_candidate_view": rank.VIEW_HYBRID,
                              "retrieval.rerank_hybrid_detail_top_k": 2}):
        views = rank.candidate_views(window)
    # A profile view carries contribution summaries; a card never does.
    assert [("contributions" in view) for view in views] == [True, True, False, False]
    assert views[0]["person_id"] == "PROJ:1" and views[3]["person_id"] == "PROJ:4"


def test_the_hybrid_head_is_chosen_by_score_not_by_presentation_position():
    """Reversing the window must detail the same people, or the arms are confounded."""
    window = _window()
    with settings.overridden({"retrieval.rerank_candidate_view": rank.VIEW_HYBRID,
                              "retrieval.rerank_hybrid_detail_top_k": 2}):
        forward = rank.candidate_views(window)
        backward = rank.candidate_views(list(reversed(window)))
    detailed = {v["person_id"] for v in forward if "contributions" in v}
    assert detailed == {v["person_id"] for v in backward if "contributions" in v}
    assert detailed == {"PROJ:1", "PROJ:2"}


def test_a_hybrid_head_wider_than_the_window_details_everyone():
    window = _window()
    with settings.overridden({"retrieval.rerank_candidate_view": rank.VIEW_HYBRID,
                              "retrieval.rerank_hybrid_detail_top_k": 99}):
        views = rank.candidate_views(window)
    assert all("contributions" in view for view in views)


def test_one_candidate_cannot_be_rendered_in_the_hybrid_view():
    """A silent fallback to the card would misreport what the model was sent."""
    with settings.overridden({"retrieval.rerank_candidate_view": rank.VIEW_HYBRID}):
        with pytest.raises(ValueError, match="candidate_views"):
            rank.candidate_view(_candidate("PROJ:1", 0.8, "PROJ-1"))


def test_the_hybrid_prompt_carries_the_ranking_rules_verbatim():
    """The citation rules are the guard, so a probe may not paraphrase them."""
    text = (PROMPTS_DIR / "rerank_hybrid_cards.md").read_text(encoding="utf-8")
    cards = (PROMPTS_DIR / "rerank_cards.md").read_text(encoding="utf-8")
    for rule in CARRIED_RULES:
        assert rule in cards, "the quoted rule has drifted from prompts/rerank_cards.md"
        assert rule in text
    # And the answer schema is the same one query/rank.py validates.
    assert '"ranking": [' in text
    assert '{"person_id": "...", "fit": "strong", "reason": "...", ' \
           '"evidence_ticket_keys": ["PROJ-123", "PROJ-456"]}' in text


def test_the_hybrid_view_never_widens_what_a_candidate_may_cite():
    """More keys are shown, but the validator still checks against the same set."""
    candidate = _candidate("PROJ:1", 0.8, "PROJ-1")
    with settings.overridden({"retrieval.rerank_candidate_view": rank.VIEW_HYBRID,
                              "retrieval.rerank_hybrid_detail_top_k": 1}):
        rank.candidate_views([candidate])
    keys, problem = rank.validated_evidence(
        {"reason": "worked on PROJ-2", "evidence_ticket_keys": ["PROJ-2"]}, candidate
    )
    assert keys == [] and "not in this person's contributions" in problem


# ---------- the permutation answer ----------

def test_a_permutation_answer_produces_entries_that_claim_nothing():
    window = _window()
    by_id = {c.person_id: c for c in window}
    ranking, rejected = rank._permutation_entries(
        {"order": ["PROJ:3", "PROJ:1"]}, by_id
    )
    assert [p.person_id for p in ranking] == ["PROJ:3", "PROJ:1"]
    assert rejected == []
    # The safety argument, structurally: nothing to validate, so nothing to smuggle.
    assert all(p.reason == "" for p in ranking)
    assert all(p.evidence_ticket_keys == [] for p in ranking)
    assert all(p.score == by_id[p.person_id].score for p in ranking)


def test_a_permutation_answer_still_cannot_name_someone_who_was_not_shown():
    by_id = {c.person_id: c for c in _window()}
    ranking, rejected = rank._permutation_entries(
        {"order": ["PROJ:1", "PROJ:99", "PROJ:1", ""]}, by_id
    )
    assert [p.person_id for p in ranking] == ["PROJ:1"]
    assert "PROJ:99: not among the ranked candidates" in rejected
    assert "PROJ:1: duplicate entry" in rejected
    assert "<missing id>: not among the ranked candidates" in rejected


def test_permutation_self_consistency_is_refused_not_silently_combined(monkeypatch):
    """Shuffle-and-vote over permutations is a measured dead end on this project."""
    with settings.overridden({"retrieval.rerank_mode": rank.RERANK_PERMUTATION,
                              "retrieval.rerank_samples": 3}):
        with pytest.raises(ValueError, match="measured and rejected"):
            rank.rerank("brief", ROLE, _window(), stage="test_stage")


def test_permutation_mode_sends_the_configured_prompt_and_parses_its_answer(monkeypatch):
    sent: dict[str, object] = {}

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        sent.update(prompt=prompt, model=model, max_tokens=max_tokens, purpose=purpose)
        return {"order": ["PROJ:2", "PROJ:1", "PROJ:3", "PROJ:4"]}

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    with settings.overridden({"retrieval.rerank_mode": rank.RERANK_PERMUTATION,
                              "llm.rerank_prompt": "rerank_permutation"}):
        ranking, rejected = rank.rerank(
            "need a containerization engineer", ROLE, _window(), stage="test_stage"
        )

    assert [p.person_id for p in ranking] == ["PROJ:2", "PROJ:1", "PROJ:3", "PROJ:4"]
    assert rejected == []
    assert sent["max_tokens"] == int(settings["llm.rerank_permutation_output_tokens"])
    assert sent["purpose"] == rank.PURPOSE
    # The real prompt, rendered by the same builder the call uses: the brief and every
    # candidate are in it, and it asks for an ordering rather than entries.
    prompt = str(sent["prompt"])
    assert "need a containerization engineer" in prompt
    assert '"order"' in prompt and '"ranking"' not in prompt
    assert all(c.person_id in prompt for c in _window())


def test_the_permutation_prompt_asks_for_no_prose():
    text = (PROMPTS_DIR / "rerank_permutation.md").read_text(encoding="utf-8")
    for placeholder in ("{{brief}}", "{{role_json}}", "{{candidates_json}}"):
        assert placeholder in text
    assert '"order"' in text
    assert "Do not write reasons" in text
    # It must not ask for the fields the validator would have to check.
    assert "evidence_ticket_keys" not in text


# ---------- arms are scoped, and cannot spend before they are allowed to ----------

def _arm(name="S1", **kwargs) -> study.ProbeArm:
    return study.ProbeArm(
        name=name, label="test arm", method="test", citation="none",
        prompt=kwargs.pop("prompt", "rerank_cards"), **kwargs
    )


def _stub_rerank():
    def fake(brief, role, candidates, *, stage=None, max_tokens=None):
        shortlist = rank.rerank_input(candidates)
        return [
            RankedPerson(person_id=c.person_id, person_name=c.person_name, fit="good",
                         reason="stub", score=c.score,
                         evidence_ticket_keys=list(rank.own_evidence_keys(c))[:1])
            for c in shortlist
        ], []
    return fake


def test_an_arms_overrides_are_scoped_including_the_per_call_ceiling(monkeypatch):
    """The work order's scoped raise must not survive the arm that asked for it."""
    before = {
        "ceiling": settings["llm.max_call_cost_usd"],
        "model": settings["llm.rerank_model"],
        "view": settings["retrieval.rerank_candidate_view"],
        "mode": settings["retrieval.rerank_mode"],
        "prompt": settings["llm.rerank_prompt"],
        "order": improvements.rerank_presentation_order(),
    }
    monkeypatch.setattr(study, "rerank", _stub_rerank())
    study.replay_case(_pin_record(), arm=_arm(
        prompt="rerank_permutation", model="openai/gpt-5.6-sol", mode="permutation",
        view="card", max_call_cost_usd=0.15, order=improvements.ORDER_REVERSE,
    ))
    assert settings["llm.max_call_cost_usd"] == before["ceiling"]
    assert settings["llm.rerank_model"] == before["model"]
    assert settings["retrieval.rerank_candidate_view"] == before["view"]
    assert settings["retrieval.rerank_mode"] == before["mode"]
    assert settings["llm.rerank_prompt"] == before["prompt"]
    assert improvements.rerank_presentation_order() == before["order"]


def test_the_scoped_ceiling_is_only_applied_by_the_arm_that_declares_it():
    plain, raised = _arm("S2"), _arm("S1", max_call_cost_usd=0.15)
    assert "llm.max_call_cost_usd" not in study._overrides(plain)
    assert study._overrides(raised)["llm.max_call_cost_usd"] == 0.15


def test_two_arms_cannot_share_a_checkpoint():
    """Prompt, model, view and answer mode all enter the configuration digest."""
    from capgraph.eval.run_eval import config_digest

    base = _arm("S2", prompt="rerank_hybrid_cards", view="hybrid", hybrid_detail_top_k=8)
    other = _arm("S1", prompt="rerank_permutation", model="openai/gpt-5.6-sol",
                 mode="permutation")
    assert config_digest(study.arm_config(base)) != config_digest(study.arm_config(other))
    widened = _arm("S2", prompt="rerank_hybrid_cards", view="hybrid",
                   hybrid_detail_top_k=16)
    assert config_digest(study.arm_config(base)) != config_digest(study.arm_config(widened))


def test_replay_pads_the_ranking_and_records_the_reorder_only_label(monkeypatch):
    monkeypatch.setattr(study, "rerank", _stub_rerank())
    _, detail = study.replay_case(_pin_record(), arm=_arm(reorder_only=True))
    assert detail["reorder_only"] is True
    output, _ = study.replay_case(_pin_record(), arm=_arm(reorder_only=False))
    assert set(output["ranked_ids"]) == {"PROJ:1", "PROJ:2", "PROJ:3", "PROJ:4"}


# ---------- pre-registration, the gate, and the ceiling ----------

def test_an_arm_must_be_pre_registered_before_it_can_spend(tmp_path):
    report = tmp_path / "rerank-probes-report.md"
    report.write_text("# probes\n\n### Arm S2 — rich evidence\n", encoding="utf-8")
    assert study.preregistered_arms(report) == {"S2"}
    study.assert_preregistered(study.arm_named("S2"), report)
    with pytest.raises(study.PreRegistrationError, match="not pre-registered"):
        study.assert_preregistered(study.arm_named("S1"), report)


def test_a_missing_report_pre_registers_nothing(tmp_path):
    assert study.preregistered_arms(tmp_path / "absent.md") == set()


def test_the_ceiling_refuses_a_chunk_that_would_break_the_authorization(monkeypatch):
    monkeypatch.setattr(study, "study_spend", lambda: 14.90)
    with pytest.raises(study.ProbeBudgetError, match="ceiling"):
        study.enforce_budget(54, arm=_arm(projected_call_usd=0.11))
    # And it lets through what fits.
    monkeypatch.setattr(study, "study_spend", lambda: 0.0)
    assert study.enforce_budget(4, arm=_arm(projected_call_usd=0.11)) == pytest.approx(0.44)


def _gate(monkeypatch, deltas):
    """Two completed arms whose per-case metrics differ from the baseline by `deltas`."""
    arms = [_arm(name) for name in ("S1", "S2")]
    base = {f"case{i}": {"hit_at_1": 0.0, "mrr": 0.2} for i in range(10)}
    monkeypatch.setattr(study, "completed_arms", lambda: arms)
    monkeypatch.setattr(study, "baseline_per_case", lambda system=None: base)

    def metrics(arm, system=None):
        delta = deltas[arm.name]
        return {
            case: {"hit_at_1": values["hit_at_1"] + (delta if i < 5 else 0.0),
                   "mrr": values["mrr"] + (delta if i < 5 else 0.0)}
            for i, (case, values) in enumerate(base.items())
        }

    monkeypatch.setattr(study, "arm_metrics", metrics)
    return arms


def test_two_dead_arms_close_the_gate_and_refuse_a_third(monkeypatch):
    _gate(monkeypatch, {"S1": 0.0, "S2": 0.0})
    assert study.gate_open() is False
    with pytest.raises(study.SequencingGateError, match="sequencing gate is closed"):
        study.assert_gate_open(_arm("S3"))


def test_one_arm_beyond_the_floor_keeps_the_gate_open(monkeypatch):
    # +0.5 on half the cases is a paired mean of +0.25, far beyond either floor.
    _gate(monkeypatch, {"S1": 0.0, "S2": 0.5})
    assert study.gate_open() is True
    study.assert_gate_open(_arm("S3"))
    rows = {row["arm"].name: row for row in study.gate_rows()}
    assert rows["S2"]["signal"] is True and rows["S1"]["signal"] is False
    assert rows["S2"]["hit_at_1"]["beyond_floor"] is True
    assert rows["S2"]["hit_at_1"]["wins"] == 5 and rows["S2"]["hit_at_1"]["losses"] == 0


def test_a_movement_inside_the_floor_is_not_signal(monkeypatch):
    # +0.02 on half the cases: a paired mean of +0.01, inside both floors.
    _gate(monkeypatch, {"S1": 0.02, "S2": 0.02})
    assert study.gate_open() is False


def test_an_arm_that_already_ran_is_never_blocked_by_the_gate(monkeypatch):
    arms = _gate(monkeypatch, {"S1": 0.0, "S2": 0.0})
    study.assert_gate_open(arms[0])          # resuming S1 must stay possible


def test_the_floors_are_the_ones_benchmark_v4_measured():
    """Not v1's borrowed 0.100 — that gauge is too loose for Hit@1 and too tight elsewhere."""
    assert study.FLOORS["hit_at_1"] == pytest.approx(0.0357)
    assert study.FLOORS["mrr"] == pytest.approx(0.0341)
    assert study.GATE_METRICS == ("hit_at_1", "mrr")


# ---------- the test split is unreachable ----------

def test_the_study_refuses_any_split_but_validation():
    with settings.overridden({"eval.rerank_probes.split": "test"}):
        with pytest.raises(ValueError, match="validation split only"):
            study._require_validation()


def test_the_pin_and_baseline_are_the_rerank_redesign_ones():
    """Named rather than restated, so the baseline cannot silently become another arm."""
    assert study.pin_path().parts[-3:] == ("rerank_redesign", "pin", "validation.jsonl")
    assert study.baseline_arm().prompt == "rerank_cards"
    assert study.baseline_arm().order == improvements.ORDER_SCORE
    assert study.baseline_arm().reference is True


def test_every_configured_arm_is_well_formed():
    configured = study.arms()
    assert [arm.name for arm in configured] == ["S1", "S2"]
    assert len(configured) <= 4, "the work order authorizes at most four paid arms"
    for arm in configured:
        assert (PROMPTS_DIR / f"{arm.prompt}.md").exists()
        assert arm.citation and arm.method
        assert arm.order in improvements.PRESENTATION_ORDERS
        if arm.mode is not None:
            assert arm.mode in rank.RERANK_MODES
        if arm.view is not None:
            assert arm.view in rank.VIEWS


def test_the_studys_own_ceiling_is_the_authorized_one():
    assert study.ceiling() == pytest.approx(15.0)
    assert study.stage() == "rerank_probes"


# ---------- the report keeps what was written by hand ----------

def test_the_report_regenerates_the_middle_and_keeps_both_ends(tmp_path, monkeypatch):
    """A pre-registration must survive every later render, and so must the verdict."""
    report = tmp_path / "report.md"
    report.write_text(
        "# probes\n\n### Arm S1 — pre-registered before spending\n\n"
        f"{study.MEASURED_BEGIN}\n\nstale numbers that must not survive\n\n"
        f"{study.MEASURED_END}\n\n## Recommendation\n\nwritten once, by hand\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(study, "measured_sections", lambda: ["## Arms", "", "fresh"])
    out = study.render_report(report)

    assert "### Arm S1 — pre-registered before spending" in out
    assert "## Recommendation" in out and "written once, by hand" in out
    assert "fresh" in out and "stale numbers that must not survive" not in out
    # And rendering the result again is a fixed point.
    report.write_text(out, encoding="utf-8")
    assert study.render_report(report) == out


def test_a_report_with_no_markers_is_treated_as_all_pre_registration(tmp_path, monkeypatch):
    report = tmp_path / "report.md"
    report.write_text("# probes\n\n### Arm S1 — only this\n", encoding="utf-8")
    monkeypatch.setattr(study, "measured_sections", lambda: ["measured"])
    out = study.render_report(report)
    assert out.index("### Arm S1") < out.index(study.MEASURED_BEGIN) < out.index("measured")


# ---------- the miss decomposition ----------

def test_the_miss_decomposition_splits_the_bucket_the_probes_aim_at(monkeypatch):
    """A truth person in the window but ranked low is the bucket; not-retrieved is not."""
    record = _pin_record()
    cases = [
        type("Case", (), {"issue_id": "a", "issue_key": "A", "project_key": "PROJ",
                          "truth_person_ids": ["PROJ:3"]})(),
        type("Case", (), {"issue_id": "b", "issue_key": "B", "project_key": "PROJ",
                          "truth_person_ids": ["PROJ:1"]})(),
        type("Case", (), {"issue_id": "c", "issue_key": "C", "project_key": "PROJ",
                          "truth_person_ids": ["PROJ:99"]})(),
    ]
    monkeypatch.setattr(study, "cases", lambda: cases)
    monkeypatch.setattr(study, "load_pin",
                        lambda: {case.issue_id: {**record, "issue_id": case.issue_id}
                                 for case in cases})
    monkeypatch.setattr(study, "load_checkpoint", lambda split, runs_dir: {
        ("capgraph_full", "a"): {"ranked_ids": ["PROJ:1", "PROJ:2", "PROJ:3"]},
        ("capgraph_full", "b"): {"ranked_ids": ["PROJ:1", "PROJ:2", "PROJ:3"]},
        ("capgraph_full", "c"): {"ranked_ids": ["PROJ:1", "PROJ:2", "PROJ:3"]},
    })
    diag = study.miss_decomposition()
    assert diag["cases"] == 3
    assert diag["misses"] == 2                     # a ranked 3rd, c not ranked at all
    assert diag["shown_but_ranked_low"] == 1       # only a had a truth person shown
    assert diag["outside_pool"] == 1               # c's truth person is nowhere
    # PROJ:3 is 3rd in role A's window and 1st in role B's, so a top-1 head reaches it.
    assert diag["reachable_by_detail_top_k"][4] == 1
    assert json.dumps(diag["reachable_by_detail_top_k"], sort_keys=True)


def test_the_mechanism_check_separates_fixes_the_head_could_reach(monkeypatch):
    """A targeted mechanism should only fix cases whose truth person it was shown."""
    arm = _arm("S2", view="hybrid", hybrid_detail_top_k=2)
    monkeypatch.setattr(study, "completed_arms", lambda: [arm])
    monkeypatch.setattr(study, "miss_decomposition", lambda: {"rows": [
        # in the detailed head (position 1), and the arm fixed it
        {"issue_id": "a", "issue_key": "A", "best_window_position": 1},
        # in the head, and the arm did not fix it
        {"issue_id": "b", "issue_key": "B", "best_window_position": 2},
        # below the head — the mechanism never saw this person's detail
        {"issue_id": "c", "issue_key": "C", "best_window_position": 9},
        # the arm broke this one
        {"issue_id": "d", "issue_key": "D", "best_window_position": 1},
    ]})
    monkeypatch.setattr(study, "baseline_per_case", lambda system=None: {
        "a": {"hit_at_1": 0.0}, "b": {"hit_at_1": 0.0},
        "c": {"hit_at_1": 0.0}, "d": {"hit_at_1": 1.0},
    })
    monkeypatch.setattr(study, "arm_metrics", lambda a, system=None: {
        "a": {"hit_at_1": 1.0}, "b": {"hit_at_1": 0.0},
        "c": {"hit_at_1": 0.0}, "d": {"hit_at_1": 0.0},
    })
    row = study.mechanism_rows()[0]
    assert row["fixed"] == 1 and row["fixed_keys"] == ["A"]
    assert row["broke"] == 1 and row["broke_keys"] == ["D"]
    assert (row["reachable_fixed"], row["reachable"]) == (1, 2)
    assert (row["unreachable_fixed"], row["unreachable"]) == (0, 1)


def test_an_arm_without_a_detail_head_reports_no_reachability_split(monkeypatch):
    """S1 ranks the whole window, so 'where the mechanism applied' has no meaning."""
    arm = _arm("S1", mode="permutation")
    monkeypatch.setattr(study, "completed_arms", lambda: [arm])
    monkeypatch.setattr(study, "miss_decomposition", lambda: {"rows": [
        {"issue_id": "a", "issue_key": "A", "best_window_position": 1},
    ]})
    monkeypatch.setattr(study, "baseline_per_case", lambda system=None: {"a": {"hit_at_1": 1.0}})
    monkeypatch.setattr(study, "arm_metrics", lambda a, system=None: {"a": {"hit_at_1": 0.0}})
    row = study.mechanism_rows()[0]
    assert row["broke"] == 1 and row["reachable"] is None and row["unreachable"] is None
