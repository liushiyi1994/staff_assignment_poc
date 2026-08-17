"""The deterministic-side sweeps: pinned parses, the lever transforms, and the gates.

Everything here is offline — no model call, no Neo4j, no manifest. What is under test is
the discipline the study's conclusions rest on, which no number in the report can
demonstrate on its own:

* **the parses are pinned.** Every condition rebuilds its roles from one pin, and the
  digest that proves it is a function of the rebuilt :class:`RoleSpec` objects, not of
  the pin's bytes — so a change in how the pin is read would show up too, and a
  condition's flags cannot move it;
* **the noise floor really is a repeat.** The arm it runs is the rerank-redesign
  reference arm itself, so "identical prompt, identical order" is structural rather
  than a copied string;
* **the transforms do only what they claim.** The G6 control scales
  ``specialization_match`` and nothing else, by a scale measured from the lever's own
  output; the pool and window diffs report the moves a lever makes;
* **the two window measures stay apart.** Window *hit rate* and window *recall* are
  different questions on multi-person truth, and conflating them is the easiest way to
  overstate a retrieval lever;
* **the gates are code.** Each lever's tier-2 test is evaluated from the measurements,
  so the report cannot claim a gate the numbers do not support;
* **the study cannot be pointed at the v4 test split, and its ceiling binds.**
"""
from __future__ import annotations

import json

import pytest

from capgraph import improvements
from capgraph.eval import rerank_redesign as rr
from capgraph.eval import sweeps
from capgraph.settings import settings

WEIGHTS = {
    "specialization_match": 0.25,
    "skill_overlap": 0.30,
    "recency": 0.40,
    "evidence_strength": 0.05,
}


def _role(role: str, person_parts: dict[str, dict[str, float]]) -> sweeps.RoleReplay:
    ordered = sorted(person_parts)
    return sweeps.RoleReplay(
        role=role,
        parts=person_parts,
        sources={person: ["structured"] for person in ordered},
        engine_scores={person: 0.0 for person in ordered},
        candidate_person_ids=ordered,
        scored_person_ids=ordered,
    )


def _case(issue_id: str, truth: set[str], roles) -> sweeps.CaseReplay:
    return sweeps.CaseReplay(
        issue_id=issue_id,
        issue_key=f"KEY-{issue_id}",
        project_key="MESOS",
        truth=frozenset(truth),
        roles=tuple(roles),
    )


def _parts(spec: float, skill: float = 0.5, recency: float = 0.5, evidence: float = 0.5):
    return {
        "specialization_match": spec,
        "skill_overlap": skill,
        "recency": recency,
        "evidence_strength": evidence,
    }


# ---------- the pinned parses ----------

def test_roles_of_rebuilds_the_pin_exactly():
    """A condition replays the pin's roles, not a paraphrase of them."""
    record = {
        "roles": [
            {"role": {"role": "backend engineer", "specializations": ["Cluster orchestration"],
                      "skills": ["Docker"], "count": 2}},
            {"role": {"role": "SRE", "specializations": [], "skills": ["Kubernetes"],
                      "count": 1}},
        ]
    }
    roles = sweeps.roles_of(record)
    assert [role.model_dump(mode="json") for role in roles] == [
        entry["role"] for entry in record["roles"]
    ]
    assert roles[0].count == 2


def test_parses_digest_is_stable_and_flag_independent():
    """The lever may move retrieval and scoring; it may never move what was asked for."""
    pinned = {
        "a": {"roles": [{"role": {"role": "backend engineer", "specializations": ["X"],
                                  "skills": ["Y"], "count": 1}}]},
        "b": {"roles": [{"role": {"role": "SRE", "specializations": [], "skills": ["Z"],
                                  "count": 3}}]},
    }
    digest = sweeps.parses_digest(pinned)
    assert digest == sweeps.parses_digest(dict(reversed(list(pinned.items()))))
    for condition in sweeps.conditions():
        with improvements.overridden(condition.flags):
            assert sweeps.parses_digest(pinned) == digest


def test_parses_digest_moves_when_a_parse_moves():
    """A digest that could not notice a changed parse would prove nothing."""
    pinned = {"a": {"roles": [{"role": {"role": "backend engineer", "specializations": ["X"],
                                        "skills": ["Y"], "count": 1}}]}}
    changed = {"a": {"roles": [{"role": {"role": "backend engineer", "specializations": ["X"],
                                         "skills": ["Y", "Z"], "count": 1}}]}}
    assert sweeps.parses_digest(pinned) != sweeps.parses_digest(changed)


def test_the_study_reuses_the_rerank_redesign_pin_read_only():
    assert sweeps.source_pin_path().parent.name == "pin"
    assert "rerank_redesign" in str(sweeps.source_pin_path())
    # Nothing this study writes may land in a frozen namespace.
    root = sweeps.root()
    assert root.name == "sweeps"
    for condition in sweeps.conditions():
        for path in (condition.checkpoint, condition.sidecar, condition.pin_path):
            assert root in path.parents
    assert root in sweeps.paid_runs_dir("noise_floor").parents


# ---------- the noise floor is a repeat, not a lookalike ----------

def test_the_noise_floor_runs_the_rerank_redesign_reference_arm():
    """"Identical prompt, identical order" is structural here, not a copied string."""
    arm = rr.reference_arm()
    assert arm.reference is True
    assert arm.order == improvements.ORDER_SCORE
    config = sweeps.paid_config(
        "noise_floor", pin_path=sweeps.source_pin_path(), stage_name="noise_floor"
    )
    assert config["rerank_prompt"] == arm.prompt
    assert config["rerank_presentation_order"] == arm.order
    assert config["pinned_retrieval"] is True
    assert config["parses_digest"] == sweeps.parses_digest()


def test_two_arms_on_different_pins_cannot_share_a_checkpoint():
    from capgraph.eval.run_eval import config_digest

    left = sweeps.paid_config("noise_floor", pin_path=sweeps.source_pin_path(),
                              stage_name="noise_floor")
    right = sweeps.paid_config("g3a_df3", pin_path=sweeps.condition_named("g3a_df3").pin_path,
                               stage_name="sweep_val",
                               flags={improvements.FLAG_VOCABULARY: 3})
    assert config_digest(left) != config_digest(right)
    assert right["retrieval_flags"] == {improvements.FLAG_VOCABULARY: 3}


# ---------- conditions ----------

def test_conditions_are_flags_and_a_graph_and_nothing_else():
    by_name = {condition.name: condition for condition in sweeps.conditions()}
    assert by_name[sweeps.BASE_CONDITION].flags == {}
    assert by_name[sweeps.BASE_CONDITION].graph == "production"
    assert by_name[sweeps.G3A_CONDITION].flags == {improvements.FLAG_VOCABULARY: 3}
    assert by_name[sweeps.G3A_CONDITION].graph == "study"
    assert by_name[sweeps.G6_CONDITION].flags == {improvements.FLAG_STRENGTH: True}
    # G6 is scoring-only, so it must not need a graph of its own.
    assert by_name[sweeps.G6_CONDITION].graph == "production"


def test_a_condition_naming_a_non_flag_is_refused():
    with settings.overridden({
        "eval.sweeps.conditions": [
            {"name": "bogus", "label": "x", "graph": "production",
             "flags": {"retrieval.rerank_top_k": 8}}
        ]
    }):
        with pytest.raises(KeyError, match="non-flag"):
            sweeps.conditions()


def test_default_flags_are_untouched_by_this_study():
    """Flags flip inside study namespaces only; the freeze order decides defaults."""
    assert improvements.vocabulary_min_document_frequency() == 0
    assert improvements.specialization_strength_enabled() is False
    assert settings["improvements.vocabulary.min_document_frequency"] == 0
    assert settings["improvements.specialization_strength.enabled"] is False


# ---------- the G6 control ----------

def test_mean_strength_scale_is_measured_from_the_levers_own_output():
    base = [_case("1", {"p1"}, [_role("r", {"p1": _parts(1.0), "p2": _parts(0.5)})])]
    variant = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.5), "p2": _parts(0.4)})])]
    # ratios: 0.5/1.0 = 0.5 and 0.4/0.5 = 0.8
    assert sweeps.strength_scales(base, variant) == pytest.approx([0.5, 0.8])
    assert sweeps.mean_strength_scale(base, variant) == pytest.approx(0.65)


def test_mean_strength_scale_ignores_candidates_with_no_specialization_match():
    base = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.0), "p2": _parts(0.5)})])]
    variant = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.0), "p2": _parts(0.25)})])]
    assert sweeps.strength_scales(base, variant) == pytest.approx([0.5])


def test_constant_scale_touches_specialization_match_and_nothing_else():
    base = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.8, skill=0.4, recency=0.9)})])]
    scaled = sweeps.constant_scale(base, 0.5)
    before = base[0].roles[0].parts["p1"]
    after = scaled[0].roles[0].parts["p1"]
    assert after["specialization_match"] == pytest.approx(0.4)
    for component in ("skill_overlap", "recency", "evidence_strength"):
        assert after[component] == before[component]
    # and the original is not mutated
    assert before["specialization_match"] == pytest.approx(0.8)


def test_constant_scale_of_one_changes_no_ordering():
    base = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.9), "p2": _parts(0.2)})])]
    assert sweeps.per_case_metrics(sweeps.constant_scale(base, 1.0), weights_=WEIGHTS) == (
        sweeps.per_case_metrics(base, weights_=WEIGHTS)
    )


# ---------- metrics: the two window measures are different questions ----------

def test_window_hit_rate_and_window_recall_are_not_the_same_number():
    role = _role("r", {
        "p1": _parts(1.0, recency=0.9),        # ranks first
        "p2": _parts(0.1, recency=0.1),        # ranks last
    })
    case = _case("1", {"p1", "p2"}, [role])
    metrics = sweeps.per_case_metrics([case], weights_=WEIGHTS, top_k=1)["1"]
    assert metrics["window_hit"] == 1.0            # someone from truth reached the window
    assert metrics["window_recall"] == pytest.approx(0.5)   # but only half of truth did
    assert metrics["candidate_recall"] == 1.0      # both were in the pool


def test_candidate_recall_reads_the_pool_not_the_ranking():
    case = _case("1", {"p1", "absent"}, [_role("r", {"p1": _parts(1.0)})])
    metrics = sweeps.per_case_metrics([case], weights_=WEIGHTS)["1"]
    assert metrics["candidate_recall"] == pytest.approx(0.5)
    assert metrics["hit_at_1"] == 1.0


# ---------- pool and window diffs: the lever's effect, reported ----------

def test_pool_diff_reports_who_the_lever_added_and_removed():
    before = [_case("1", {"p1"}, [_role("r", {"p1": _parts(1.0), "p2": _parts(0.5)})])]
    after = [_case("1", {"p1"}, [_role("r", {"p2": _parts(0.5), "p3": _parts(0.4)})])]
    row = sweeps.pool_diff_rows(before, after)[0]
    assert row["gained"] == ["p3"]
    assert row["lost"] == ["p1"]
    assert row["truth_lost"] == ["p1"]
    assert row["identical"] is False


def test_pool_diff_marks_an_unmoved_pool_identical():
    before = [_case("1", {"p1"}, [_role("r", {"p1": _parts(1.0), "p2": _parts(0.5)})])]
    after = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.4), "p2": _parts(0.9)})])]
    row = sweeps.pool_diff_rows(before, after)[0]
    assert row["identical"] is True and row["gained"] == [] and row["lost"] == []


def test_window_diff_ignores_order_and_reports_population():
    """Reordering the window is not the same as changing who is shown the model."""
    before = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.9), "p2": _parts(0.5),
                                              "p3": _parts(0.1)})])]
    reordered = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.5), "p2": _parts(0.9),
                                                 "p3": _parts(0.1)})])]
    promoted = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.9), "p2": _parts(0.1),
                                                "p3": _parts(0.5)})])]
    with settings.overridden({"retrieval.rerank_top_k": 2}):
        assert sweeps.window_diff_rows(before, reordered)[0]["identical"] is True
        moved = sweeps.window_diff_rows(before, promoted)[0]
        assert moved["identical"] is False
        assert moved["entered"] == ["p3"] and moved["left"] == ["p2"]


# ---------- gates ----------

def test_gate_reasons_are_generated_from_the_numbers(monkeypatch):
    """A gate is code, so the report cannot claim one the measurements do not support."""
    base = [_case(str(i), {"p1"}, [_role("r", {"p1": _parts(0.5), "p2": _parts(0.4)})])
            for i in range(4)]
    monkeypatch.setattr(sweeps, "load_condition", lambda name: base)
    monkeypatch.setattr(sweeps, "measured_floor", lambda metric="hit_at_1": 0.05)
    gate = sweeps.gate_g6()
    assert gate.passed is False
    assert gate.detail["cases_with_changed_window"] == 0
    assert any("nobody new reaches the window" in reason for reason in gate.reasons)


def test_g3a_gate_stops_on_a_recall_regression(monkeypatch):
    base = [_case("1", {"p1"}, [_role("r", {"p1": _parts(0.9), "p2": _parts(0.4)})])]
    worse = [_case("1", {"p1"}, [_role("r", {"p2": _parts(0.4)})])]
    monkeypatch.setattr(
        sweeps, "load_condition",
        lambda name: base if name == sweeps.BASE_CONDITION else worse,
    )
    monkeypatch.setattr(sweeps, "measured_floor", lambda metric="hit_at_1": 0.05)
    gate = sweeps.gate_g3a()
    assert gate.passed is False
    assert gate.detail["candidate_recall_delta"] < 0
    assert gate.detail["truth_lost_from_pool"] == 1
    assert any("recall guard FAILS" in reason for reason in gate.reasons)


def test_measured_floor_is_none_before_it_is_measured(monkeypatch):
    monkeypatch.setattr(sweeps, "noise_floor_measurement", dict)
    assert sweeps.measured_floor() is None


# ---------- guards ----------

def test_the_test_split_is_unreachable():
    with settings.overridden({"eval.sweeps.split": "test"}):
        with pytest.raises(ValueError, match="validation split only"):
            sweeps._require_validation()


def test_the_ceiling_binds_across_both_stages(monkeypatch):
    monkeypatch.setattr(sweeps, "study_spend", lambda: 7.9)
    with pytest.raises(sweeps.SweepBudgetError, match="exceeds"):
        sweeps.enforce_budget(60)
    assert sweeps.enforce_budget(1) > 0


def test_both_stage_names_share_one_ceiling():
    assert set(sweeps.stages()) == {"noise_floor", "sweep_val"}
    assert sweeps.ceiling() == 8.0


def test_compare_counts_names_every_mismatch():
    expected = {"Person": 316, "Skill": 10630}
    assert sweeps.compare_counts({"Person": 316, "Skill": 10630}, expected) == []
    mismatches = sweeps.compare_counts({"Person": 315, "Skill": 10630}, expected)
    assert mismatches == ["Person: expected 316, observed 315"]


# ---------- checkpoint round trip ----------

def test_case_replay_survives_a_json_round_trip():
    case = _case("1", {"p1"}, [_role("r", {"p1": _parts(0.9), "p2": _parts(0.4)})])
    restored = sweeps.CaseReplay.from_json(json.loads(json.dumps(case.to_json())))
    assert restored == case
    assert restored.pool() == ["p1", "p2"]
    assert restored.to_case_scores().truth == case.truth


def test_replay_pool_keeps_the_engines_order_across_roles():
    case = _case("1", {"p1"}, [
        _role("a", {"p2": _parts(0.5), "p1": _parts(0.9)}),
        _role("b", {"p3": _parts(0.5), "p1": _parts(0.9)}),
    ])
    # candidate_person_ids is the engine's own sorted order per role; the union keeps
    # first appearance and never double-counts a person two roles both retrieved.
    assert case.pool() == ["p1", "p2", "p3"]


# ---------- the stage plumbing the study namespace needs ----------

def test_stage3_and_stage4_write_where_they_are_told(tmp_path):
    from capgraph.pipeline import stage3_normalize, stage4_project

    raw = tmp_path / "raw.jsonl"
    rows = [
        {"contribution_id": f"c{i}", "person_id": "MESOS:1", "project_key": "MESOS",
         "period": "2018-Q1", "contribution_summary": "work",
         "specializations": [{"name": "Cluster orchestration", "strength": "primary"}],
         "skills": [{"name": "Docker"}], "confidence": "high", "reason": "",
         "evidence_ticket_keys": [f"MESOS-{i}"]}
        for i in range(2)
    ]
    raw.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    norm = tmp_path / "study" / "normalized.jsonl"
    terms = tmp_path / "study" / "terms.jsonl"
    caps = tmp_path / "study" / "capabilities.jsonl"
    summary = stage3_normalize.run(
        raw_path=raw, norm_path=norm, terms_path=terms,
        report_path=tmp_path / "study" / "terms_report.md",
    )
    assert summary["contributions"] == 2
    assert summary["min_document_frequency"] == 0
    assert norm.exists() and terms.exists()

    projected = stage4_project.run(norm_path=norm, caps_path=caps)
    assert caps.exists()
    assert {cap.term for cap in projected} == {"Cluster orchestration", "Docker"}
    # The production artifacts were not touched.
    assert not (tmp_path / "contributions").exists()


def test_stage5_load_accepts_study_paths(monkeypatch, tmp_path):
    """The study graph is built by the production loader, not a copy of it."""
    from capgraph.pipeline import stage5_graph

    seen: dict[str, object] = {}

    def recorder(key, value):
        def fake(path=None, *args, **kwargs):
            seen[key] = path
            return value

        return fake

    monkeypatch.setattr(stage5_graph, "read_buckets", recorder("buckets", []))
    monkeypatch.setattr(stage5_graph, "read_contributions", recorder("contributions", ([], 0)))
    monkeypatch.setattr(stage5_graph, "read_terms", recorder("terms", []))
    monkeypatch.setattr(stage5_graph, "read_capabilities", recorder("caps", []))

    def fake_embed(contribs, path=None, force=False):
        seen["embeddings"] = path
        return {}, False

    monkeypatch.setattr(stage5_graph, "embed_contributions", fake_embed)
    monkeypatch.setattr(stage5_graph, "run_batches", lambda driver, statement, rows: len(rows))
    monkeypatch.setattr(stage5_graph, "graph_counts", lambda driver: {})
    monkeypatch.setattr(stage5_graph, "format_counts", lambda counts: "")

    stage5_graph.load(
        object(),
        contributions_path=tmp_path / "normalized.jsonl",
        terms_path=tmp_path / "terms.jsonl",
        capabilities_path=tmp_path / "capabilities.jsonl",
    )
    assert seen["contributions"] == tmp_path / "normalized.jsonl"
    assert seen["terms"] == tmp_path / "terms.jsonl"
    assert seen["caps"] == tmp_path / "capabilities.jsonl"
    # Buckets and the embedding cache stay on the production artifacts: Stage 3 rewrites
    # term names and never contribution summaries, so the vectors are the same vectors.
    assert seen["buckets"] is None
    assert seen["embeddings"] == stage5_graph.EMBEDDINGS_PATH


# ---------- the report's one hand-written section is guarded ----------

def test_the_recommendation_shouts_when_its_gate_flips(monkeypatch):
    """Hand-written prose over re-run numbers is the one drift this report can suffer."""
    from capgraph.eval import recommendations_sweeps as recs

    stopped = sweeps.Gate(lever="G3a", passed=False, reasons=[], detail={})
    assert recs._guard(stopped) == []

    passed = sweeps.Gate(lever="G3a", passed=True, reasons=[], detail={})
    banner = recs._guard(passed)
    assert any("measurements moved" in line for line in banner)


def test_every_recommendation_declares_the_outcome_it_was_written_for():
    from capgraph.eval import recommendations_sweeps as recs

    assert set(recs.WRITTEN_FOR) == {"G3a", "G6"}
    assert all(value is False for value in recs.WRITTEN_FOR.values())


def test_an_unknown_lever_is_not_silently_waved_through():
    from capgraph.eval import recommendations_sweeps as recs

    assert recs._guard(sweeps.Gate(lever="G99", passed=True, reasons=[], detail={})) == []
    assert recs.summary_table([sweeps.Gate(lever="G99", passed=True, reasons=[], detail={})])[-1] \
        .endswith("| — |")


def test_a_recorded_claim_is_graded_against_the_floor_in_three_bands():
    """"Inside the floor" and "twice the floor" are different verdicts; keep them apart."""
    from capgraph.eval.report_sweeps import _against_floor

    assert "inside the floor" in _against_floor(0.02, 0.05)
    assert "marginal" in _against_floor(-0.07, 0.05)
    assert "survives" in _against_floor(0.25, 0.05)
