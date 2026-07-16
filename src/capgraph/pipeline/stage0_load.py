"""Stage 0: normalize TAWOS v1.1 into parquet files.

The SQL in this module follows the official v1.1 schema.  In particular, TAWOS's
``User`` table contains only ``ID`` and ``Project_ID``.  It does not contain a
username or a cross-project identity.  Stage 0 therefore uses project-qualified
IDs (``<project_key>:<user_id>``) and explicit pseudonyms
(``Person <project_key>-<user_id>``).

Outputs:
  tickets.parquet  - every issue in the configured projects (audit complete)
  people.parquet   - the pre-cutoff eligible roster
  projects.parquet - configured projects and their configured domains

Comments deliberately are *not* used as a description fallback.  Comment creation
usually occurs after issue creation, so silently folding comments into ``description``
would make that post-query evidence available to temporal benchmark builders.  The
report measures comment coverage, but the Ticket contract contains only issue text.
Creation-time title/description are reconstructed from ``Change_Log`` when an edit
exists.  Current components are omitted when component changes make that snapshot
unsafe. Final assignee/project/key and raw snapshot resolution time are retained
only as explicitly-provenanced audit data.
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from bs4 import BeautifulSoup
from sqlalchemy import bindparam, create_engine, text

from ..models import Ticket
from ..settings import DATA_DIR, settings
from .stage1_bucket import historical_profile_rows, profile_eligible_person_ids

PARQUET_DIR = DATA_DIR / "parquet"


# Keeping the queries as module constants makes it straightforward to check them
# against the upstream schema and to exercise them with a schema-faithful fixture.
ISSUES_SQL = text(
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
    JOIN `Project` AS p
      ON p.`ID` = i.`Project_ID`
    LEFT JOIN `Repository` AS r
      ON r.`ID` = p.`Repository_ID`
    LEFT JOIN `User` AS u
      ON u.`ID` = i.`Assignee_ID`
     AND u.`Project_ID` = p.`ID`
    WHERE p.`Project_Key` IN :project_keys
    ORDER BY p.`Project_Key`, i.`ID`
    """
).bindparams(bindparam("project_keys", expanding=True))


CHANGE_LOG_SQL = text(
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
    JOIN `Issue` AS i
      ON i.`ID` = cl.`Issue_ID`
    JOIN `Project` AS p
      ON p.`ID` = i.`Project_ID`
    WHERE p.`Project_Key` IN :project_keys
      AND LOWER(TRIM(cl.`Field`)) IN (
          'summary', 'description', 'assignee', 'component', 'components',
          'project', 'key', 'resolution', 'resolutiondate'
      )
    ORDER BY cl.`Issue_ID`, cl.`Creation_Date`, cl.`ID`
    """
).bindparams(bindparam("project_keys", expanding=True))


PROJECT_USERS_SQL = text(
    """
    SELECT
        p.`Project_Key` AS project_key,
        u.`ID` AS user_id
    FROM `User` AS u
    JOIN `Project` AS p
      ON p.`ID` = u.`Project_ID`
    WHERE p.`Project_Key` IN :project_keys
    ORDER BY p.`Project_Key`, u.`ID`
    """
).bindparams(bindparam("project_keys", expanding=True))


COMPONENTS_SQL = text(
    """
    SELECT
        ic.`Issue_ID` AS issue_id,
        c.`Name` AS component_name
    FROM `Issue_Component` AS ic
    JOIN `Component` AS c
      ON c.`ID` = ic.`Component_ID`
    JOIN `Issue` AS i
      ON i.`ID` = ic.`Issue_ID`
    JOIN `Project` AS p
      ON p.`ID` = i.`Project_ID`
     AND p.`ID` = c.`Project_ID`
    WHERE p.`Project_Key` IN :project_keys
    """
).bindparams(bindparam("project_keys", expanding=True))


PROJECTS_SQL = text(
    """
    SELECT
        p.`Project_Key` AS project_key,
        p.`Name` AS name,
        r.`Name` AS repository
    FROM `Project` AS p
    LEFT JOIN `Repository` AS r
      ON r.`ID` = p.`Repository_ID`
    WHERE p.`Project_Key` IN :project_keys
    ORDER BY p.`Project_Key`
    """
).bindparams(bindparam("project_keys", expanding=True))


# The CTE names make the time semantics explicit:
# - eligible is frozen using only issues created and resolved before cutoff;
# - plausible briefs are issues *created* after the cutoff, not issues that merely
#   happened to resolve after it.
REPORT_SQL = text(
    """
    WITH issue_boundary AS (
        SELECT
            i.`ID` AS issue_id,
            CASE
                WHEN i.`Resolution_Date` IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) IN (
                          'project', 'key', 'resolutiondate'
                      )
                ) THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'resolution'
                      AND cl.`Creation_Date` IS NULL
                ) THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'resolution'
                      AND cl.`Creation_Date` IS NOT NULL
                ) AND (
                    SELECT COALESCE(
                        NULLIF(TRIM(cl.`To_Value`), ''),
                        NULLIF(TRIM(cl.`To_String`), '')
                    )
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'resolution'
                      AND cl.`Creation_Date` IS NOT NULL
                    ORDER BY cl.`Creation_Date` DESC, cl.`ID` DESC
                    LIMIT 1
                ) IS NULL THEN NULL
                WHEN COALESCE((
                    SELECT MAX(cl.`Creation_Date`)
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'resolution'
                ), i.`Resolution_Date`) > i.`Resolution_Date`
                THEN (
                    SELECT MAX(cl.`Creation_Date`)
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'resolution'
                )
                ELSE i.`Resolution_Date`
            END AS evidence_resolved_at
        FROM `Issue` AS i
    ),
    resolved_owner AS (
        SELECT
            i.`ID` AS issue_id,
            i.`Project_ID` AS project_id,
            b.evidence_resolved_at,
            CASE
                WHEN b.evidence_resolved_at IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'assignee'
                      AND cl.`Creation_Date` IS NULL
                ) THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'assignee'
                      AND cl.`Creation_Date` <= b.evidence_resolved_at
                ) THEN (
                    SELECT COALESCE(
                        NULLIF(TRIM(cl.`To_Value`), ''),
                        NULLIF(TRIM(cl.`To_String`), '')
                    )
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'assignee'
                      AND cl.`Creation_Date` <= b.evidence_resolved_at
                    ORDER BY cl.`Creation_Date` DESC, cl.`ID` DESC
                    LIMIT 1
                )
                WHEN EXISTS (
                    SELECT 1
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'assignee'
                      AND cl.`Creation_Date` > b.evidence_resolved_at
                ) THEN (
                    SELECT COALESCE(
                        NULLIF(TRIM(cl.`From_Value`), ''),
                        NULLIF(TRIM(cl.`From_String`), '')
                    )
                    FROM `Change_Log` AS cl
                    WHERE cl.`Issue_ID` = i.`ID`
                      AND LOWER(TRIM(cl.`Field`)) = 'assignee'
                      AND cl.`Creation_Date` > b.evidence_resolved_at
                    ORDER BY cl.`Creation_Date`, cl.`ID`
                    LIMIT 1
                )
                ELSE CAST(i.`Assignee_ID` AS CHAR)
            END AS assignee_id
        FROM `Issue` AS i
        JOIN issue_boundary AS b
          ON b.issue_id = i.`ID`
    ),
    eligible AS (
        SELECT
            i.`Project_ID`,
            ro.assignee_id,
            COUNT(*) AS pre_cutoff_resolved_count
        FROM `Issue` AS i
        JOIN resolved_owner AS ro
          ON ro.issue_id = i.`ID`
        JOIN `User` AS u
          ON CAST(u.`ID` AS CHAR) = ro.assignee_id
         AND u.`Project_ID` = i.`Project_ID`
        WHERE ro.evidence_resolved_at IS NOT NULL
          AND ro.evidence_resolved_at < :cutoff
          AND i.`Creation_Date` IS NOT NULL
          AND i.`Creation_Date` < :cutoff
        GROUP BY i.`Project_ID`, ro.assignee_id
        HAVING COUNT(*) >= :min_tickets
    ),
    commented_issue AS (
        SELECT DISTINCT `Issue_ID`
        FROM `Comment`
        WHERE `Issue_ID` IS NOT NULL
    )
    SELECT
        p.`Project_Key` AS project_key,
        p.`Name` AS project_name,
        r.`Name` AS repository,
        COUNT(i.`ID`) AS total_tickets,
        SUM(CASE WHEN i.`Resolution_Date` IS NOT NULL THEN 1 ELSE 0 END)
            AS resolved_tickets,
        SUM(CASE WHEN u.`ID` IS NOT NULL THEN 1 ELSE 0 END)
            AS assigned_tickets,
        SUM(CASE WHEN NULLIF(TRIM(i.`Title`), '') IS NOT NULL THEN 1 ELSE 0 END)
            AS tickets_with_summary,
        SUM(CASE WHEN COALESCE(
                NULLIF(TRIM(i.`Description_Text`), ''),
                NULLIF(TRIM(i.`Description`), '')
            ) IS NOT NULL THEN 1 ELSE 0 END)
            AS tickets_with_description,
        COUNT(ci.`Issue_ID`) AS tickets_with_comments,
        COUNT(DISTINCT u.`ID`) AS distinct_assignees,
        MIN(i.`Creation_Date`) AS first_created_at,
        MAX(i.`Creation_Date`) AS last_created_at,
        SUM(CASE WHEN i.`Creation_Date` < :cutoff THEN 1 ELSE 0 END)
            AS pre_cutoff_tickets,
        SUM(CASE WHEN i.`Creation_Date` >= :cutoff THEN 1 ELSE 0 END)
            AS post_cutoff_tickets,
        SUM(CASE
            WHEN i.`Creation_Date` < :cutoff
             AND ro.evidence_resolved_at < :cutoff
            THEN 1 ELSE 0
        END)
            AS pre_cutoff_resolved_tickets,
        COUNT(DISTINCT e.assignee_id) AS people_with_min_pre_cutoff_resolved,
        SUM(CASE
            WHEN i.`Creation_Date` >= :cutoff
             AND ro.evidence_resolved_at IS NOT NULL
             AND e.assignee_id IS NOT NULL
             AND (
                 CHAR_LENGTH(COALESCE(NULLIF(TRIM(i.`Title`), ''), ''))
                 + CHAR_LENGTH(COALESCE(
                     NULLIF(TRIM(i.`Description_Text`), ''),
                     NULLIF(TRIM(i.`Description`), ''),
                     ''
                 ))
             ) >= :min_brief_chars
            THEN 1 ELSE 0
        END) AS plausible_heldout_briefs
    FROM `Project` AS p
    LEFT JOIN `Repository` AS r
      ON r.`ID` = p.`Repository_ID`
    LEFT JOIN `Issue` AS i
      ON i.`Project_ID` = p.`ID`
    LEFT JOIN `User` AS u
      ON u.`ID` = i.`Assignee_ID`
     AND u.`Project_ID` = p.`ID`
    LEFT JOIN resolved_owner AS ro
      ON ro.issue_id = i.`ID`
    LEFT JOIN eligible AS e
      ON e.`Project_ID` = p.`ID`
     AND e.assignee_id = ro.assignee_id
    LEFT JOIN commented_issue AS ci
      ON ci.`Issue_ID` = i.`ID`
    GROUP BY p.`ID`, p.`Project_Key`, p.`Name`, r.`Name`
    ORDER BY p.`Project_Key`
    """
)


COUNT_COLUMNS = [
    "total_tickets",
    "resolved_tickets",
    "assigned_tickets",
    "tickets_with_summary",
    "tickets_with_description",
    "tickets_with_comments",
    "distinct_assignees",
    "pre_cutoff_tickets",
    "post_cutoff_tickets",
    "pre_cutoff_resolved_tickets",
    "people_with_min_pre_cutoff_resolved",
    "plausible_heldout_briefs",
]


def get_engine():
    if not settings.mysql_url:
        raise SystemExit("Set MYSQL_URL in .env (see .env.example)")
    return create_engine(settings.mysql_url)


def introspect() -> None:
    eng = get_engine()
    with eng.connect() as conn:
        tables = [r[0] for r in conn.execute(text("SHOW TABLES"))]
        print("Tables:", tables)
        for table_name in tables:
            # Table names cannot be SQL parameters.  Restrict this interpolation to
            # names returned by SHOW TABLES and quote embedded backticks defensively.
            quoted_name = str(table_name).replace("`", "``")
            cols = conn.execute(text(f"DESCRIBE `{quoted_name}`")).fetchall()
            print(f"\n{table_name}:")
            for col in cols:
                print(f"  {col[0]:<30} {col[1]}")


def _project_keys() -> list[str]:
    """Read project keys while tolerating the original and mapping-shaped configs."""
    configured = settings["dataset.projects"]
    if isinstance(configured, Mapping):
        keys = [str(key) for key in configured]
    else:
        keys = []
        for item in configured:
            if isinstance(item, Mapping):
                key = item.get("key") or item.get("project_key")
                if key:
                    keys.append(str(key))
            else:
                keys.append(str(item))
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise ValueError("dataset.projects must contain at least one project key")
    return keys


def _project_domains() -> dict[str, str]:
    configured = settings.get("dataset.project_domains", {})
    if not isinstance(configured, Mapping):
        raise TypeError("dataset.project_domains must be a mapping of project key to domain")
    return {str(key): str(value) for key, value in configured.items() if value is not None}


def _add_report_percentages(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in COUNT_COLUMNS:
        if column not in frame:
            frame[column] = 0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype("int64")

    denominator = frame["total_tickets"].where(frame["total_tickets"] > 0)
    for output, numerator in [
        ("resolved_coverage_pct", "resolved_tickets"),
        ("assignee_coverage_pct", "assigned_tickets"),
        ("summary_coverage_pct", "tickets_with_summary"),
        ("description_coverage_pct", "tickets_with_description"),
        ("comment_coverage_pct", "tickets_with_comments"),
    ]:
        frame[output] = (100 * frame[numerator] / denominator).fillna(0).round(2)

    domains = _project_domains()
    frame["project_domain"] = frame["project_key"].map(domains).fillna("")
    return frame


def _markdown_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        value = value.isoformat()
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_table(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = [
        "| " + " | ".join(_markdown_value(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])


def _write_report(frame: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Keep the restored-database cross-check separate from the canonical streaming
    # archive report; their extra comment-coverage columns intentionally differ.
    csv_path = output_dir / "slice_report_mysql.csv"
    markdown_path = output_dir / "slice_report_mysql.md"
    frame.to_csv(csv_path, index=False)

    cutoff = settings["dataset.holdout_cutoff"]
    min_tickets = settings["dataset.min_tickets_per_person"]
    min_chars = settings.get("eval.min_brief_chars", 300)
    recommendation_text = ", ".join(_project_keys())
    markdown = f"""# TAWOS v1.1 project slice report

Generated from the restored official schema. The cutoff is `{cutoff}`.

Definitions:

- `pre_cutoff_tickets` / `post_cutoff_tickets` use issue creation time.
- `pre_cutoff_resolved_tickets` uses resolution time and is the evidence pool.
- eligible people have at least {min_tickets} resolved tickets before the cutoff.
- plausible held-out briefs were created after the cutoff, eventually resolved,
  have an eligible assignee, and contain at least {min_chars} issue-text characters.
- comments are measured but are never substituted into benchmark query text.

Configured recommendation selected after density and domain-diversity review:
{recommendation_text}.

{_markdown_table(frame)}
"""
    markdown_path.write_text(markdown)
    return csv_path, markdown_path


def report(engine=None, output_dir: Path = PARQUET_DIR) -> pd.DataFrame:
    """Build and persist the complete per-project slice report."""
    eng = engine or get_engine()
    params = {
        "cutoff": pd.Timestamp(settings["dataset.holdout_cutoff"]).to_pydatetime(),
        "min_tickets": int(settings["dataset.min_tickets_per_person"]),
        "min_brief_chars": int(settings.get("eval.min_brief_chars", 300)),
    }
    frame = pd.read_sql(REPORT_SQL, eng, params=params)
    frame = _add_report_percentages(frame)
    csv_path, markdown_path = _write_report(frame, Path(output_dir))
    print(frame.to_string(index=False))
    print(f"\nReport -> {markdown_path} (machine-readable: {csv_path})")
    return frame


def strip_markup(text_: str | None, max_chars: int) -> str | None:
    """Strip common Jira wiki markup and HTML, collapse whitespace, and truncate."""
    if text_ is None or not str(text_).strip():
        return None
    cleaned = BeautifulSoup(str(text_), "html.parser").get_text(" ")
    cleaned = re.sub(r"(?m)^h[1-6]\.\s*", "", cleaned)
    cleaned = re.sub(r"\{[^}\n]{0,80}\}", " ", cleaned)  # macro delimiters
    cleaned = re.sub(r"\[([^]|]+)\|[^]]+\]", r"\1", cleaned)  # [label|URL]
    cleaned = re.sub(r"!([^!\n]+)!", " ", cleaned)  # Jira image syntax
    cleaned = cleaned.replace("{{", "").replace("}}", "")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars] or None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _stable_id(value: Any) -> str | None:
    """Normalize integer-like database identifiers without manufacturing null IDs."""
    if _is_missing(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    candidate = str(value).strip()
    return candidate or None


def _clean_description(description_text: Any, description_raw: Any, max_chars: int) -> str | None:
    """Prefer upstream plain text, then raw issue text; never use comments."""
    for candidate in (description_text, description_raw):
        if candidate is None or bool(pd.isna(candidate)):
            continue
        cleaned = strip_markup(str(candidate), max_chars)
        if cleaned:
            return cleaned
    return None


def _change_log_map(frame: pd.DataFrame) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Index actual-schema Change_Log rows by issue and normalized field name."""
    indexed: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if frame.empty:
        return indexed

    work = frame.copy()
    work["field_name"] = work["field_name"].fillna("").astype(str).str.strip().str.casefold()
    work["changed_at"] = pd.to_datetime(work["changed_at"], errors="coerce")
    work = work.sort_values(
        ["issue_id", "changed_at", "change_id"], kind="stable", na_position="last"
    )
    value_columns = (
        "change_id",
        "from_value",
        "to_value",
        "from_string",
        "to_string",
        "changed_at",
    )
    for row in work.itertuples(index=False):
        issue_id = _stable_id(row.issue_id)
        field_name = str(row.field_name)
        if issue_id is None or not field_name:
            continue
        change = {column: getattr(row, column) for column in value_columns}
        indexed.setdefault(issue_id, {}).setdefault(field_name, []).append(change)
    return indexed


def _project_user_ids(frame: pd.DataFrame) -> dict[str, set[str]]:
    users: dict[str, set[str]] = {}
    for row in frame.itertuples(index=False):
        user_id = _stable_id(row.user_id)
        if user_id is not None:
            users.setdefault(str(row.project_key), set()).add(user_id)
    return users


def _change_side_value(change: Mapping[str, Any], side: str) -> tuple[Any, str]:
    """Read text with human-readable String preferred over opaque Value."""
    for suffix in ("string", "value"):
        column = f"{side}_{suffix}"
        value = change.get(column)
        if not _is_missing(value):
            return value, column
    return None, "null"


def _creation_text(
    changes: list[dict[str, Any]],
    *,
    snapshot_values: Sequence[Any],
    max_chars: int,
) -> tuple[str | None, str]:
    """Return the pre-first-edit text, or the unchanged final snapshot.

    An undated edit makes ordering unknowable, so the mutable snapshot is omitted.
    A recorded SQL NULL/empty ``From_*`` is meaningful: the field was empty before
    its first edit and must stay empty in a creation-time brief.
    """
    if changes:
        if any(_is_missing(change.get("changed_at")) for change in changes):
            return None, "omitted_undated_change_log"
        value, source = _change_side_value(changes[0], "from")
        if source == "null":
            return None, "change_log_initially_empty"
        cleaned = strip_markup(str(value), max_chars)
        if cleaned is None:
            return None, f"change_log_{source}_empty"
        return cleaned, f"change_log_{source}"

    for value in snapshot_values:
        if _is_missing(value):
            continue
        cleaned = strip_markup(str(value), max_chars)
        if cleaned:
            return cleaned, "snapshot_no_recorded_change"
    return None, "empty_snapshot_no_recorded_change"


def _transition_user_id(
    change: Mapping[str, Any],
    side: str,
    valid_user_ids: set[str],
) -> str | None:
    """Resolve an assignee transition to a project-local official User row."""
    # Jira's assignee From_Value/To_Value normally stores the numeric User.ID;
    # String is a conservative fallback for the occasional equivalent export.
    for suffix in ("value", "string"):
        user_id = _stable_id(change.get(f"{side}_{suffix}"))
        if user_id is not None and user_id in valid_user_ids:
            return user_id
    return None


def _evidence_assignee(
    changes: list[dict[str, Any]],
    *,
    resolved_at: Any,
    final_user_id: str | None,
    valid_user_ids: set[str],
) -> tuple[str | None, str]:
    """Reconstruct profile ownership at resolution, never from a later mutation."""
    if _is_missing(resolved_at):
        return None, "missing_resolution"
    if not changes:
        return final_user_id, "final_snapshot_no_recorded_assignee_change"
    if any(_is_missing(change.get("changed_at")) for change in changes):
        return None, "omitted_undated_assignee_change_log"

    boundary = pd.Timestamp(resolved_at)
    at_or_before = [
        change for change in changes if pd.Timestamp(change["changed_at"]) <= boundary
    ]
    if at_or_before:
        return (
            _transition_user_id(at_or_before[-1], "to", valid_user_ids),
            "change_log_to_at_resolution",
        )

    after = [change for change in changes if pd.Timestamp(change["changed_at"]) > boundary]
    if after:
        return (
            _transition_user_id(after[0], "from", valid_user_ids),
            "change_log_from_after_resolution",
        )
    return final_user_id, "final_snapshot_after_empty_assignee_change_log"


def _final_assignment_metadata(
    changes: list[dict[str, Any]],
    *,
    final_user_id: str | None,
    valid_user_ids: set[str],
) -> tuple[pd.Timestamp | None, str]:
    """Provenance for final outcome assignee without claiming it existed at creation."""
    if final_user_id is None:
        return None, "unassigned_final_snapshot"
    if not changes:
        return None, "final_snapshot_assignment_time_unknown"
    if any(_is_missing(change.get("changed_at")) for change in changes):
        return None, "final_snapshot_with_undated_assignee_change"
    for change in reversed(changes):
        if _transition_user_id(change, "to", valid_user_ids) == final_user_id:
            return pd.Timestamp(change["changed_at"]), "final_assignment_from_change_log"
    return None, "final_snapshot_not_reconciled_with_change_log"


def _safe_resolution_boundary(
    snapshot_resolved_at: Any,
    resolution_changes: list[dict[str, Any]],
    resolution_date_changes: list[dict[str, Any]],
) -> tuple[pd.Timestamp | None, str, str | None]:
    """Return an evidence-available resolution boundary or fail closed."""
    if resolution_date_changes:
        return None, "omitted_resolutiondate_change_log", "resolution_date_changed"
    if any(_is_missing(change.get("changed_at")) for change in resolution_changes):
        return None, "omitted_undated_resolution_change_log", "undated_resolution_change"
    if _is_missing(snapshot_resolved_at):
        return None, "missing_resolution", None

    boundary = pd.Timestamp(snapshot_resolved_at)
    if resolution_changes:
        latest_change = resolution_changes[-1]
        latest_sets_resolution = any(
            not _is_missing(latest_change.get(column))
            and bool(str(latest_change[column]).strip())
            for column in ("to_value", "to_string")
        )
        if not latest_sets_resolution:
            return (
                None,
                "omitted_latest_resolution_clear",
                "latest_resolution_transition_cleared",
            )
        latest_transition = max(
            pd.Timestamp(change["changed_at"]) for change in resolution_changes
        )
        boundary = max(boundary, latest_transition)
        return boundary, "resolution_change_log_boundary", None
    return boundary, "snapshot_no_recorded_resolution_change", None


def _component_map(frame: pd.DataFrame) -> dict[Any, list[str]]:
    if frame.empty:
        return {}
    usable = frame.dropna(subset=["component_name"])
    return {
        str(issue_id): sorted({str(value).strip() for value in values if str(value).strip()})
        for issue_id, values in usable.groupby("issue_id")["component_name"]
    }


def _normalise_issue_rows(
    issue_rows: pd.DataFrame,
    component_rows: pd.DataFrame,
    change_rows: pd.DataFrame,
    project_users: pd.DataFrame,
) -> pd.DataFrame:
    max_chars = int(settings["bucketing.max_description_chars"])
    components = _component_map(component_rows)
    changes_by_issue = _change_log_map(change_rows)
    users_by_project = _project_user_ids(project_users)
    records: list[dict[str, Any]] = []
    for row in issue_rows.itertuples(index=False):
        project_key = str(row.project_key)
        valid_user_ids = users_by_project.get(project_key, set())
        user_id = _stable_id(row.user_id)
        issue_id = _stable_id(row.issue_id)
        issue_key = _stable_id(row.issue_key) or f"missing-key:{issue_id or 'unknown'}"
        field_changes = changes_by_issue.get(issue_id or "", {})
        moved_issue = bool(field_changes.get("project") or field_changes.get("key"))
        resolved_at, resolved_at_provenance, resolution_exclusion = (
            _safe_resolution_boundary(
                row.resolved_at,
                field_changes.get("resolution", []),
                field_changes.get("resolutiondate", []),
            )
        )
        temporal_exclusions = []
        if moved_issue:
            temporal_exclusions.append("project_or_key_changed")
        if resolution_exclusion:
            temporal_exclusions.append(resolution_exclusion)
        temporal_exclusion_reason = ";".join(temporal_exclusions) or None

        summary, summary_provenance = _creation_text(
            field_changes.get("summary", []),
            snapshot_values=(row.issue_title,),
            max_chars=2_000,
        )
        description, description_provenance = _creation_text(
            field_changes.get("description", []),
            snapshot_values=(row.description_text, row.description_raw),
            max_chars=max_chars,
        )
        assignee_changes = field_changes.get("assignee", [])
        evidence_user_id, evidence_provenance = _evidence_assignee(
            assignee_changes,
            resolved_at=resolved_at,
            final_user_id=user_id,
            valid_user_ids=valid_user_ids,
        )
        if temporal_exclusion_reason is not None:
            evidence_user_id = None
            evidence_provenance = f"omitted_{temporal_exclusion_reason}"
        assigned_at, final_assignee_provenance = _final_assignment_metadata(
            assignee_changes,
            final_user_id=user_id,
            valid_user_ids=valid_user_ids,
        )
        component_changed = bool(
            field_changes.get("component") or field_changes.get("components")
        )
        issue_components = [] if component_changed else components.get(issue_id or "", [])
        records.append(
            {
                "source_issue_id": issue_id or "",
                "key": issue_key,
                "project_key": project_key,
                "person_id": f"{project_key}:{user_id}" if user_id is not None else None,
                "person_name": (
                    f"Person {project_key}-{user_id}" if user_id is not None else None
                ),
                "evidence_person_id": (
                    f"{project_key}:{evidence_user_id}"
                    if evidence_user_id is not None
                    else None
                ),
                "evidence_person_name": (
                    f"Person {project_key}-{evidence_user_id}"
                    if evidence_user_id is not None
                    else None
                ),
                "type": None if pd.isna(row.issue_type) else str(row.issue_type),
                "summary": summary or "",
                "summary_provenance": summary_provenance,
                "description": description,
                "description_provenance": description_provenance,
                "components": issue_components,
                "components_provenance": (
                    "omitted_due_to_component_change_log"
                    if component_changed
                    else "snapshot_no_recorded_change"
                ),
                "labels": [],  # TAWOS v1.1 has no labels table/column.
                "resolution": None if pd.isna(row.resolution) else str(row.resolution),
                "snapshot_resolved_at": pd.to_datetime(
                    row.resolved_at, errors="coerce"
                ),
                "resolved_at": resolved_at,
                "resolved_at_provenance": resolved_at_provenance,
                "created_at": pd.to_datetime(row.created_at, errors="coerce"),
                "query_time_source": "created_at",
                "temporal_exclusion_reason": temporal_exclusion_reason,
                "assigned_at": assigned_at,
                "assignee_provenance": (
                    f"{final_assignee_provenance};evidence={evidence_provenance}"
                ),
            }
        )
    frame = pd.DataFrame.from_records(records, columns=list(Ticket.model_fields))
    # Pandas 3 may infer nullable string columns and expose missing values as NaN.
    # Preserve Python None in the in-memory stage contract as well as parquet nulls.
    for column in (
        "person_id",
        "person_name",
        "evidence_person_id",
        "evidence_person_name",
        "type",
        "description",
        "resolution",
        "temporal_exclusion_reason",
    ):
        frame[column] = frame[column].astype(object)
        frame.loc[frame[column].isna(), column] = None
    return frame


def _eligible_person_ids(tickets: pd.DataFrame) -> set[str]:
    """Freeze people who meet activity and retained-profile requirements."""
    return profile_eligible_person_ids(
        tickets,
        cutoff=settings["dataset.holdout_cutoff"],
        min_resolved_tickets=int(settings["dataset.min_tickets_per_person"]),
        min_tickets_per_bucket=int(settings["bucketing.min_tickets_per_bucket"]),
        max_tickets_per_bucket=int(settings["bucketing.max_tickets_per_bucket"]),
    )


def _people_frame(tickets: pd.DataFrame, eligible_ids: set[str]) -> pd.DataFrame:
    required = {"evidence_person_id", "evidence_person_name"}
    missing = required.difference(tickets.columns)
    if missing:
        raise ValueError(f"tickets are missing hardened ownership columns: {sorted(missing)}")
    pre_cutoff = historical_profile_rows(
        tickets, cutoff=settings["dataset.holdout_cutoff"]
    )
    ownership_column = "evidence_person_id"
    ownership_name_column = "evidence_person_name"
    pre_cutoff = pre_cutoff[
        pre_cutoff[ownership_column].notna()
        & pre_cutoff[ownership_column].astype(str).isin(eligible_ids)
    ]
    rows = []
    for person_id, group in pre_cutoff.groupby(ownership_column, sort=True):
        rows.append(
            {
                "person_id": str(person_id),
                "person_name": str(group[ownership_name_column].iloc[0]),
                "project_keys": sorted(group["project_key"].astype(str).unique().tolist()),
                # Retain the original contract name, but define it conservatively as
                # pre-cutoff resolved count.  The explicit alias removes ambiguity.
                "ticket_count": int(len(group)),
                "pre_cutoff_resolved_ticket_count": int(len(group)),
            }
        )
    return pd.DataFrame.from_records(
        rows,
        columns=[
            "person_id",
            "person_name",
            "project_keys",
            "ticket_count",
            "pre_cutoff_resolved_ticket_count",
        ],
    )


def _projects_frame(raw_projects: pd.DataFrame) -> pd.DataFrame:
    domains = _project_domains()
    projects = raw_projects.copy()
    projects["project_key"] = projects["project_key"].astype(str)
    projects["domain"] = projects["project_key"].map(domains).fillna("")
    return projects[["project_key", "name", "domain", "repository"]].sort_values(
        "project_key", kind="stable"
    )


def _validate_ticket_sample(tickets: pd.DataFrame, sample_size: int = 25) -> None:
    if tickets.empty:
        return
    positions = sorted(set(range(min(sample_size, len(tickets)))) | {len(tickets) - 1})
    for position in positions:
        record = tickets.iloc[position].to_dict()
        for nullable in (
            "person_id",
            "person_name",
            "evidence_person_id",
            "evidence_person_name",
            "type",
            "description",
            "resolution",
            "temporal_exclusion_reason",
            "snapshot_resolved_at",
            "resolved_at",
            "created_at",
            "assigned_at",
        ):
            if pd.isna(record[nullable]):
                record[nullable] = None
        Ticket.model_validate(record)


def _write_parquet(
    frame: pd.DataFrame,
    path: Path,
    *,
    string_list_columns: Sequence[str] = (),
) -> None:
    """Write stable Arrow list types even when every observed list is empty."""
    table = pa.Table.from_pandas(frame, preserve_index=False)
    for column in string_list_columns:
        position = table.schema.get_field_index(column)
        if position < 0:
            raise ValueError(f"missing configured list column: {column}")
        values = pa.array(frame[column].tolist(), type=pa.list_(pa.string()))
        table = table.set_column(position, column, values)
    pq.write_table(table, path)


def export(
    engine=None,
    output_dir: Path = PARQUET_DIR,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Export every configured-project issue plus the frozen eligible roster."""
    eng = engine or get_engine()
    project_keys = _project_keys()
    params: Mapping[str, Sequence[str]] = {"project_keys": project_keys}
    issue_rows = pd.read_sql(ISSUES_SQL, eng, params=params)
    component_rows = pd.read_sql(COMPONENTS_SQL, eng, params=params)
    change_rows = pd.read_sql(CHANGE_LOG_SQL, eng, params=params)
    project_users = pd.read_sql(PROJECT_USERS_SQL, eng, params=params)
    raw_projects = pd.read_sql(PROJECTS_SQL, eng, params=params)

    tickets = _normalise_issue_rows(
        issue_rows, component_rows, change_rows, project_users
    )
    eligible_ids = _eligible_person_ids(tickets)
    _validate_ticket_sample(tickets)

    people = _people_frame(tickets, eligible_ids)
    projects = _projects_frame(raw_projects)

    missing_domains = projects.loc[projects["domain"].eq(""), "project_key"].tolist()
    if missing_domains:
        raise ValueError(
            "missing non-empty dataset.project_domains entries for: "
            + ", ".join(missing_domains)
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet(
        tickets,
        output_dir / "tickets.parquet",
        string_list_columns=("components", "labels"),
    )
    _write_parquet(
        people,
        output_dir / "people.parquet",
        string_list_columns=("project_keys",),
    )
    _write_parquet(projects, output_dir / "projects.parquet")

    print(
        f"Wrote {len(tickets):,} audit-complete tickets and {len(people):,} eligible people "
        f"across {len(projects):,} projects -> {output_dir}"
    )
    return tickets, people, projects


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--introspect", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.introspect:
        introspect()
    elif args.report:
        report()
    else:
        export()
