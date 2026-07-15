"""Temporal and audit-completeness checks for the real TAWOS Change_Log shape."""
from __future__ import annotations

import pandas as pd

from capgraph.pipeline.stage0_load import export

pytest_plugins = ("test_stage0",)


def test_export_reconstructs_creation_state_and_resolution_owner(
    tawos_engine, tmp_path
):
    with tawos_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            UPDATE `Issue`
               SET `Title` = 'LEAKED final title',
                   `Description` = 'LEAKED final description',
                   `Description_Text` = 'LEAKED final description'
             WHERE `ID` = 1
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO `Issue` (
                `ID`, `Issue_Key`, `Title`, `Description`, `Description_Text`,
                `Type`, `Resolution`, `Creation_Date`, `Resolution_Date`,
                `Assignee_ID`, `Project_ID`
            ) VALUES (
                102, 'PROJ-102', 'LEAKED title added later', NULL, NULL,
                'Task', 'Fixed', '2020-05-01 09:00:00',
                '2020-05-02 09:00:00', NULL, 1
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO `Change_Log` (
                `ID`, `Field`, `From_Value`, `To_Value`, `From_String`,
                `To_String`, `Change_Type`, `Creation_Date`, `Author_ID`, `Issue_ID`
            ) VALUES
                (1, 'summary', NULL, NULL, 'Initial safe title',
                    'LEAKED final title', 'DESCRIPTION',
                    '2018-01-15 09:00:00', 10, 1),
                (2, 'description', NULL, NULL, 'h2. Initial safe description',
                    'LEAKED final description', 'DESCRIPTION',
                    '2018-01-15 09:01:00', 10, 1),
                (3, 'Component', NULL, NULL, 'api', 'core', 'OTHER',
                    '2018-01-16 09:00:00', 10, 1),
                (4, 'assignee', '11', '10', '11', '10', 'PEOPLE',
                    '2018-03-01 09:00:00', 10, 1),
                (5, 'summary', NULL, NULL, NULL, 'LEAKED title added later',
                    'DESCRIPTION', '2020-05-01 10:00:00', 10, 102),
                (6, 'project', '99', '1', 'OLD', 'PROJ', 'OTHER',
                    '2019-01-01 00:00:00', 10, 101),
                (7, 'Key', NULL, NULL, 'OLD-101', 'PROJ-101', 'OTHER',
                    '2019-01-01 00:00:01', 10, 101),
                (8, 'resolutiondate', NULL, NULL, '2020-01-15 00:00:00',
                    '2020-02-01 09:00:00', 'OTHER',
                    '2020-02-02 09:00:00', 10, 100),
                (9, 'resolution', '1', NULL, 'Fixed', NULL, 'OTHER',
                    '2020-05-03 09:00:00', 10, 102)
            """
        )

    tickets, people, _ = export(tawos_engine, tmp_path)

    # The full configured-project issue population remains auditable: neither an
    # unassigned row nor an assignee outside the frozen eligible roster is dropped.
    assert len(tickets) == 18
    assert tickets.loc[tickets["key"].eq("PROJ-102"), "person_id"].iloc[0] is None
    assert "PROJ:11" in set(tickets["person_id"].dropna())
    assert tickets["source_issue_id"].is_unique

    changed = tickets.loc[tickets["key"].eq("PROJ-1")].iloc[0]
    assert changed["summary"] == "Initial safe title"
    assert changed["description"] == "Initial safe description"
    assert changed["summary_provenance"] == "change_log_from_string"
    assert changed["description_provenance"] == "change_log_from_string"
    assert changed["components"] == []
    assert changed["components_provenance"] == "omitted_due_to_component_change_log"

    # Final assignee remains audit-only, while profile evidence and benchmark truth
    # belong to the assignee recorded immediately before the later mutation.
    assert changed["person_id"] == "PROJ:10"
    assert changed["evidence_person_id"] == "PROJ:11"
    assert changed["assigned_at"] == pd.Timestamp("2018-03-01 09:00:00")
    assert changed["query_time_source"] == "created_at"

    initially_blank = tickets.loc[tickets["key"].eq("PROJ-102")].iloc[0]
    assert initially_blank["summary"] == ""
    assert initially_blank["summary_provenance"] == "change_log_initially_empty"
    assert initially_blank["temporal_exclusion_reason"] == (
        "latest_resolution_transition_cleared"
    )
    assert pd.isna(initially_blank["resolved_at"])

    moved = tickets.loc[tickets["key"].eq("PROJ-101")].iloc[0]
    assert moved["person_id"] == "PROJ:11"  # retained for source audit
    assert moved["evidence_person_id"] is None
    assert moved["temporal_exclusion_reason"] == "project_or_key_changed"

    changed_resolution_date = tickets.loc[tickets["key"].eq("PROJ-100")].iloc[0]
    assert changed_resolution_date["snapshot_resolved_at"] == pd.Timestamp(
        "2020-02-01 09:00:00"
    )
    assert pd.isna(changed_resolution_date["resolved_at"])
    assert changed_resolution_date["evidence_person_id"] is None
    assert changed_resolution_date["temporal_exclusion_reason"] == "resolution_date_changed"

    # Reassigning one of the final assignee's 15 historical rows after resolution
    # leaves that person with only 14 safely-owned rows, so nobody qualifies.
    assert people.empty
