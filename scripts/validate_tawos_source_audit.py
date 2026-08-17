#!/usr/bin/env python3
"""Run a deterministic ten-row TAWOS source-to-Parquet acceptance audit.

The command intentionally prints category-level results only. Raw issue text,
source issue IDs, and user IDs remain local to the validation process.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text

from capgraph.models import Ticket
from capgraph.pipeline.stage0_load import (
    PROJECT_USERS_SQL,
    _normalise_issue_rows,
    _project_keys,
    get_engine,
)
from capgraph.settings import DATA_DIR, settings

ISSUES_BY_ID_SQL = text(
    """
    SELECT
        i.`ID` AS issue_id,
        i.`Issue_Key` AS issue_key,
        p.`Project_Key` AS project_key,
        p.`Name` AS project_name,
        r.`Name` AS repository,
        u.`ID` AS user_id,
        i.`Type` AS issue_type,
        i.`Title` AS issue_title,
        i.`Description_Text` AS description_text,
        i.`Description` AS description_raw,
        i.`Resolution` AS resolution,
        i.`Resolution_Date` AS resolved_at,
        i.`Creation_Date` AS created_at
    FROM `Issue` AS i
    JOIN `Project` AS p ON p.`ID` = i.`Project_ID`
    LEFT JOIN `Repository` AS r ON r.`ID` = p.`Repository_ID`
    LEFT JOIN `User` AS u
      ON u.`ID` = i.`Assignee_ID` AND u.`Project_ID` = p.`ID`
    WHERE i.`ID` IN :issue_ids
    ORDER BY p.`Project_Key`, i.`ID`
    """
).bindparams(bindparam("issue_ids", expanding=True))

CHANGES_BY_ID_SQL = text(
    """
    SELECT
        cl.`ID` AS change_id,
        cl.`Issue_ID` AS issue_id,
        cl.`Field` AS field_name,
        cl.`From_Value` AS from_value,
        cl.`To_Value` AS to_value,
        cl.`From_String` AS from_string,
        cl.`To_String` AS to_string,
        cl.`Creation_Date` AS changed_at
    FROM `Change_Log` AS cl
    WHERE cl.`Issue_ID` IN :issue_ids
      AND LOWER(TRIM(cl.`Field`)) IN (
          'summary', 'description', 'assignee', 'component', 'components',
          'project', 'key', 'resolution', 'resolutiondate'
      )
    ORDER BY cl.`Issue_ID`, cl.`Creation_Date`, cl.`ID`
    """
).bindparams(bindparam("issue_ids", expanding=True))

COMPONENTS_BY_ID_SQL = text(
    """
    SELECT ic.`Issue_ID` AS issue_id, c.`Name` AS component_name
    FROM `Issue_Component` AS ic
    JOIN `Component` AS c ON c.`ID` = ic.`Component_ID`
    JOIN `Issue` AS i ON i.`ID` = ic.`Issue_ID`
    JOIN `Project` AS p
      ON p.`ID` = i.`Project_ID` AND p.`ID` = c.`Project_ID`
    WHERE ic.`Issue_ID` IN :issue_ids
    """
).bindparams(bindparam("issue_ids", expanding=True))

RERESOLUTION_CANDIDATES_SQL = text(
    """
    SELECT cl.`Issue_ID` AS issue_id
    FROM `Change_Log` AS cl
    JOIN `Issue` AS i ON i.`ID` = cl.`Issue_ID`
    JOIN `Project` AS p ON p.`ID` = i.`Project_ID`
    WHERE p.`Project_Key` IN :project_keys
      AND LOWER(TRIM(cl.`Field`)) = 'resolution'
      AND cl.`Creation_Date` IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM `Change_Log` AS unsafe
          WHERE unsafe.`Issue_ID` = cl.`Issue_ID`
            AND LOWER(TRIM(unsafe.`Field`)) IN (
                'project', 'key', 'resolutiondate'
            )
      )
    GROUP BY cl.`Issue_ID`
    HAVING COUNT(*) >= 2
       AND SUM(CASE
           WHEN COALESCE(
               NULLIF(TRIM(cl.`To_Value`), ''),
               NULLIF(TRIM(cl.`To_String`), '')
           ) IS NULL THEN 1 ELSE 0 END
       ) >= 1
    ORDER BY cl.`Issue_ID`
    """
).bindparams(bindparam("project_keys", expanding=True))

AUDIT_CATEGORIES = (
    "ordinary_pre_cutoff",
    "ordinary_post_cutoff",
    "creation_summary_edit",
    "creation_description_edit",
    "assignee_transition_before_resolution",
    "assignee_transition_after_resolution",
    "component_mutation",
    "project_or_key_move",
    "resolution_date_mutation",
    "resolution_clear_then_reresolution",
)


def _issue_order(values: Iterable[Any]) -> list[str]:
    return sorted((str(value) for value in values), key=lambda value: int(value))


def _choose(
    tickets: pd.DataFrame,
    mask: pd.Series,
    used: set[str],
    category: str,
) -> str:
    candidates = _issue_order(tickets.loc[mask, "source_issue_id"])
    try:
        issue_id = next(candidate for candidate in candidates if candidate not in used)
    except StopIteration as exc:
        raise RuntimeError(f"no distinct source row found for {category}") from exc
    used.add(issue_id)
    return issue_id


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _has_value(change: pd.Series, side: str) -> bool:
    return any(
        not _is_missing(change[column]) and bool(str(change[column]).strip())
        for column in (f"{side}_value", f"{side}_string")
    )


def _ticket_payload(row: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in Ticket.model_fields:
        value = row[field]
        if field in {"components", "labels"}:
            if value is None:
                payload[field] = []
            elif hasattr(value, "tolist"):
                payload[field] = value.tolist()
            else:
                payload[field] = list(value)
        elif _is_missing(value):
            payload[field] = None
        elif isinstance(value, pd.Timestamp):
            payload[field] = value.to_pydatetime()
        else:
            payload[field] = value
    return Ticket.model_validate(payload).model_dump(mode="json")


def _select_rows(tickets: pd.DataFrame, engine) -> dict[str, str]:
    cutoff = pd.Timestamp(settings["dataset.holdout_cutoff"])
    created = pd.to_datetime(tickets["created_at"], errors="coerce")
    safe = tickets["temporal_exclusion_reason"].isna()
    safe_resolution = tickets["resolved_at"].notna()
    ordinary = (
        safe
        & safe_resolution
        & tickets["summary_provenance"].eq("snapshot_no_recorded_change")
        & tickets["description_provenance"].isin(
            {"snapshot_no_recorded_change", "empty_snapshot_no_recorded_change"}
        )
        & tickets["components_provenance"].eq("snapshot_no_recorded_change")
        & tickets["assignee_provenance"].str.contains(
            "evidence=final_snapshot_no_recorded_assignee_change", regex=False
        )
    )
    used: set[str] = set()
    selected = {
        "ordinary_pre_cutoff": _choose(
            tickets, ordinary & created.lt(cutoff), used, "ordinary_pre_cutoff"
        ),
        "ordinary_post_cutoff": _choose(
            tickets, ordinary & created.ge(cutoff), used, "ordinary_post_cutoff"
        ),
        "creation_summary_edit": _choose(
            tickets,
            safe & tickets["summary_provenance"].str.startswith("change_log_from_"),
            used,
            "creation_summary_edit",
        ),
        "creation_description_edit": _choose(
            tickets,
            safe & tickets["description_provenance"].str.startswith("change_log_from_"),
            used,
            "creation_description_edit",
        ),
        "assignee_transition_before_resolution": _choose(
            tickets,
            safe
            & tickets["assignee_provenance"].str.contains(
                "evidence=change_log_to_at_resolution", regex=False
            ),
            used,
            "assignee_transition_before_resolution",
        ),
        "assignee_transition_after_resolution": _choose(
            tickets,
            safe
            & tickets["assignee_provenance"].str.contains(
                "evidence=change_log_from_after_resolution", regex=False
            ),
            used,
            "assignee_transition_after_resolution",
        ),
        "component_mutation": _choose(
            tickets,
            safe
            & tickets["components_provenance"].eq(
                "omitted_due_to_component_change_log"
            ),
            used,
            "component_mutation",
        ),
        "project_or_key_move": _choose(
            tickets,
            tickets["temporal_exclusion_reason"]
            .fillna("")
            .str.contains("project_or_key_changed", regex=False),
            used,
            "project_or_key_move",
        ),
        "resolution_date_mutation": _choose(
            tickets,
            tickets["temporal_exclusion_reason"]
            .fillna("")
            .str.contains("resolution_date_changed", regex=False),
            used,
            "resolution_date_mutation",
        ),
    }

    candidates = pd.read_sql(
        RERESOLUTION_CANDIDATES_SQL,
        engine,
        params={"project_keys": _project_keys()},
    )
    candidate_ids = _issue_order(candidates["issue_id"])
    safe_ids = set(
        tickets.loc[
            safe
            & tickets["resolved_at_provenance"].eq("resolution_change_log_boundary"),
            "source_issue_id",
        ].astype(str)
    )
    try:
        reresolution_id = next(
            issue_id
            for issue_id in candidate_ids
            if issue_id in safe_ids and issue_id not in used
        )
    except StopIteration as exc:
        raise RuntimeError("no safe resolution-clear/re-resolution row found") from exc
    selected["resolution_clear_then_reresolution"] = reresolution_id
    return selected


def _source_frames(engine, issue_ids: list[int]):
    params = {"issue_ids": issue_ids}
    issues = pd.read_sql(ISSUES_BY_ID_SQL, engine, params=params)
    changes = pd.read_sql(CHANGES_BY_ID_SQL, engine, params=params)
    components = pd.read_sql(COMPONENTS_BY_ID_SQL, engine, params=params)
    users = pd.read_sql(
        PROJECT_USERS_SQL,
        engine,
        params={"project_keys": _project_keys()},
    )
    return issues, changes, components, users


def _assert_category_semantics(
    category: str,
    row: pd.Series,
    changes: pd.DataFrame,
) -> None:
    work = changes.copy()
    work["field_name"] = work["field_name"].fillna("").astype(str).str.strip().str.casefold()
    fields = set(work["field_name"])
    resolved_at = pd.Timestamp(row["resolved_at"]) if not _is_missing(row["resolved_at"]) else None

    if category.startswith("ordinary_"):
        assert fields <= {"resolution"}
    elif category == "creation_summary_edit":
        assert "summary" in fields
        assert str(row["summary_provenance"]).startswith("change_log_from_")
    elif category == "creation_description_edit":
        assert "description" in fields
        assert str(row["description_provenance"]).startswith("change_log_from_")
    elif category == "assignee_transition_before_resolution":
        assignee = work.loc[work["field_name"].eq("assignee")]
        assert resolved_at is not None and not assignee.empty
        assert pd.to_datetime(assignee["changed_at"]).le(resolved_at).any()
    elif category == "assignee_transition_after_resolution":
        assignee = work.loc[work["field_name"].eq("assignee")]
        assert resolved_at is not None and not assignee.empty
        assert pd.to_datetime(assignee["changed_at"]).gt(resolved_at).all()
    elif category == "component_mutation":
        assert fields & {"component", "components"}
        assert list(row["components"]) == []
    elif category == "project_or_key_move":
        assert fields & {"project", "key"}
        assert "project_or_key_changed" in str(row["temporal_exclusion_reason"])
        assert _is_missing(row["evidence_person_id"])
    elif category == "resolution_date_mutation":
        assert "resolutiondate" in fields
        assert "resolution_date_changed" in str(row["temporal_exclusion_reason"])
        assert _is_missing(row["resolved_at"])
        assert _is_missing(row["evidence_person_id"])
    elif category == "resolution_clear_then_reresolution":
        resolution = work.loc[work["field_name"].eq("resolution")].sort_values(
            ["changed_at", "change_id"], kind="stable"
        )
        assert len(resolution) >= 2
        assert any(not _has_value(change, "to") for _, change in resolution.iterrows())
        assert _has_value(resolution.iloc[-1], "to")
        assert row["resolved_at_provenance"] == "resolution_change_log_boundary"
    else:  # pragma: no cover - guarded by the fixed category contract
        raise AssertionError(f"unknown audit category: {category}")


def validate(tickets_path: Path) -> None:
    tickets = pd.read_parquet(tickets_path)
    engine = get_engine()
    selected = _select_rows(tickets, engine)
    if tuple(selected) != AUDIT_CATEGORIES:
        raise AssertionError("audit category selection drifted")

    issue_ids = [int(selected[category]) for category in AUDIT_CATEGORIES]
    issues, changes, components, users = _source_frames(engine, issue_ids)
    if len(issues) != len(AUDIT_CATEGORIES):
        raise AssertionError("source lookup did not return exactly ten distinct issues")

    recomputed = _normalise_issue_rows(issues, components, changes, users).set_index(
        "source_issue_id", drop=False
    )
    exported = tickets.set_index("source_issue_id", drop=False)
    passed = 0
    for category in AUDIT_CATEGORIES:
        issue_id = selected[category]
        if issue_id not in recomputed.index or issue_id not in exported.index:
            raise AssertionError(f"{category}: source/export row missing")
        source_row = recomputed.loc[issue_id]
        exported_row = exported.loc[issue_id]
        if _ticket_payload(source_row) != _ticket_payload(exported_row):
            raise AssertionError(f"{category}: normalized source differs from Parquet")
        issue_changes = changes.loc[changes["issue_id"].astype(str).eq(issue_id)]
        _assert_category_semantics(category, exported_row, issue_changes)
        passed += 1

    print(f"TAWOS source audit: {passed}/{len(AUDIT_CATEGORIES)} passed")
    print("Categories: " + ", ".join(AUDIT_CATEGORIES))
    print("No source issue text, issue IDs, or user IDs were emitted.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickets",
        type=Path,
        default=DATA_DIR / "parquet" / "tickets.parquet",
        help="Stage 0 tickets Parquet path",
    )
    args = parser.parse_args()
    validate(args.tickets)


if __name__ == "__main__":
    main()
