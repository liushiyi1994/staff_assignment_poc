"""The Stage 7 harness: temporal guards, ranking merges, checkpoints, and metrics.

Offline throughout — no Neo4j, no model, no manifest on disk. What is pinned here is
the machinery that decides whether a published number is trustworthy: recency measured
at the case's own query time, rankings that never leave the frozen roster, an
interrupted run that resumes instead of paying twice, and metric arithmetic at its
edges.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from capgraph.eval import run_eval
from capgraph.eval.contracts import BenchmarkQueryContext, RankingOutput
from capgraph.eval.costs import CostMeter
from capgraph.eval.holdout import BenchmarkManifestEntry
from capgraph.eval.metrics import _percentile, _summarize, evaluate, hit_at_k, mrr, recall_at_k
from capgraph.eval.systems import candidate_pool, full_ordering, round_robin, score_ordering
from capgraph.models import (
    CandidateProfile,
    Intent,
    QueryResult,
    RankedPerson,
    RoleSpec,
    ShortlistResult,
)
from capgraph.pipeline.stage4_project import decay, snapshot_date
from capgraph.query import retrieve
from capgraph.settings import settings

ROSTER = ("ALPHA:1", "ALPHA:2", "ALPHA:3")


# ---------- fakes ----------

class FakeSession:
    def __init__(self, driver):
        self._driver = driver

    def run(self, statement, **params):
        self._driver.calls.append((statement, params))
        for fragment, rows in self._driver.responses.items():
            if fragment in statement:
                return list(rows)
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDriver:
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


def _case(issue_id: str = "1", *, project: str = "ALPHA", truth=("ALPHA:1",), split="validation"):
    return BenchmarkManifestEntry(
        seed=1,
        issue_id=issue_id,
        issue_key=f"{project}-{issue_id}",
        query_text="Need someone who has worked on Kafka streaming consumer lag issues.",
        as_of_time=datetime(2019, 6, 1),
        project_key=project,
        eligible_roster=list(ROSTER),
        truth_person_ids=list(truth),
        split=split,
    )


def _context(case: BenchmarkManifestEntry) -> BenchmarkQueryContext:
    return BenchmarkQueryContext(
        issue_id=case.issue_id,
        query_text=case.query_text,
        as_of_time=case.as_of_time,
        project_key=case.project_key,
        eligible_roster=tuple(case.eligible_roster),
    )


class StubRanker:
    """A baseline stand-in that counts how often the harness actually calls it."""

    def __init__(self, ranked=ROSTER):
        self.ranked = list(ranked)
        self.calls = 0

    def rank(self, context: BenchmarkQueryContext) -> RankingOutput:
        self.calls += 1
        return RankingOutput(
            ranked_ids=self.ranked,
            candidate_ids=list(context.eligible_roster),
            latency_ms=1.0,
        )


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Point the harness at a two-case in-memory manifest and a temporary checkpoint."""
    cases = [_case("1"), _case("2")]
    monkeypatch.setattr(run_eval, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        run_eval, "load_manifest",
        lambda splits=None: [case for case in cases if splits is None or case.split in splits],
    )
    return cases


def _wire_baseline(monkeypatch, ranker):
    class StubView:
        tickets: tuple = ()
        documents: dict = {}

        @classmethod
        def load(cls):
            return cls()

        def write_document_cache(self):
            return None

    monkeypatch.setattr(run_eval, "EvidenceView", StubView)
    monkeypatch.setattr(run_eval, "build_baselines", lambda view, names: {"bm25": ranker})


# ---------- recency is recomputed at the case's as-of time ----------

def _capability_rows():
    return [{"person_id": "ALPHA:1", "term": "Kafka", "kind": "skill",
             "evidence_count": 4, "last_used": "2018-06-30"}]


def test_expansion_measures_recency_at_the_query_time_not_the_graph_cutoff():
    driver = FakeDriver({"HAS_SKILL|HAS_SPECIALIZATION": _capability_rows()})
    candidates = [CandidateProfile(person_id="ALPHA:1", person_name="Person ALPHA-1")]

    at_cutoff = retrieve.expand(candidates, driver)[0].skills[0].decay_score
    candidates[0].skills.clear()
    at_query = retrieve.expand(candidates, driver, as_of=datetime(2020, 6, 1))[0].skills[0]

    half_life = int(settings["projections.recency_half_life_days"])
    assert at_cutoff == pytest.approx(
        round(decay(date(2018, 6, 30), half_life, as_of=snapshot_date()), 4)
    )
    assert at_query.decay_score == pytest.approx(
        round(decay(date(2018, 6, 30), half_life, as_of=date(2020, 6, 1)), 4)
    )
    # A benchmark query time is always after the cutoff, so evidence must look older.
    assert at_query.decay_score < at_cutoff


def test_expansion_defaults_to_the_frozen_snapshot_so_ordinary_queries_are_unchanged():
    driver = FakeDriver({"HAS_SKILL|HAS_SPECIALIZATION": _capability_rows()})
    candidates = [CandidateProfile(person_id="ALPHA:1", person_name="Person ALPHA-1")]

    default = retrieve.expand(candidates, driver)[0].skills[0].decay_score
    candidates[0].skills.clear()
    explicit = retrieve.expand(candidates, driver, as_of=snapshot_date())[0].skills[0]

    assert default == explicit.decay_score


def test_the_structured_arm_decays_its_strength_at_the_query_time():
    resolution = retrieve.TermResolution(skills={"kafka": ["Kafka"]})
    driver = FakeDriver({"UNION ALL": [
        {"person_id": "ALPHA:1", "person_name": "Person ALPHA-1", "evidence_count": 4,
         "last_used": "2018-06-30", "matched": "Kafka"},
    ]})

    at_cutoff = retrieve.structured_candidates(resolution, driver)[0]["strength"]
    at_query = retrieve.structured_candidates(
        resolution, driver, as_of=datetime(2020, 6, 1)
    )[0]["strength"]

    half_life = int(settings["projections.recency_half_life_days"])
    assert at_cutoff == pytest.approx(4 * decay(date(2018, 6, 30), half_life,
                                                as_of=snapshot_date()))
    assert at_query < at_cutoff


# ---------- roster restriction reaches Cypher ----------

def test_both_arms_receive_the_roster_as_a_cypher_parameter():
    driver = FakeDriver()
    resolution = retrieve.TermResolution(skills={"kafka": ["Kafka"]})

    retrieve.generate_candidates(
        RoleSpec(role="eng", skills=["Kafka"]), "brief", driver,
        resolution=resolution, roster=ROSTER,
        embed_fn=lambda texts: [[0.0] * int(settings["embedding.dims"])] * len(texts),
    )

    assert driver.params_for("db.index.vector.queryNodes")["roster"] == sorted(ROSTER)
    assert driver.params_for("UNION ALL")["roster"] == sorted(ROSTER)


def test_the_roster_restricted_vector_arm_asks_the_index_for_a_wider_pool():
    driver = FakeDriver()
    embed = lambda texts: [[0.0] * int(settings["embedding.dims"])] * len(texts)  # noqa: E731

    retrieve.vector_candidates(RoleSpec(role="eng"), "brief", driver, embed_fn=embed)
    unrestricted = driver.params_for("db.index.vector.queryNodes")["k"]

    driver.calls.clear()
    retrieve.vector_candidates(RoleSpec(role="eng"), "brief", driver, embed_fn=embed,
                               roster=ROSTER)
    restricted = driver.params_for("db.index.vector.queryNodes")["k"]

    assert unrestricted == int(settings["retrieval.vector_top_k"])
    assert restricted == int(settings["retrieval.roster_vector_pool_k"]) > unrestricted


def test_a_ranking_that_leaves_the_roster_is_refused():
    case = _case()
    with pytest.raises(ValueError, match="leaves the frozen roster"):
        run_eval.assert_within_roster(RankingOutput(ranked_ids=["BETA:9"]), case)


def test_a_candidate_pool_that_leaves_the_roster_is_refused():
    case = _case()
    with pytest.raises(ValueError, match="candidate pool"):
        run_eval.assert_within_roster(
            RankingOutput(ranked_ids=["ALPHA:1"], candidate_ids=["ALPHA:1", "BETA:9"]), case
        )


def test_an_out_of_roster_system_is_recorded_as_a_failure_not_a_result(harness, monkeypatch):
    _wire_baseline(monkeypatch, StubRanker(ranked=["ALPHA:1", "BETA:9"]))

    counts = run_eval.run_split("validation", systems=["bm25"])

    assert counts["failed"] == 2
    records = run_eval.load_checkpoint("validation")
    assert all("error" in record for record in records.values())
    assert "leaves the frozen roster" in records[("bm25", "1")]["error"]


# ---------- merging several roles into one ranking ----------

def _result(*shortlists: ShortlistResult) -> QueryResult:
    return QueryResult(
        brief="b", intent=Intent(roles=[s.role for s in shortlists]), shortlists=list(shortlists)
    )


def _shortlist(role: str, ranking: list[str], scored: list[str], pool: list[str] | None = None):
    return ShortlistResult(
        role=RoleSpec(role=role),
        ranking=[
            RankedPerson(person_id=person_id, person_name=person_id, fit="good", reason="r",
                         score=0.5)
            for person_id in ranking
        ],
        scored_person_ids=scored,
        candidate_person_ids=scored if pool is None else pool,
    )


def test_round_robin_interleaves_roles_without_privileging_the_first():
    assert round_robin([["a", "b", "c"], ["x", "y"]]) == ["a", "x", "b", "y", "c"]


def test_round_robin_deduplicates_a_person_wanted_by_two_roles():
    assert round_robin([["a", "b"], ["b", "c"]]) == ["a", "b", "c"]


def test_the_full_ranking_pads_the_rerank_with_the_deterministic_remainder():
    result = _result(_shortlist("eng", ranking=["p3"], scored=["p1", "p2", "p3"]))

    assert full_ordering(result) == ["p3", "p1", "p2"]
    assert score_ordering(result) == ["p1", "p2", "p3"]


def test_the_candidate_pool_is_the_union_across_roles():
    result = _result(
        _shortlist("a", ranking=[], scored=["p1"], pool=["p1", "p2"]),
        _shortlist("b", ranking=[], scored=["p3"], pool=["p2", "p3"]),
    )
    assert candidate_pool(result) == ["p1", "p2", "p3"]


# ---------- checkpoints and resume ----------

def test_a_completed_case_is_not_run_again(harness, monkeypatch):
    ranker = StubRanker()
    _wire_baseline(monkeypatch, ranker)

    first = run_eval.run_split("validation", systems=["bm25"])
    second = run_eval.run_split("validation", systems=["bm25"])

    assert first["ran"] == 2 and ranker.calls == 2
    assert second["ran"] == 0 and second["skipped"] == 2
    assert ranker.calls == 2                      # the resume paid for nothing


def test_an_interrupted_run_resumes_from_the_case_it_reached(harness, monkeypatch):
    ranker = StubRanker()
    _wire_baseline(monkeypatch, ranker)

    run_eval.run_split("validation", systems=["bm25"], limit=1)
    resumed = run_eval.run_split("validation", systems=["bm25"])

    assert ranker.calls == 2
    assert resumed["skipped"] == 1 and resumed["ran"] == 1
    assert sorted(issue_id for _, issue_id in run_eval.load_checkpoint("validation")) == ["1", "2"]


def test_a_checkpoint_from_another_configuration_is_refused(harness, monkeypatch):
    _wire_baseline(monkeypatch, StubRanker())
    run_eval.run_split("validation", systems=["bm25"])
    monkeypatch.setattr(run_eval, "config_digest", lambda config=None: "0000000000000000")

    with pytest.raises(SystemExit, match="must not mix configurations"):
        run_eval.run_split("validation", systems=["bm25"])


def test_a_later_record_supersedes_an_earlier_one_for_the_same_case(harness, monkeypatch):
    _wire_baseline(monkeypatch, StubRanker())
    run_eval.run_split("validation", systems=["bm25"])
    path = run_eval.checkpoint_path("validation")
    path.write_text(
        path.read_text()
        + json.dumps({"system": "bm25", "issue_id": "1", "ranked_ids": ["ALPHA:2"]}) + "\n"
    )

    assert run_eval.load_checkpoint("validation")[("bm25", "1")]["ranked_ids"] == ["ALPHA:2"]


def test_unknown_systems_are_refused_before_anything_runs(harness):
    with pytest.raises(ValueError, match="unknown system"):
        run_eval.run_split("validation", systems=["astrology"])


# ---------- scoring the checkpoint ----------

def test_the_report_scores_successes_and_lists_failures(harness, monkeypatch):
    _wire_baseline(monkeypatch, StubRanker())
    run_eval.run_split("validation", systems=["bm25"])
    run_eval.append_record("validation", {
        "split": "validation", "system": "bm25", "issue_id": "2", "issue_key": "ALPHA-2",
        "project_key": "ALPHA", "config_digest": run_eval.config_digest(),
        "error": "RuntimeError('graph unavailable')",
    })

    results, failures = run_eval.score_split("validation", systems=["bm25"])

    assert results[0].n_briefs == 1                     # the failed case is not scored
    assert failures["bm25"][0]["issue_id"] == "2"


def test_the_rendered_report_names_every_system_and_keeps_the_caveats(harness, monkeypatch):
    _wire_baseline(monkeypatch, StubRanker())
    run_eval.run_split("validation", systems=["bm25"])

    markdown = run_eval.render_report({"validation": run_eval.score_split(
        "validation", systems=["bm25"])})

    assert "Validation split" in markdown
    assert "prediction target" in markdown
    assert "Method and leakage guards" in markdown
    assert "roster 3" in markdown                       # per-project table carries the size
    assert "| `bm25` | 2 | 2 | 0 |" in markdown         # every case is accounted for


def _graph_record(issue_id: str, *, ranked: int, roles: int, rejected: list[str]) -> dict:
    return {
        "split": "validation", "system": run_eval.CAPGRAPH_FULL, "issue_id": issue_id,
        "issue_key": f"ALPHA-{issue_id}", "project_key": "ALPHA",
        "config_digest": run_eval.config_digest(), "ranked_ids": list(ROSTER),
        "candidate_ids": list(ROSTER), "latency_ms": 1.0, "cost_usd": 0.03,
        "detail": {"roles": ["eng"] * roles, "n_ranked_by_rerank": ranked,
                   "n_llm_calls": 1 + roles, "rejected": rejected},
    }


def test_diagnostics_report_what_the_rerank_actually_returned(harness):
    run_eval.append_record("validation", _graph_record("1", ranked=15, roles=1, rejected=[]))
    run_eval.append_record(
        "validation",
        _graph_record("2", ranked=8, roles=2, rejected=["p9: cites no evidence ticket key"]),
    )

    diagnostics = run_eval.run_diagnostics("validation")

    assert diagnostics["cases"] == 2
    assert diagnostics["multi_role_cases"] == 1
    assert diagnostics["llm_calls"] == 5
    assert diagnostics["cases_below_ten_ranked"] == 1
    assert diagnostics["rejected_rerank_entries"] == 1
    assert diagnostics["rejection_reasons"] == ["cites no evidence ticket key"]


def test_diagnostics_of_a_split_with_no_graph_run_are_empty(harness):
    assert run_eval.run_diagnostics("validation") == {}


def _summary(**metrics):
    defaults = dict(
        hit_at_1=0.0, hit_at_5=0.0, hit_at_10=0.0, recall_at_5=0.0, recall_at_10=0.0,
        mrr=0.0, candidate_recall=None, n_briefs=1, latency_ms_mean=0.0,
        latency_ms_median=0.0, latency_ms_p95=0.0, cost_usd_total=0.0,
    )
    return run_eval.EvaluationResult(**{**defaults, **metrics})


def test_the_comparison_picks_the_best_baseline_per_metric_and_shows_a_loss():
    results = [
        _summary(system=run_eval.CAPGRAPH_FULL, hit_at_1=0.30, hit_at_5=0.50),
        _summary(system="bm25", hit_at_1=0.10, hit_at_5=0.60),
        _summary(system="most_active", hit_at_1=0.25, hit_at_5=0.20),
    ]

    rows = "\n".join(run_eval._versus_baselines(results))

    assert "| Hit@1 | 0.300 | 0.250 (`most_active`) | +0.050 |" in rows
    assert "| Hit@5 | 0.500 | 0.600 (`bm25`) | -0.100 |" in rows   # a loss is not hidden


def test_the_comparison_is_omitted_when_there_is_nothing_to_compare():
    assert run_eval._versus_baselines([_summary(system="bm25")]) == []


# ---------- metric arithmetic ----------

def test_hit_at_k_is_binary_and_recall_at_k_is_fractional():
    ranked, truth = ["a", "b", "c"], {"a", "z"}
    assert hit_at_k(ranked, truth, 1) == 1.0
    assert recall_at_k(ranked, truth, 1) == 0.5
    assert hit_at_k(ranked, truth, 10) == 1.0           # k beyond the list is not an error


def test_metrics_ignore_repeated_ids_when_counting_positions():
    assert mrr(["a", "a", "b"], {"b"}) == pytest.approx(0.5)
    assert hit_at_k(["a", "a", "b"], {"b"}, 2) == 1.0


def test_metrics_of_a_miss_and_of_an_empty_truth_set_are_zero():
    assert mrr(["a"], {"z"}) == 0.0
    assert hit_at_k(["a"], set(), 1) == 0.0
    assert recall_at_k([], {"a"}, 5) == 0.0


def test_k_below_one_is_an_error_rather_than_an_empty_slice():
    with pytest.raises(ValueError):
        hit_at_k(["a"], {"a"}, 0)


def test_percentiles_and_the_median_come_from_the_measured_latencies():
    case = _case()
    outputs = iter([12.0, 4.0, 100.0])
    briefs = [_case("1"), _case("2"), _case("3")]

    result = evaluate(
        "fixture",
        lambda context: RankingOutput(ranked_ids=["ALPHA:1"], latency_ms=next(outputs)),
        briefs,
    )

    assert result.latency_ms_median == 12.0
    assert result.latency_ms_mean == pytest.approx((12.0 + 4.0 + 100.0) / 3)
    assert result.latency_ms_p95 == 100.0
    assert _summarize([]).latency_ms_median == 0.0
    assert case.project_key == "ALPHA"


def test_percentile_of_an_empty_series_is_zero():
    assert _percentile([], 0.95) == 0.0


# ---------- per-case spend ----------

def test_the_cost_meter_sums_only_this_stage_and_splits_it_by_purpose(tmp_path):
    path = tmp_path / "llm_costs.jsonl"
    path.write_text(json.dumps({"stage": "other", "cost_usd": 9.0}) + "\n")
    meter = CostMeter(path)
    with path.open("a") as handle:
        for record in (
            {"stage": "stage7_eval", "cost_usd": 0.002, "purpose": "intent"},
            {"stage": "stage7_eval", "cost_usd": 0.030, "purpose": "rerank"},
            {"stage": "stage6_pilot", "cost_usd": 5.0, "purpose": "rerank"},
        ):
            handle.write(json.dumps(record) + "\n")

    spend = meter.spend_since(stage="stage7_eval")

    assert spend.total == pytest.approx(0.032)
    assert spend.n_calls == 2
    assert spend.by_purpose == {"intent": pytest.approx(0.002), "rerank": pytest.approx(0.030)}


def test_the_cost_meter_window_advances_so_a_case_is_never_billed_twice(tmp_path):
    path = tmp_path / "llm_costs.jsonl"
    path.write_text("")
    meter = CostMeter(path)
    with path.open("a") as handle:
        handle.write(json.dumps({"stage": "s", "cost_usd": 1.0}) + "\n")

    assert meter.spend_since(stage="s").total == 1.0
    assert meter.spend_since(stage="s").total == 0.0


def test_the_cost_meter_of_a_missing_log_is_empty(tmp_path):
    assert CostMeter(tmp_path / "absent.jsonl").spend_since().total == 0.0
