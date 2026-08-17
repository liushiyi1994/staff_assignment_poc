"""The final weights round: the selection rule, the window arithmetic, and the gate.

Everything here is offline — no model call, no Neo4j, no manifest — because the round
itself is. What is under test is the discipline the conclusion rests on, which no number
in the report can demonstrate on its own:

* **selection is mechanism-based.** The adopted vector is one step in the direction the
  marginals point, and the grid's best row is never adopted — a synthetic checkpoint
  whose best row is extreme proves the rule ignores it;
* **the window arithmetic is the gate.** Membership is compared as a *set*, truth moves
  are counted separately from population moves, and a scan answers the propagation
  question for a whole grid rather than for one point;
* **the gate is code.** It stops when nothing can propagate even though the
  deterministic arm improved, and it opens when a truth person can actually reach the
  re-rank — so the report cannot claim a verdict the numbers do not support;
* **the round cannot be pointed at the v4 test split, and its ledger is checkable.**
"""
from __future__ import annotations

import pytest

from capgraph.eval import sweeps
from capgraph.eval import weights_round as wr
from capgraph.settings import settings

CURRENT = {
    "specialization_match": 0.25,
    "skill_overlap": 0.30,
    "recency": 0.40,
    "evidence_strength": 0.05,
}


def _parts(spec=0.5, skill=0.5, recency=0.5, evidence=0.5):
    return {
        "specialization_match": spec,
        "skill_overlap": skill,
        "recency": recency,
        "evidence_strength": evidence,
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


# ---------- the grid ----------

def test_grid_normalizes_and_dedupes_scale_equivalent_vectors():
    """Two vectors that differ only by scale rank identically, so they are one point."""
    grid = wr.grid_of({
        "specialization_match": [1.0, 2.0],
        "skill_overlap": [1.0, 2.0],
        "recency": [1.0, 2.0],
        "evidence_strength": [1.0, 2.0],
    })
    assert {"specialization_match": 0.25, "skill_overlap": 0.25, "recency": 0.25,
            "evidence_strength": 0.25} in grid
    assert all(abs(sum(vector.values()) - 1.0) < 1e-6 for vector in grid)
    assert len(grid) == len({tuple(sorted(vector.items())) for vector in grid})


def test_grid_refuses_vectors_the_engine_could_not_use():
    """A role with no specializations and no skills is scored on the other two alone."""
    grid = wr.grid_of({
        "specialization_match": [1.0],
        "skill_overlap": [1.0],
        "recency": [0.0],
        "evidence_strength": [0.0],
    })
    assert grid == []


# ---------- selection: mechanism, not leaderboard ----------

def test_one_step_moves_are_single_steps_from_the_current_weighting():
    directions = {
        "specialization_match": {"direction": "down", "span": -0.04},
        "skill_overlap": {"direction": "up", "span": +0.01},
        "recency": {"direction": "up", "span": +0.08},
        "evidence_strength": {"direction": "down", "span": -0.06},
    }
    moves = wr.one_step_moves(directions, CURRENT)
    assert {move["moved_from"] for move in moves} == {
        "specialization_match", "evidence_strength"
    }
    # Every move goes to the strongest upward marginal, and moves exactly one step.
    assert {move["moved_to"] for move in moves} == {"recency"}
    for move in moves:
        assert abs(sum(move["weights"].values()) - sum(CURRENT.values())) < 1e-9
        moved = CURRENT[move["moved_from"]] - move["weights"][move["moved_from"]]
        assert moved == pytest.approx(wr.step())


def test_a_component_with_no_weight_to_give_is_not_moved():
    directions = {
        "specialization_match": {"direction": "down", "span": -0.04},
        "skill_overlap": {"direction": "flat", "span": 0.0},
        "recency": {"direction": "up", "span": +0.08},
        "evidence_strength": {"direction": "down", "span": -0.06},
    }
    current = {**CURRENT, "evidence_strength": 0.0}
    moves = wr.one_step_moves(directions, current)
    assert [move["moved_from"] for move in moves] == ["specialization_match"]


def test_selection_ignores_the_grids_best_row(monkeypatch):
    """The adopted vector is one step from current, however good an extreme row looks."""
    cases = [
        _case("1", {"p1"}, [_role("r", {
            "p1": _parts(spec=0.0, skill=0.9, recency=0.9, evidence=0.0),
            "p2": _parts(spec=1.0, skill=0.1, recency=0.1, evidence=1.0),
        })]),
        _case("2", {"q1"}, [_role("r", {
            "q1": _parts(spec=0.0, skill=0.8, recency=1.0, evidence=0.0),
            "q2": _parts(spec=1.0, skill=0.2, recency=0.0, evidence=1.0),
        })]),
    ]
    monkeypatch.setattr(wr, "current_weights", lambda: dict(CURRENT))
    chosen = wr.select_candidate(cases, grid=wr.grid_of({
        "specialization_match": [0.0, 1.0, 2.0],
        "skill_overlap": [0.0, 1.0, 2.0],
        "recency": [0.0, 1.0, 2.0],
        "evidence_strength": [0.0, 1.0, 2.0],
    }))
    weights = chosen["weights"]
    # Exactly one step moved, from one component into one other, and nothing zeroed
    # that the current weighting funded.
    differing = {name for name in CURRENT if weights[name] != CURRENT[name]}
    assert differing == {chosen["moved_from"], chosen["moved_to"]}
    assert weights[chosen["moved_from"]] == pytest.approx(
        CURRENT[chosen["moved_from"]] - chosen["step"]
    )
    assert weights != {"specialization_match": 0.0, "skill_overlap": 0.0,
                       "recency": 1.0, "evidence_strength": 0.0}


def test_direction_counts_steps_against_the_span():
    rows = [
        {"component": "recency", "weight": 0.0, "hit_at_1": 0.10},
        {"component": "recency", "weight": 0.2, "hit_at_1": 0.30},
        {"component": "recency", "weight": 0.4, "hit_at_1": 0.20},
        {"component": "recency", "weight": 0.6, "hit_at_1": 0.40},
    ]
    entry = wr.direction(rows, "recency", "hit_at_1")
    assert entry["direction"] == "up"
    assert entry["span"] == pytest.approx(0.30)
    assert entry["agreeing_steps"] == 2
    assert entry["contradicting_steps"] == 1


# ---------- the window ----------

def test_membership_counts_population_and_truth_moves_apart(monkeypatch):
    """A window that churns non-truth candidates is not a window that gained truth."""
    monkeypatch.setattr(wr, "window_width", lambda: 1)
    # Under the current weighting `a` leads on recency; under the candidate, `b` leads
    # on specialization. Neither is the truth person, who is never in a 1-card window.
    cases = [_case("1", {"c"}, [_role("r", {
        "a": _parts(spec=0.0, skill=0.0, recency=1.0, evidence=0.0),
        "b": _parts(spec=1.0, skill=0.0, recency=0.0, evidence=0.0),
        "c": _parts(spec=0.0, skill=0.0, recency=0.0, evidence=0.0),
    })])]
    rows = wr.membership_rows(cases, {"specialization_match": 0.0, "skill_overlap": 0.0,
                                      "recency": 1.0, "evidence_strength": 0.0},
                              {"specialization_match": 1.0, "skill_overlap": 0.0,
                               "recency": 0.0, "evidence_strength": 0.0})
    totals = wr.membership_totals(rows)
    assert totals["cases_changed"] == 1
    assert totals["entered"] == 1 and totals["left"] == 1
    assert totals["truth_entered"] == 0 and totals["truth_left"] == 0


def test_truth_totals_and_outside_window_agree(monkeypatch):
    monkeypatch.setattr(wr, "window_width", lambda: 1)
    weights = {"specialization_match": 0.0, "skill_overlap": 0.0, "recency": 1.0,
               "evidence_strength": 0.0}
    cases = [
        _case("1", {"a"}, [_role("r", {
            "a": _parts(recency=1.0), "b": _parts(recency=0.0),
        })]),
        _case("2", {"z"}, [_role("r", {
            "y": _parts(recency=1.0), "z": _parts(recency=0.0),
        })]),
    ]
    totals = wr.truth_totals(cases, weights)
    assert totals == {"truth_people": 2, "in_window": 1, "outside_window": 1,
                      "cases_with_truth_outside": 1}
    outside = wr.truth_outside_window(cases, weights)
    assert [row["outside"] for row in outside] == [["z"]]


def test_scan_reports_the_best_any_vector_could_do(monkeypatch):
    """The scan's job is an upper bound over a grid, not a score for one vector."""
    monkeypatch.setattr(wr, "window_width", lambda: 1)
    # The truth person is reachable only by a weighting that funds evidence_strength.
    cases = [_case("1", {"t"}, [_role("r", {
        "t": _parts(spec=0.0, skill=0.0, recency=0.0, evidence=1.0),
        "u": _parts(spec=1.0, skill=1.0, recency=1.0, evidence=0.0),
    })])]
    current = {"specialization_match": 0.5, "skill_overlap": 0.5, "recency": 0.0,
               "evidence_strength": 0.0}
    grid = wr.grid_of({
        "specialization_match": [0.0, 1.0],
        "skill_overlap": [0.0, 1.0],
        "recency": [0.0, 1.0],
        "evidence_strength": [0.0, 1.0],
    })
    result = wr.scan(cases, grid, against=current, choices={})
    assert result["max_truth_entering"] == 1
    assert result["best_window_recall"] == pytest.approx(1.0)
    assert result["baseline_window_recall"] == pytest.approx(0.0)


def test_scan_counts_the_reranks_own_choices(monkeypatch):
    """Removing the person the model chose is the other way a retune could propagate."""
    monkeypatch.setattr(wr, "window_width", lambda: 1)
    cases = [_case("1", {"t"}, [_role("r", {
        "t": _parts(spec=0.0, skill=0.0, recency=0.0, evidence=1.0),
        "u": _parts(spec=1.0, skill=1.0, recency=1.0, evidence=0.0),
    })])]
    current = {"specialization_match": 1.0, "skill_overlap": 0.0, "recency": 0.0,
               "evidence_strength": 0.0}
    grid = wr.grid_of({
        "specialization_match": [0.0, 1.0],
        "skill_overlap": [0.0, 0.0],
        "recency": [0.0, 0.0],
        "evidence_strength": [0.0, 1.0],
    })
    # The arm wrongly ranked `u` first; a weighting that funds evidence_strength drops
    # `u` out of the 1-card window, which is a real propagation channel.
    result = wr.scan(cases, grid, against=current, choices={"1": {"first": "u"}})
    assert result["max_wrong_choices_removed"] == 1
    assert result["max_right_choices_removed"] == 0


def test_order_change_measures_displacement_not_membership(monkeypatch):
    monkeypatch.setattr(wr, "window_width", lambda: 3)
    cases = [_case("1", {"a"}, [_role("r", {
        "a": _parts(spec=1.0, skill=0.0, recency=0.0, evidence=0.0),
        "b": _parts(spec=0.0, skill=0.0, recency=1.0, evidence=0.0),
        "c": _parts(spec=0.5, skill=0.0, recency=0.5, evidence=0.0),
    })])]
    spec_first = {"specialization_match": 1.0, "skill_overlap": 0.0, "recency": 0.0,
                  "evidence_strength": 0.0}
    recency_first = {"specialization_match": 0.0, "skill_overlap": 0.0, "recency": 1.0,
                     "evidence_strength": 0.0}
    change = wr.order_change(cases, spec_first, recency_first)
    assert change["roles"] == 1
    assert change["roles_whose_first_card_changed"] == 1
    assert change["mean_card_displacement"] > 0
    # The same weighting on both sides cannot move anything.
    same = wr.order_change(cases, spec_first, spec_first)
    assert same["roles_whose_first_card_changed"] == 0
    assert same["mean_card_displacement"] == 0.0


# ---------- the gate ----------

def _tier0(**overrides):
    tier0 = {
        "target_metric": "hit_at_1",
        "current_metrics": {"hit_at_1": 0.1429},
        "candidate_metrics": {"hit_at_1": 0.2143},
        "floors": {"hit_at_1": 0.0357},
        "plateau": [{"metric": "hit_at_1", "points": 81, "min": 0.1429, "median": 0.1786,
                     "max": 0.25, "current": 0.1429, "beats": 70, "ties": 11, "worse": 0}],
        "membership_totals": {"cases": 28, "cases_changed": 10, "entered": 11, "left": 11,
                              "truth_entered": 0, "truth_left": 0},
        "neighbourhood_scan": {"vectors": 270, "vectors_with_truth_entering": 0,
                               "max_truth_entering": 0, "max_wrong_choices_removed": 0,
                               "max_right_choices_removed": 0, "best_window_recall": 0.9685,
                               "baseline_window_recall": 0.9685},
        "simplex_scan": {"vectors": 13776, "vectors_with_truth_entering": 10355,
                         "max_truth_entering": 1, "max_wrong_choices_removed": 0,
                         "max_right_choices_removed": 0, "best_window_recall": 0.9774,
                         "baseline_window_recall": 0.9685},
    }
    tier0.update(overrides)
    return tier0


def test_gate_stops_when_nothing_can_propagate():
    """A deterministic gain the re-rank cannot be shown does not buy a paid arm."""
    gate = wr.gate_one(_tier0())
    assert gate.passed is False
    assert gate.detail["improves"] is True
    assert gate.detail["plateau_holds"] is True
    assert gate.detail["propagation_possible"] is False


def test_gate_opens_when_a_truth_person_can_reach_the_rerank():
    gate = wr.gate_one(_tier0(
        membership_totals={"cases": 28, "cases_changed": 12, "entered": 14, "left": 13,
                           "truth_entered": 2, "truth_left": 0},
    ))
    assert gate.passed is True
    assert gate.detail["propagation_possible"] is True


def test_gate_opens_when_the_retune_removes_a_wrong_first_choice():
    gate = wr.gate_one(_tier0(
        neighbourhood_scan={"vectors": 270, "vectors_with_truth_entering": 0,
                            "max_truth_entering": 0, "max_wrong_choices_removed": 3,
                            "max_right_choices_removed": 0, "best_window_recall": 0.9685,
                            "baseline_window_recall": 0.9685},
    ))
    assert gate.passed is True


def test_gate_stops_when_the_improvement_is_inside_the_floor():
    gate = wr.gate_one(_tier0(
        candidate_metrics={"hit_at_1": 0.1786},
        membership_totals={"cases": 28, "cases_changed": 12, "entered": 14, "left": 13,
                           "truth_entered": 2, "truth_left": 0},
    ))
    assert gate.passed is False
    assert gate.detail["improves"] is False


def test_gate_stops_when_the_plateau_does_not_hold():
    gate = wr.gate_one(_tier0(
        plateau=[{"metric": "hit_at_1", "points": 81, "min": 0.10, "median": 0.14,
                  "max": 0.25, "current": 0.1429, "beats": 20, "ties": 5, "worse": 56}],
        membership_totals={"cases": 28, "cases_changed": 12, "entered": 14, "left": 13,
                           "truth_entered": 2, "truth_left": 0},
    ))
    assert gate.passed is False
    assert gate.detail["plateau_holds"] is False


# ---------- guards ----------

def test_the_round_refuses_a_split_that_is_not_validation():
    with settings.overridden({f"eval.{wr.STUDY}.split": "test"}):
        with pytest.raises(wr.WeightsRoundError, match="validation"):
            wr._require_validation()


def test_the_round_refuses_a_levers_checkpoint_as_its_substrate():
    """Re-scoring a lever's condition would measure the lever and the weights together."""
    with settings.overridden({f"eval.{wr.STUDY}.source_condition": "g6_strength"}):
        with pytest.raises(wr.WeightsRoundError, match="flags"):
            wr.source_cases()


def test_both_paid_stage_names_are_configured_and_share_one_ceiling():
    assert wr.stages() == ["weights_test", "weights_val"]
    assert wr.ceiling() == pytest.approx(10.0)


def test_checkpoints_stay_out_of_the_frozen_namespaces():
    root = wr.root()
    assert root.name == "weights"
    for frozen in ("v1", "v2", "v3", "v4", "rerank_redesign", "sweeps"):
        assert frozen != root.name


def test_report_guard_fires_when_the_gate_flips():
    from capgraph.eval import report_weights_round as report

    passing = {"gate_1": {"name": "gate 1 → weights_val", "passed": True}}
    stopping = {"gate_1": {"name": "gate 1 → weights_val", "passed": False}}
    assert report._guard(stopping) == []
    assert any("measurements moved" in line for line in report._guard(passing))
