"""Focused Stage 0 tests using a minimal TAWOS v1.1-shaped schema."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, event

from capgraph.pipeline.stage0_load import (
    ISSUES_SQL,
    _eligible_person_ids,
    export,
    report,
    strip_markup,
)
from capgraph.pipeline.stage1_bucket import build_buckets
from capgraph.settings import settings


@pytest.fixture
def stage0_settings(monkeypatch):
    dataset = settings._cfg["dataset"]
    eval_config = settings._cfg["eval"]
    monkeypatch.setitem(dataset, "projects", ["PROJ"])
    monkeypatch.setitem(dataset, "project_domains", {"PROJ": "distributed systems"})
    monkeypatch.setitem(dataset, "min_tickets_per_person", 15)
    monkeypatch.setitem(dataset, "holdout_cutoff", "2019-01-01")
    monkeypatch.setitem(eval_config, "min_brief_chars", 20)


@pytest.fixture
def tawos_engine(stage0_settings):
    """Create only the official columns touched by Stage 0."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def register_char_length(dbapi_connection, _connection_record):
        dbapi_connection.create_function(
            "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None
        )

    ddl = [
        """
        CREATE TABLE `Repository` (
            `ID` INTEGER PRIMARY KEY,
            `Name` TEXT,
            `Description` TEXT,
            `URL` TEXT
        )
        """,
        """
        CREATE TABLE `Project` (
            `ID` INTEGER PRIMARY KEY,
            `Project_Key` TEXT,
            `Name` TEXT,
            `Repository_ID` INTEGER
        )
        """,
        """
        CREATE TABLE `User` (
            `ID` INTEGER PRIMARY KEY,
            `Project_ID` INTEGER
        )
        """,
        """
        CREATE TABLE `Issue` (
            `ID` INTEGER PRIMARY KEY,
            `Issue_Key` TEXT,
            `Title` TEXT,
            `Description` TEXT,
            `Description_Text` TEXT,
            `Type` TEXT,
            `Resolution` TEXT,
            `Creation_Date` DATETIME,
            `Resolution_Date` DATETIME,
            `Assignee_ID` INTEGER,
            `Project_ID` INTEGER,
            `Sprint_ID` INTEGER,
            `Story_Point` REAL,
            `Timespent` REAL,
            `In_Progress_Minutes` REAL,
            `Total_Effort_Minutes` REAL
        )
        """,
        """
        CREATE TABLE `Sprint` (
            `ID` INTEGER PRIMARY KEY,
            `JiraID` INTEGER,
            `Name` TEXT,
            `State` TEXT,
            `Start_Date` DATETIME,
            `End_Date` DATETIME,
            `Activated_Date` DATETIME,
            `Complete_Date` DATETIME,
            `Project_ID` INTEGER
        )
        """,
        """
        CREATE TABLE `Component` (
            `ID` INTEGER PRIMARY KEY,
            `Name` TEXT,
            `Project_ID` INTEGER
        )
        """,
        """
        CREATE TABLE `Issue_Component` (
            `Issue_ID` INTEGER,
            `Component_ID` INTEGER
        )
        """,
        """
        CREATE TABLE `Comment` (
            `ID` INTEGER PRIMARY KEY,
            `Comment` TEXT,
            `Comment_Text` TEXT,
            `Creation_Date` DATETIME,
            `Author_ID` INTEGER,
            `Issue_ID` INTEGER
        )
        """,
        """
        CREATE TABLE `Change_Log` (
            `ID` INTEGER PRIMARY KEY,
            `Field` TEXT,
            `From_Value` TEXT,
            `To_Value` TEXT,
            `From_String` TEXT,
            `To_String` TEXT,
            `Change_Type` TEXT,
            `Creation_Date` DATETIME,
            `Author_ID` INTEGER,
            `Issue_ID` INTEGER
        )
        """,
    ]
    with engine.begin() as connection:
        for statement in ddl:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO `Repository` (`ID`, `Name`) VALUES (1, 'Apache')"
        )
        connection.exec_driver_sql(
            """INSERT INTO `Project` (`ID`, `Project_Key`, `Name`, `Repository_ID`)
               VALUES (1, 'PROJ', 'Fixture Project', 1)"""
        )
        connection.exec_driver_sql(
            "INSERT INTO `User` (`ID`, `Project_ID`) VALUES (10, 1), (11, 1), (20, 2)"
        )
        connection.exec_driver_sql(
            """INSERT INTO `Component` (`ID`, `Name`, `Project_ID`)
               VALUES (1, 'core', 1), (2, 'api', 1), (3, 'other-project', 2)"""
        )

        # User 10 qualifies with exactly 15 resolved pre-cutoff issues.
        for issue_id in range(1, 16):
            description_text = "plain description"
            description_raw = "<p>raw description</p>"
            if issue_id == 1:
                description_text = None
                description_raw = "<p>Raw {code}fallback{code}</p>"
            elif issue_id == 2:
                description_text = None
                description_raw = None
            connection.exec_driver_sql(
                """
                INSERT INTO `Issue` (
                    `ID`, `Issue_Key`, `Title`, `Description`, `Description_Text`,
                    `Type`, `Resolution`, `Creation_Date`, `Resolution_Date`,
                    `Assignee_ID`, `Project_ID`
                ) VALUES (?, ?, ?, ?, ?, 'Bug', 'Fixed', ?, ?, 10, 1)
                """,
                (
                    issue_id,
                    f"PROJ-{issue_id}",
                    f"Fix issue {issue_id}",
                    description_raw,
                    description_text,
                    f"2018-01-{min(issue_id, 28):02d} 09:00:00",
                    f"2018-02-{min(issue_id, 28):02d} 09:00:00",
                ),
            )

        # Two sprints, identified by (project, Jira id) the way the change log does.
        connection.exec_driver_sql(
            """INSERT INTO `Sprint` (
                    `ID`, `JiraID`, `Name`, `State`, `Start_Date`, `End_Date`,
                    `Activated_Date`, `Complete_Date`, `Project_ID`
               ) VALUES
                 (1, 500, 'PROJ Sprint 12', 'CLOSED', '2020-01-15 09:00:00',
                  '2020-01-29 09:00:00', NULL, '2020-01-29 17:00:00', 1),
                 (2, 501, 'PROJ Sprint 13', 'CLOSED', '2020-02-01 09:00:00',
                  '2020-02-15 09:00:00', NULL, '2020-02-15 17:00:00', 1)"""
        )

        # A created-post-cutoff issue for the eligible user is retained for holdout.
        connection.exec_driver_sql(
            """
            INSERT INTO `Issue` (
                `ID`, `Issue_Key`, `Title`, `Description`, `Description_Text`,
                `Type`, `Resolution`, `Creation_Date`, `Resolution_Date`,
                `Assignee_ID`, `Project_ID`, `Sprint_ID`, `Story_Point`,
                `Timespent`, `In_Progress_Minutes`, `Total_Effort_Minutes`
            ) VALUES (
                100, 'PROJ-100', 'Implement a substantial future capability', NULL,
                'Long enough held-out issue description', 'Story', 'Fixed',
                '2020-01-01 09:00:00', '2020-02-01 09:00:00', 10, 1, 1, 5.0,
                3600.0, 120.0, 240.0
            )
            """
        )
        # User 11 has activity but no qualifying pre-cutoff history.
        connection.exec_driver_sql(
            """
            INSERT INTO `Issue` (
                `ID`, `Issue_Key`, `Title`, `Description_Text`, `Type`, `Resolution`,
                `Creation_Date`, `Resolution_Date`, `Assignee_ID`, `Project_ID`
            ) VALUES (
                101, 'PROJ-101', 'Ineligible user work', 'Detailed future issue text',
                'Story', 'Fixed', '2020-03-01 09:00:00', '2020-04-01 09:00:00', 11, 1
            )
            """
        )

        connection.exec_driver_sql(
            """INSERT INTO `Issue_Component` (`Issue_ID`, `Component_ID`)
               VALUES (1, 1), (1, 2), (1, 1)"""
        )
        # Sprint joins: PROJ-100 was planned into sprint 500 five days before it
        # started, PROJ-101 was pulled in after it started, and PROJ-100 later moved
        # on to sprint 501 without leaving 500.
        connection.exec_driver_sql(
            """INSERT INTO `Change_Log` (
                    `ID`, `Field`, `From_Value`, `To_Value`, `From_String`, `To_String`,
                    `Creation_Date`, `Issue_ID`
               ) VALUES
                 (900, 'Sprint', NULL, '500', NULL, 'PROJ Sprint 12',
                  '2020-01-10 09:00:00', 100),
                 (901, 'Sprint', '500', '500, 501', 'PROJ Sprint 12',
                  'PROJ Sprint 12, PROJ Sprint 13', '2020-02-02 09:00:00', 100),
                 (902, 'Sprint', NULL, '500', NULL, 'PROJ Sprint 12',
                  '2020-01-20 09:00:00', 101)"""
        )
        # This late comment must not become the empty description of PROJ-2.
        connection.exec_driver_sql(
            """INSERT INTO `Comment` (
                    `ID`, `Comment_Text`, `Creation_Date`, `Author_ID`, `Issue_ID`
                ) VALUES (
                    1, 'SECRET POST-CREATION COMMENT', '2018-01-20 09:00:00', 10, 2
                )"""
        )
    return engine


def test_export_uses_real_joins_and_pseudonymous_project_scoped_ids(tawos_engine, tmp_path):
    tickets, people, projects = export(tawos_engine, tmp_path)

    assert len(tickets) == 17
    assert set(tickets["person_id"].dropna()) == {"PROJ:10", "PROJ:11"}
    assert set(tickets["person_name"].dropna()) == {
        "Person PROJ-10",
        "Person PROJ-11",
    }
    assert tickets["labels"].map(list).tolist() == [[] for _ in range(17)]

    issue_1 = tickets.loc[tickets["key"].eq("PROJ-1")].iloc[0]
    assert issue_1["components"] == ["api", "core"]
    assert issue_1["description"] == "Raw fallback"

    issue_2 = tickets.loc[tickets["key"].eq("PROJ-2")].iloc[0]
    assert issue_2["description"] is None
    assert "SECRET" not in " ".join(tickets["description"].dropna())

    assert people.iloc[0]["ticket_count"] == 15
    assert "total_exported_ticket_count" not in people
    assert projects.iloc[0]["domain"] == "distributed systems"
    assert list(
        zip(tickets["project_key"], tickets["source_issue_id"].astype(int), strict=True)
    ) == sorted(
        zip(tickets["project_key"], tickets["source_issue_id"].astype(int), strict=True)
    )
    assert {path.name for path in tmp_path.iterdir()} == {
        "tickets.parquet",
        "people.parquet",
        "projects.parquet",
        "sprints.parquet",
        "sprint_membership.parquet",
    }
    ticket_schema = pq.read_schema(tmp_path / "tickets.parquet")
    people_schema = pq.read_schema(tmp_path / "people.parquet")
    assert ticket_schema.field("components").type == pa.list_(pa.string())
    assert ticket_schema.field("labels").type == pa.list_(pa.string())
    assert people_schema.field("project_keys").type == pa.list_(pa.string())
    round_trip_tickets = pd.read_parquet(tmp_path / "tickets.parquet")
    round_trip_people = pd.read_parquet(tmp_path / "people.parquet")
    assert round_trip_tickets["components"].map(list).tolist() == tickets[
        "components"
    ].map(list).tolist()
    assert round_trip_tickets["labels"].map(list).tolist() == tickets["labels"].map(
        list
    ).tolist()
    assert round_trip_people["project_keys"].map(list).tolist() == people[
        "project_keys"
    ].map(list).tolist()


def test_sprint_export_carries_dated_joins_and_keeps_them_out_of_evidence(
    tawos_engine, tmp_path
):
    """The v4 grouping key: dated joins for as-of discipline, snapshot for audit."""
    tickets, _people, _projects = export(tawos_engine, tmp_path)

    sprints = pd.read_parquet(tmp_path / "sprints.parquet")
    assert list(sprints["sprint_jira_id"]) == [500, 501]
    assert sprints["activated_date"].isna().all()          # as in the real slice
    assert list(sprints["start_date"].astype(str)) == [
        "2020-01-15 09:00:00",
        "2020-02-01 09:00:00",
    ]

    membership = pd.read_parquet(tmp_path / "sprint_membership.parquet")
    dated = membership[membership["provenance"].eq("change_log_sprint_join")]
    joins = sorted(
        (row.source_issue_id, int(row.sprint_jira_id), str(row.added_at))
        for row in dated.itertuples(index=False)
    )
    assert joins == [
        ("100", 500, "2020-01-10 09:00:00"),
        ("100", 501, "2020-02-02 09:00:00"),
        ("101", 500, "2020-01-20 09:00:00"),
    ]
    # The transition that only *kept* sprint 500 must not re-join the issue to it.
    assert sum(1 for join in joins if join[:2] == ("100", 500)) == 1

    snapshot = membership[membership["provenance"].eq("final_snapshot_join_time_unknown")]
    assert list(snapshot["source_issue_id"]) == ["100"]
    assert snapshot["added_at"].isna().all()

    issue_100 = tickets.loc[tickets["key"].eq("PROJ-100")].iloc[0]
    assert issue_100["sprint_id"] == "1"
    assert issue_100["sprint_provenance"] == "final_snapshot_with_recorded_sprint_change"
    assert issue_100["story_point"] == 5.0
    assert issue_100["total_effort_minutes"] == 240.0
    assert tickets.loc[tickets["key"].eq("PROJ-1")].iloc[0]["sprint_id"] is None

    # Nothing about sprint membership or effort may reach profile evidence.
    buckets = build_buckets(tickets)
    evidence = [ticket for bucket in buckets for ticket in bucket.tickets]
    assert evidence
    assert all(ticket.sprint_id is None for ticket in evidence)
    assert all(ticket.story_point is None for ticket in evidence)
    assert all(ticket.total_effort_minutes is None for ticket in evidence)
    assert {ticket.sprint_provenance for ticket in evidence} == {
        "redacted_unversioned_final_snapshot"
    }


def test_issue_query_has_an_explicit_stable_order() -> None:
    normalized_sql = " ".join(str(ISSUES_SQL).split())

    assert normalized_sql.endswith("ORDER BY p.`Project_Key`, i.`ID`")


def test_roster_threshold_never_counts_post_cutoff_tickets(stage0_settings):
    rows = []
    for _ in range(14):
        rows.append({
            "person_id": "PROJ:1",
            "evidence_person_id": "PROJ:1",
            "project_key": "PROJ",
            "created_at": datetime(2017, 12, 1),
            "resolved_at": datetime(2018, 1, 1),
            "temporal_exclusion_reason": None,
        })
    for _ in range(20):
        rows.append({
            "person_id": "PROJ:1",
            "evidence_person_id": "PROJ:1",
            "project_key": "PROJ",
            "created_at": datetime(2019, 12, 1),
            "resolved_at": datetime(2020, 1, 1),
            "temporal_exclusion_reason": None,
        })
    for _ in range(15):
        rows.append({
            "person_id": "PROJ:2",
            "evidence_person_id": "PROJ:2",
            "project_key": "PROJ",
            "created_at": datetime(2017, 12, 1),
            "resolved_at": datetime(2018, 1, 1),
            "temporal_exclusion_reason": None,
        })

    # Fifteen tickets meet the activity threshold, but one ticket in each of
    # fifteen quarters cannot produce the configured three-ticket profile bucket.
    for index in range(15):
        created_at = datetime(2015 + index // 4, 1 + 3 * (index % 4), 1)
        rows.append({
            "person_id": "PROJ:3",
            "evidence_person_id": "PROJ:3",
            "project_key": "PROJ",
            "created_at": created_at,
            "resolved_at": created_at + timedelta(days=1),
            "temporal_exclusion_reason": None,
        })

    assert _eligible_person_ids(pd.DataFrame(rows)) == {"PROJ:2"}


def test_report_statistics_and_artifacts(tawos_engine, tmp_path):
    # A post-resolution reassignment must not reduce user 10's historical count
    # from the eligibility threshold of 15 to 14.
    with tawos_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE `Issue` SET `Assignee_ID` = 11 WHERE `ID` = 1"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO `Change_Log` (
                `ID`, `Field`, `From_Value`, `To_Value`, `From_String`,
                `To_String`, `Change_Type`, `Creation_Date`, `Author_ID`, `Issue_ID`
            ) VALUES (
                1, 'assignee', '10', '11', '10', '11', 'PEOPLE',
                '2018-03-01 09:00:00', 10, 1
            )
            """
        )
    frame = report(tawos_engine, tmp_path)
    row = frame.iloc[0]

    assert row["total_tickets"] == 17
    assert row["resolved_tickets"] == 17
    assert row["assigned_tickets"] == 17
    assert row["distinct_assignees"] == 2
    assert row["pre_cutoff_tickets"] == 15
    assert row["post_cutoff_tickets"] == 2
    assert row["pre_cutoff_resolved_tickets"] == 15
    assert row["people_with_min_pre_cutoff_resolved"] == 1
    assert row["plausible_heldout_briefs"] == 1
    assert row["tickets_with_comments"] == 1
    assert row["assignee_coverage_pct"] == 100.0

    markdown = (tmp_path / "slice_report_mysql.md").read_text()
    assert "comments are measured but are never substituted" in markdown
    assert (tmp_path / "slice_report_mysql.csv").exists()


def test_report_excludes_latest_resolution_clear_from_roster(tawos_engine, tmp_path):
    with tawos_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO `Change_Log` (
                `ID`, `Field`, `From_Value`, `To_Value`, `From_String`,
                `To_String`, `Change_Type`, `Creation_Date`, `Author_ID`, `Issue_ID`
            ) VALUES (
                1, 'resolution', '1', NULL, 'Fixed', NULL, 'OTHER',
                '2018-03-01 09:00:00', 10, 1
            )
            """
        )

    row = report(tawos_engine, tmp_path).iloc[0]

    assert row["pre_cutoff_resolved_tickets"] == 14
    assert row["people_with_min_pre_cutoff_resolved"] == 0
    assert row["plausible_heldout_briefs"] == 0


def test_markup_cleanup_and_description_truncation():
    raw = "<p>h2. See [the docs|https://example.test] {code}x = 1{code}</p>"
    assert strip_markup(raw, 14) == "See the docs x"


def test_bucket_populates_project_domain(stage0_settings):
    tickets = pd.DataFrame(
        [
            {
                "source_issue_id": str(index),
                "key": f"PROJ-{index}",
                "project_key": "PROJ",
                "person_id": "PROJ:10",
                "person_name": "Person PROJ-10",
                "evidence_person_id": "PROJ:10",
                "evidence_person_name": "Person PROJ-10",
                "type": "Bug",
                "summary": "Fix it",
                "summary_provenance": "snapshot_no_recorded_change",
                "description": None,
                "description_provenance": "empty_snapshot_no_recorded_change",
                "components": ["core"],
                "components_provenance": "snapshot_no_recorded_change",
                "labels": [],
                "resolution": "Fixed",
                "snapshot_resolved_at": datetime(2018, 3, 10),
                "resolved_at": datetime(2018, 3, 10),
                "resolved_at_provenance": "snapshot_no_recorded_resolution_change",
                "created_at": datetime(2018, 3, 1),
                "query_time_source": "created_at",
                "temporal_exclusion_reason": None,
            }
            for index in range(3)
        ]
    )

    buckets = build_buckets(tickets)
    assert len(buckets) == 1
    assert buckets[0].project_domain == "distributed systems"
