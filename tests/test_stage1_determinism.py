from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pandas as pd
import pytest

from capgraph.pipeline.stage1_bucket import build_buckets
from capgraph.settings import settings


def _rows(person_id: str, count: int, project: str = "PROJ") -> list[dict]:
    start = datetime(2018, 1, 1)
    return [
        {
            "source_issue_id": str(index),
            "key": f"{project}-{index:03d}",
            "project_key": project,
            "person_id": person_id,
            "person_name": f"Person {project}-{person_id.split(':')[-1]}",
            "evidence_person_id": person_id,
            "evidence_person_name": f"Person {project}-{person_id.split(':')[-1]}",
            "type": "Task",
            "summary": f"Work item {index}",
            "summary_provenance": "snapshot_no_recorded_change",
            "description": None,
            "description_provenance": "empty_snapshot_no_recorded_change",
            "components": ["core"],
            "components_provenance": "snapshot_no_recorded_change",
            "labels": [],
            "resolution": "Fixed",
            "snapshot_resolved_at": start + timedelta(days=30, hours=index),
            "created_at": start + timedelta(hours=index),
            "resolved_at": start + timedelta(days=30, hours=index),
            "resolved_at_provenance": "snapshot_no_recorded_resolution_change",
            "query_time_source": "created_at",
            "temporal_exclusion_reason": None,
        }
        for index in range(count)
    ]


@pytest.fixture
def bucket_settings(monkeypatch):
    monkeypatch.setitem(
        settings._cfg["dataset"], "project_domains", {"PROJ": "distributed systems"}
    )


def test_bucket_membership_is_independent_of_input_order(bucket_settings):
    tickets = pd.DataFrame(_rows("PROJ:1", 35))

    first = build_buckets(tickets, eligible_person_ids={"PROJ:1"})
    shuffled = build_buckets(
        tickets.sample(frac=1, random_state=42), eligible_person_ids={"PROJ:1"}
    )

    assert [bucket.model_dump(mode="json") for bucket in first] == [
        bucket.model_dump(mode="json") for bucket in shuffled
    ]
    assert [len(bucket.tickets) for bucket in first] == [30, 5]


@pytest.mark.parametrize(
    ("ticket_count", "expected_sizes"),
    [(31, [28, 3]), (32, [29, 3])],
)
def test_undersized_tail_is_rebalanced_without_dropping_tickets(
    bucket_settings, ticket_count, expected_sizes
):
    tickets = pd.DataFrame(_rows("PROJ:1", ticket_count))

    buckets = build_buckets(tickets, eligible_person_ids={"PROJ:1"})

    emitted_ids = [ticket.source_issue_id for bucket in buckets for ticket in bucket.tickets]
    assert [len(bucket.tickets) for bucket in buckets] == expected_sizes
    assert Counter(emitted_ids) == Counter(tickets["source_issue_id"].astype(str))


def test_chunking_has_no_loss_or_duplication_for_all_boundary_counts(bucket_settings):
    min_n = settings["bucketing.min_tickets_per_bucket"]
    max_n = settings["bucketing.max_tickets_per_bucket"]
    rows: list[dict] = []
    expected_by_person: dict[str, list[str]] = {}
    eligible_person_ids: set[str] = set()
    for ticket_count in range(min_n, max_n * 3 + 1):
        person_id = f"PROJ:{ticket_count}"
        eligible_person_ids.add(person_id)
        person_rows = _rows(person_id, ticket_count)
        for row in person_rows:
            row["source_issue_id"] = f"{ticket_count}:{row['source_issue_id']}"
            row["key"] = f"PROJ-{ticket_count}-{row['key']}"
        rows.extend(person_rows)
        expected_by_person[person_id] = [row["source_issue_id"] for row in person_rows]

    buckets = build_buckets(
        pd.DataFrame(rows), eligible_person_ids=eligible_person_ids
    )

    emitted_by_person: dict[str, list[str]] = defaultdict(list)
    for bucket in buckets:
        assert min_n <= len(bucket.tickets) <= max_n
        emitted_by_person[bucket.person_id].extend(
            ticket.source_issue_id for ticket in bucket.tickets
        )
    assert emitted_by_person.keys() == expected_by_person.keys()
    for person_id, expected_ids in expected_by_person.items():
        assert Counter(emitted_by_person[person_id]) == Counter(expected_ids)


def test_stage1_uses_only_explicitly_frozen_roster(bucket_settings):
    tickets = pd.DataFrame(_rows("PROJ:1", 3) + _rows("PROJ:2", 3))

    buckets = build_buckets(tickets, eligible_person_ids={"PROJ:2"})

    assert {bucket.person_id for bucket in buckets} == {"PROJ:2"}


def test_stage1_rejects_roster_person_without_retained_profile(bucket_settings):
    rows = _rows("PROJ:1", 3)
    for index, month in enumerate((2, 5, 8)):
        rows[index]["created_at"] = datetime(2018, month - 1, 1)
        rows[index]["resolved_at"] = datetime(2018, month, 1)

    with pytest.raises(ValueError, match="without a retained Stage 1 profile"):
        build_buckets(pd.DataFrame(rows), eligible_person_ids={"PROJ:1"})


def test_stage1_uses_resolution_owner_not_eventual_assignee(bucket_settings):
    tickets = pd.DataFrame(_rows("PROJ:1", 3))
    tickets["evidence_person_id"] = "PROJ:2"
    tickets["evidence_person_name"] = "Person PROJ-2"

    buckets = build_buckets(tickets, eligible_person_ids={"PROJ:2"})

    assert len(buckets) == 1
    assert buckets[0].person_id == "PROJ:2"
    assert buckets[0].person_name == "Person PROJ-2"
    assert {ticket.person_id for ticket in buckets[0].tickets} == {None}
    assert {ticket.evidence_person_id for ticket in buckets[0].tickets} == {"PROJ:2"}
    assert all(ticket.components == [] for ticket in buckets[0].tickets)
    assert all(
        ticket.components_provenance == "redacted_unversioned_component_name"
        for ticket in buckets[0].tickets
    )


def test_missing_project_domain_fails_instead_of_emitting_blank(monkeypatch):
    monkeypatch.setitem(settings._cfg["dataset"], "project_domains", {})
    tickets = pd.DataFrame(_rows("PROJ:1", 3))

    with pytest.raises(ValueError, match="project_domains"):
        build_buckets(tickets, eligible_person_ids={"PROJ:1"})


def test_stage1_rejects_legacy_final_assignee_only_rows(bucket_settings):
    tickets = pd.DataFrame(_rows("PROJ:1", 3)).drop(
        columns=["evidence_person_id"]
    )

    with pytest.raises(ValueError, match="hardened Stage 0 columns"):
        build_buckets(tickets, eligible_person_ids={"PROJ:1"})


def test_stage1_rejects_mutable_snapshot_text(bucket_settings):
    tickets = pd.DataFrame(_rows("PROJ:1", 3))
    tickets.loc[0, "summary_provenance"] = "final_snapshot_after_recorded_change"

    with pytest.raises(ValueError, match="unsafe summary provenance"):
        build_buckets(tickets, eligible_person_ids={"PROJ:1"})


def test_stage1_ignores_explicitly_excluded_unsafe_resolution_row(bucket_settings):
    rows = _rows("PROJ:1", 4)
    rows[-1]["resolved_at_provenance"] = "omitted_resolutiondate_change_log"
    rows[-1]["temporal_exclusion_reason"] = "resolution_date_changed"
    rows[-1]["evidence_person_id"] = None
    rows[-1]["evidence_person_name"] = None

    buckets = build_buckets(
        pd.DataFrame(rows), eligible_person_ids={"PROJ:1"}
    )

    assert len(buckets) == 1
    assert len(buckets[0].tickets) == 3
