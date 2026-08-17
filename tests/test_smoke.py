"""Toy-data tests for the dataset-independent core. Run: make test"""
from datetime import date, datetime

import pandas as pd

from capgraph.models import CandidateProfile, PersonCapability, RoleSpec
from capgraph.pipeline.stage1_bucket import build_buckets, quarter_of
from capgraph.pipeline.stage4_project import decay, period_end
from capgraph.query.rank import score_candidate
from capgraph.eval.run_eval import mrr, recall_at_k


def _tickets_df(n: int, person="p1", project="MESOS", month=3) -> pd.DataFrame:
    return pd.DataFrame([{
        "source_issue_id": str(i),
        "key": f"{project}-{i}", "project_key": project, "person_id": person,
        "person_name": "Pat Doe", "type": "Bug", "summary": f"Fix thing {i}",
        "evidence_person_id": person, "evidence_person_name": "Pat Doe",
        "summary_provenance": "snapshot_no_recorded_change",
        "description": None,
        "description_provenance": "empty_snapshot_no_recorded_change",
        "components": ["core"],
        "components_provenance": "snapshot_no_recorded_change", "labels": [],
        "resolution": "Fixed",
        "snapshot_resolved_at": datetime(2018, month, 10),
        "resolved_at": datetime(2018, month, 10),
        "resolved_at_provenance": "snapshot_no_recorded_resolution_change",
        "created_at": datetime(2018, month, 1), "query_time_source": "created_at",
        "temporal_exclusion_reason": None,
    } for i in range(n)])


def test_quarter_of():
    assert quarter_of(pd.Timestamp("2018-03-10")) == "2018-Q1"
    assert quarter_of(pd.Timestamp("2018-12-31")) == "2018-Q4"


def test_bucketing_groups_and_drops_sparse():
    df = pd.concat([_tickets_df(5), _tickets_df(2, person="p2")])
    buckets = build_buckets(df)
    assert len(buckets) == 1  # p2 dropped (< min_tickets_per_bucket)
    assert buckets[0].person_id == "p1" and len(buckets[0].tickets) == 5


def test_bucketing_respects_holdout_cutoff():
    df = _tickets_df(5)
    df["resolved_at"] = datetime(2025, 1, 1)  # after cutoff
    assert build_buckets(df) == []


def test_period_end_and_decay():
    assert period_end("2018-Q3") == date(2018, 9, 30)
    assert decay(date(2020, 1, 1), 540, as_of=date(2020, 1, 1)) == 1.0
    half = decay(date(2020, 1, 1), 540, as_of=date(2021, 6, 24))  # ~540 days
    assert 0.45 < half < 0.55


def _cap(term, kind, count=5, last=date(2018, 12, 28)):
    return PersonCapability(person_id="p1", term=term, kind=kind, evidence_count=count,
                            contribution_ids=["c1"], last_used=last, decay_score=0.8)


def test_score_candidate_orders_sensibly():
    role = RoleSpec(role="backend", specializations=["Distributed systems backend"],
                    skills=["Kafka", "streaming"])
    strong = score_candidate(CandidateProfile(
        person_id="p1", person_name="A",
        specializations=[_cap("Distributed systems backend", "specialization")],
        skills=[_cap("Kafka", "skill"), _cap("streaming", "skill")]), role)
    weak = score_candidate(CandidateProfile(
        person_id="p2", person_name="B",
        specializations=[_cap("Frontend web development", "specialization")],
        skills=[_cap("CSS", "skill")]), role)
    assert strong.score > weak.score > -1
    assert 0 <= strong.score <= 1


def test_metrics():
    assert recall_at_k(["a", "b", "c"], {"b"}, 5) == 1.0
    assert recall_at_k(["a", "b", "c"], {"z"}, 5) == 0.0
    assert mrr(["a", "b", "c"], {"b"}) == 0.5
