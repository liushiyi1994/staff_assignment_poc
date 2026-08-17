"""Benchmark-v2 machinery: rank combinators, the score-component sweep, and namespaces.

Offline throughout. What is pinned here is everything a v2 number depends on that is
not already covered by the Stage 7 suite: the fusion and backstop arithmetic that
produced the lever table, the re-scoring path the weight sweep uses (which must be the
*same* arithmetic the engine scores with, not a second copy), and the two separations
that keep v1 immutable — a distinct checkpoint namespace and a tracked report whose
halves do not overwrite each other.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from capgraph.eval import run_eval, run_v2, scores
from capgraph.eval.contracts import RankingOutput
from capgraph.eval.fusion import dedupe, reciprocal_rank_fusion, roster_backstop
from capgraph.eval.holdout import BenchmarkManifestEntry
from capgraph.eval.scores import CaseScores, RoleScores, coarse_grid, evaluate_weights, normalized
from capgraph.models import CandidateProfile, PersonCapability, RoleSpec
from capgraph.query.rank import SCORE_COMPONENTS, combine_parts, score_candidate
from capgraph.settings import settings

ROSTER = ("ALPHA:1", "ALPHA:2", "ALPHA:3", "ALPHA:4")


def _case(issue_id: str = "1", *, truth=("ALPHA:1",), split: str = "validation"):
    return BenchmarkManifestEntry(
        seed=1,
        issue_id=issue_id,
        issue_key=f"ALPHA-{issue_id}",
        query_text="Need someone who has worked on Kafka streaming consumer lag issues.",
        as_of_time=datetime(2019, 6, 1),
        project_key="ALPHA",
        eligible_roster=list(ROSTER),
        truth_person_ids=list(truth),
        split=split,
    )


# ---------- reciprocal rank fusion ----------

def test_rrf_scores_are_the_sum_of_reciprocal_ranks():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=1)
    # Both appear at rank 1 and rank 2 in one list each, so the tie breaks on id.
    assert fused == ["a", "b"]

    # "b" is second in both lists, "a" is first in one and absent from the other:
    # 1/(1+1) = 0.5 for "a" against 2 * 1/(1+2) = 0.667 for "b".
    fused = reciprocal_rank_fusion([["a", "b"], ["c", "b"]], k=1)
    assert fused[0] == "b"


def test_rrf_k_trades_one_strong_vote_against_many_weak_ones():
    """The knob the sweep turns: small k keeps rank 1 decisive, large k flattens it.

    ``x`` is first in one ranking and absent from the others. ``y`` is eighth in all
    three. Small k rewards the decisive placement; large k rewards the agreement. This
    is exactly why fusing a strong ranker with a weaker one at a large k costs Hit@1.
    """
    rankings = [
        ["x", *(f"a{i}" for i in range(6)), "y"],
        [*(f"b{i}" for i in range(7)), "y"],
        [*(f"c{i}" for i in range(7)), "y"],
    ]
    def beats(fused, first, second):
        return fused.index(first) < fused.index(second)

    assert beats(reciprocal_rank_fusion(rankings, k=1), "x", "y")
    assert beats(reciprocal_rank_fusion(rankings, k=100), "y", "x")


def test_rrf_absent_from_a_ranking_scores_nothing_from_it():
    """A retrieval union is not a roster permutation; a missing id is not last-placed."""
    fused = reciprocal_rank_fusion([["a"], ["b", "a"]], k=60)
    assert set(fused) == {"a", "b"}
    assert fused[0] == "a"


def test_rrf_weights_let_one_ranking_count_for_more():
    rankings = [["a", "b"], ["b", "a"]]
    assert reciprocal_rank_fusion(rankings, k=60, weights=[3.0, 1.0])[0] == "a"
    assert reciprocal_rank_fusion(rankings, k=60, weights=[1.0, 3.0])[0] == "b"


def test_rrf_deduplicates_within_a_ranking_before_scoring():
    assert reciprocal_rank_fusion([["a", "a", "b"]], k=1) == reciprocal_rank_fusion(
        [["a", "b"]], k=1
    )


@pytest.mark.parametrize("kwargs", [{"k": 0}, {"k": -1}])
def test_rrf_refuses_non_positive_k(kwargs):
    with pytest.raises(ValueError, match="positive"):
        reciprocal_rank_fusion([["a"]], **kwargs)


def test_rrf_refuses_mismatched_or_negative_weights():
    with pytest.raises(ValueError, match="one entry per ranking"):
        reciprocal_rank_fusion([["a"], ["b"]], k=60, weights=[1.0])
    with pytest.raises(ValueError, match="negative"):
        reciprocal_rank_fusion([["a"]], k=60, weights=[-1.0])


# ---------- roster backstop ----------

def test_backstop_appends_the_unretrieved_roster_below_the_ranking():
    backstopped = roster_backstop(["ALPHA:3"], ROSTER)
    assert backstopped[0] == "ALPHA:3"
    assert set(backstopped) == set(ROSTER)


def test_backstop_cannot_change_the_head_it_was_given():
    """The mechanism that makes it safe: nothing already ranked moves."""
    head = ["ALPHA:4", "ALPHA:2"]
    assert roster_backstop(head, ROSTER)[: len(head)] == head


def test_backstop_orders_the_tail_by_the_supplied_ranking():
    backstopped = roster_backstop(
        ["ALPHA:1"], ROSTER, tail_order=["ALPHA:4", "ALPHA:9", "ALPHA:2"]
    )
    assert backstopped == ["ALPHA:1", "ALPHA:4", "ALPHA:2", "ALPHA:3"]


def test_backstop_refuses_a_ranking_that_leaves_the_roster():
    with pytest.raises(ValueError, match="leaves the roster"):
        roster_backstop(["BETA:9"], ROSTER)


def test_dedupe_keeps_first_occurrence():
    assert dedupe(["b", "a", "b"]) == ["b", "a"]


# ---------- score components and the weight sweep ----------

def _capability(term: str, *, decay: float = 0.5) -> PersonCapability:
    return PersonCapability(
        person_id="ALPHA:1",
        term=term,
        kind="skill",
        evidence_count=3,
        contribution_ids=[],
        last_used="2018-06-01",
        decay_score=decay,
    )


def test_combine_parts_is_the_arithmetic_score_candidate_uses():
    """One implementation, so a sweep cannot drift from what the engine scores."""
    candidate = CandidateProfile(
        person_id="ALPHA:1", person_name="Person ALPHA-1", skills=[_capability("kafka")]
    )
    role = RoleSpec(role="streaming engineer", skills=["kafka"])
    score_candidate(candidate, role)
    assert candidate.score == combine_parts(candidate.score_parts)


def test_combine_parts_renormalizes_over_the_components_that_applied():
    """A role that asks for no specialization must not be scored as if it failed one."""
    weights = {"specialization_match": 0.4, "skill_overlap": 0.25, "recency": 0.2,
               "evidence_strength": 0.15}
    both = combine_parts({"skill_overlap": 1.0, "recency": 0.0}, weights)
    assert both == pytest.approx(0.25 / 0.45, abs=1e-4)


def test_combine_parts_refuses_an_unweighted_component():
    with pytest.raises(ValueError, match="no weight configured"):
        combine_parts({"made_up": 1.0}, {"recency": 1.0})


def _case_scores(issue_id: str = "1", *, truth=("ALPHA:1",)) -> CaseScores:
    return CaseScores(
        issue_id=issue_id,
        issue_key=f"ALPHA-{issue_id}",
        project_key="ALPHA",
        truth=frozenset(truth),
        roles=(
            RoleScores(
                role="streaming engineer",
                # recency and evidence_strength are always scored; skill_overlap is
                # present because this role asked for skills. That is the shape
                # score_candidate always produces.
                parts={
                    "ALPHA:1": {"skill_overlap": 1.0, "recency": 0.1,
                                "evidence_strength": 0.5},
                    "ALPHA:2": {"skill_overlap": 0.0, "recency": 1.0,
                                "evidence_strength": 0.5},
                },
            ),
        ),
    )


def test_case_ordering_follows_the_weights_it_is_given():
    case = _case_scores()
    skill_heavy = {"specialization_match": 0.0, "skill_overlap": 1.0, "recency": 0.0,
                   "evidence_strength": 0.0}
    recency_heavy = {"specialization_match": 0.0, "skill_overlap": 0.0, "recency": 1.0,
                     "evidence_strength": 0.0}
    assert case.ordering(skill_heavy)[0] == "ALPHA:1"
    assert case.ordering(recency_heavy)[0] == "ALPHA:2"


def test_window_is_each_role_top_k_deduplicated():
    case = _case_scores()
    weights = {name: 1.0 for name in SCORE_COMPONENTS}
    assert len(case.window(weights, 1)) == 1
    assert set(case.window(weights, 5)) == {"ALPHA:1", "ALPHA:2"}


def test_evaluate_weights_reports_window_recall_separately_from_hit_at_k():
    """The window is the full system's ceiling: truth can be ranked and still miss it."""
    case = _case_scores()
    recency_heavy = {"specialization_match": 0.0, "skill_overlap": 0.0, "recency": 1.0,
                     "evidence_strength": 0.0}
    narrow = evaluate_weights([case], recency_heavy, top_k=1)
    assert narrow.hit_at_5 == 1.0                # truth is ranked, second
    assert narrow.hit_at_1 == 0.0
    assert narrow.window_recall == 0.0           # but never reaches a one-slot window
    assert evaluate_weights([case], recency_heavy, top_k=2).window_recall == 1.0


def test_case_scores_round_trip_through_json():
    case = _case_scores()
    assert CaseScores.from_json(json.loads(json.dumps(case.to_json()))) == case


def test_normalized_weights_sum_to_one_over_every_component():
    weights = normalized([1.0, 1.0, 1.0, 1.0])
    assert set(weights) == set(SCORE_COMPONENTS)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_normalized_refuses_a_wrong_length_or_zero_vector():
    with pytest.raises(ValueError, match="4 values"):
        normalized([1.0])
    with pytest.raises(ValueError, match="sum to zero"):
        normalized([0.0, 0.0, 0.0, 0.0])


def test_coarse_grid_is_deduplicated_and_normalized():
    grid = coarse_grid((0.0, 1.0, 2.0))
    assert all(sum(point.values()) == pytest.approx(1.0, abs=1e-3) for point in grid)
    assert len({tuple(point.values()) for point in grid}) == len(grid)
    # (1,1,1,1) and (2,2,2,2) are the same weighting and must appear once.
    assert grid.count(normalized([1.0, 1.0, 1.0, 1.0])) == 1


def test_load_scores_prefers_the_later_record_for_a_case(tmp_path):
    path = tmp_path / "validation.jsonl"
    first = _case_scores()
    second = _case_scores(truth=("ALPHA:2",))
    path.write_text(
        json.dumps(first.to_json()) + "\n" + json.dumps(second.to_json()) + "\n",
        encoding="utf-8",
    )
    assert scores.load_scores("validation", path=path) == [second]


# ---------- namespace and report separation ----------

def test_v2_records_never_land_in_the_v1_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "RUNS_DIR", tmp_path / "v1")
    v2_dir = tmp_path / "v2"
    run_eval.append_record("validation", {"system": "capgraph_full", "issue_id": "1"},
                           runs_dir=v2_dir)
    assert run_eval.load_checkpoint("validation") == {}
    assert ("capgraph_full", "1") in run_eval.load_checkpoint("validation", runs_dir=v2_dir)


def test_v2_config_carries_the_prompt_identity_and_its_own_stage():
    config = run_v2.v2_config("test")
    assert config["stage"] == settings["eval.v2.test_stage"]
    assert config["rerank_prompt"] == settings["llm.rerank_prompt"]
    assert config["rerank_prompt_digest"]
    assert run_v2.config_digest(config) != run_v2.config_digest(run_v2.run_config())


def test_a_prompt_revision_changes_the_configuration_digest(tmp_path, monkeypatch):
    """A re-rank prompt is part of the ranking configuration, not an invisible input."""
    before = run_v2.v2_config("validation")["rerank_prompt_digest"]
    monkeypatch.setattr(run_v2, "PROMPTS_DIR", tmp_path)
    (tmp_path / f"{settings['llm.rerank_prompt']}.md").write_text("different", encoding="utf-8")
    assert run_v2.v2_config("validation")["rerank_prompt_digest"] != before


def test_write_tracked_section_leaves_the_v1_half_byte_identical(tmp_path):
    path = tmp_path / "eval-results.md"
    v1 = "# Temporal benchmark results\n\nv1 numbers.\n"
    path.write_text(v1, encoding="utf-8")
    run_v2.write_tracked_section("# Benchmark v2\n\nfirst.\n", path=path)
    run_v2.write_tracked_section("# Benchmark v2\n\nsecond.\n", path=path)
    written = path.read_text(encoding="utf-8")
    assert written.startswith(v1.rstrip("\n"))
    assert written.count(run_eval.V2_MARKER) == 1
    assert "first." not in written and "second." in written


def test_the_v1_report_writer_preserves_an_existing_v2_section(tmp_path):
    path = tmp_path / "eval-results.md"
    path.write_text(
        f"old v1\n{run_eval.V2_MARKER}\n# Benchmark v2\n\nkeep me.\n", encoding="utf-8"
    )
    run_eval.write_report(
        {},
        markdown_path=tmp_path / "results.md",
        json_path=tmp_path / "results.json",
        tracked_path=path,
    )
    written = path.read_text(encoding="utf-8")
    assert "keep me." in written
    assert "old v1" not in written                  # the v1 half is regenerated


def test_score_rankings_enforces_the_frozen_roster(tmp_path):
    case = _case("1")
    outside = {"1": RankingOutput(ranked_ids=["BETA:9"])}
    with pytest.raises(ValueError, match="outside frozen roster"):
        run_v2.score_rankings("fused", outside, [case])


def test_score_rankings_scores_only_the_cases_it_was_given():
    cases = [_case("1"), _case("2", truth=("ALPHA:2",))]
    rankings = {"1": RankingOutput(ranked_ids=["ALPHA:1", "ALPHA:2"])}
    result = run_v2.score_rankings("fused", rankings, cases)
    assert result.n_briefs == 1
    assert result.hit_at_1 == 1.0


def test_enforce_v2_budget_refuses_a_projection_over_the_track_ceiling(monkeypatch):
    monkeypatch.setattr(run_v2, "spend_by_stage", lambda stages: [("s", 1, 7.9)])
    with pytest.raises(run_v2.V2BudgetError, match="escalate"):
        run_v2.enforce_v2_budget(120)


def test_enforce_v2_budget_returns_the_projection_when_it_fits(monkeypatch):
    monkeypatch.setattr(run_v2, "spend_by_stage", lambda stages: [("s", 0, 0.0)])
    per_case = float(settings["eval.v2.expected_cost_per_case_usd"])
    assert run_v2.enforce_v2_budget(30) == pytest.approx(30 * per_case)


def test_spend_by_stage_reads_the_cost_log_by_stage_name(tmp_path, monkeypatch):
    log = tmp_path / "llm_costs.jsonl"
    log.write_text(
        "\n".join(
            json.dumps({"stage": stage, "cost_usd": cost})
            for stage, cost in (("stage7b_val", 0.5), ("stage7_eval", 9.0),
                                ("stage7b_val", 0.25))
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("capgraph.llm.cost_log_path", lambda: log)
    assert run_v2.spend_by_stage(["stage7b_val", "stage7b_test"]) == [
        ("stage7b_val", 2, 0.75),
        ("stage7b_test", 0, 0.0),
    ]


def test_each_ab_arm_gets_its_own_namespace_inside_the_v2_one():
    assert run_v2.runs_dir("ab_weights_only") == run_v2.runs_dir() / "ab_weights_only"
    assert run_v2.runs_dir().name == "runs"


def test_the_report_reads_validation_from_the_frozen_arm_and_test_from_the_run():
    assert run_v2.split_dir("validation") == run_v2.frozen_validation_dir()
    assert run_v2.split_dir("test") == run_v2.runs_dir()


def test_configured_weights_cover_every_score_component_and_sum_to_one():
    """A weight vector that silently dropped a component would renormalize around it."""
    configured = settings["scoring.weights"]
    assert set(configured) == set(SCORE_COMPONENTS)
    assert sum(configured.values()) == pytest.approx(1.0)


def test_the_configured_rerank_prompt_exists_and_keeps_the_prompt_contract():
    from capgraph.settings import PROMPTS_DIR

    text = (PROMPTS_DIR / f"{settings['llm.rerank_prompt']}.md").read_text(encoding="utf-8")
    for placeholder in ("{{brief}}", "{{role_json}}", "{{candidates_json}}"):
        assert placeholder in text
    # The citation rules are what the evidence validator depends on; a prompt revision
    # must not quietly drop them.
    assert "evidence_ticket_keys" in text and "person_id" in text


def test_marginal_effects_average_over_the_grid_not_its_argmax():
    """The decision input: one row per (component, weight), not a single best vector."""
    cases = [_case_scores("1"), _case_scores("2", truth=("ALPHA:2",))]
    grid = [
        {"specialization_match": 0.0, "skill_overlap": 1.0, "recency": 0.0,
         "evidence_strength": 0.0},
        {"specialization_match": 0.0, "skill_overlap": 0.0, "recency": 1.0,
         "evidence_strength": 0.0},
    ]
    effects = scores.marginal_effects(cases, top_k=5, grid=grid)
    assert set(effects) == set(SCORE_COMPONENTS)
    # Only levels backed by at least five grid points are reported, so a two-point
    # grid yields nothing rather than a mean of one.
    assert all(rows == [] for rows in effects.values())


def test_marginal_effects_report_levels_backed_by_enough_grid_points():
    cases = [_case_scores()]
    effects = scores.marginal_effects(cases, top_k=15)
    assert effects["recency"], "the real grid must populate every component"
    weights = [weight for weight, _, _ in effects["recency"]]
    assert weights == sorted(weights)


def test_the_weight_lever_section_is_omitted_without_a_component_checkpoint(monkeypatch):
    def missing(split):
        raise SystemExit("no score checkpoint")

    monkeypatch.setattr(scores, "load_scores", missing)
    assert run_v2.weight_lever_section() == []


def test_the_grid_excludes_weightings_no_role_could_be_ranked_under():
    """recency and evidence_strength are the only components always computed, so a
    vector that gives both nothing cannot rank a role that asked for neither kind."""
    from capgraph.query.rank import ALWAYS_SCORED_COMPONENTS

    for point in coarse_grid():
        assert any(point[name] > 0 for name in ALWAYS_SCORED_COMPONENTS)


def test_the_component_checkpoint_records_only_what_can_change_a_component():
    """Weights must NOT be recorded: re-using the checkpoint under new weights is the
    point of the sweep, and recording them would make it refuse itself."""
    recorded = scores.retrieval_config()
    assert "scoring" not in recorded and "weights" not in recorded
    assert {"intent_model", "retrieval", "recency_half_life_days"} <= set(recorded)


def test_a_component_checkpoint_from_different_retrieval_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "validation.jsonl"
    path.write_text(json.dumps(_case_scores().to_json()) + "\n", encoding="utf-8")
    stale = {**scores.retrieval_config(), "intent_model": "some/other-model"}
    scores.config_path("validation", path=path).write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(SystemExit, match="intent_model"):
        scores.load_scores("validation", path=path)


def test_a_matching_component_checkpoint_loads(tmp_path):
    path = tmp_path / "validation.jsonl"
    path.write_text(json.dumps(_case_scores().to_json()) + "\n", encoding="utf-8")
    scores.config_path("validation", path=path).write_text(
        json.dumps(scores.retrieval_config()), encoding="utf-8"
    )
    assert scores.load_scores("validation", path=path) == [_case_scores()]


def test_compare_variants_labels_the_frozen_v1_arm_apart_from_v2_namespaces(tmp_path,
                                                                            monkeypatch):
    monkeypatch.setattr(run_eval, "RUNS_DIR", tmp_path / "absent")
    rows = run_v2.compare_variants("validation", [run_v2.V1_ARM, "some_arm"])
    assert rows[0].startswith("| Arm |")
    assert len(rows) == 2                      # header only when no checkpoint exists


def test_the_ab_section_is_omitted_until_an_arm_has_run(monkeypatch):
    monkeypatch.setattr(run_v2, "compare_variants", lambda split, arms: ["header", "rule"])
    assert run_v2.ab_section() == []


def test_the_variance_section_is_omitted_without_a_component_checkpoint(monkeypatch):
    def missing(split):
        raise SystemExit("no score checkpoint")

    monkeypatch.setattr(scores, "load_scores", missing)
    assert run_v2.variance_section() == []
