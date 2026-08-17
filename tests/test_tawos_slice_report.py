import hashlib
import json
from pathlib import Path

import pytest

from scripts.tawos_slice_report import (
    ArchiveMetadata,
    InsertParser,
    ReportBuilder,
    ReportParameters,
    SqlField,
    build_report,
    field_text,
    load_report_parameters,
    parse_args,
    resolve_report_parameters,
    verify_archive,
    write_csv,
    write_markdown,
    write_metadata,
)


def test_report_defaults_come_from_canonical_settings():
    parameters = load_report_parameters(Path("config/settings.yaml"))

    assert parameters == ReportParameters(
        cutoff="2019-01-01",
        min_tickets=15,
        min_brief_chars=300,
        recommended_projects=("MESOS", "FAB", "TIMOB", "DM", "EVG"),
    )


def test_report_cli_overrides_are_recorded_as_effective_parameters():
    args = parse_args(
        [
            "--cutoff",
            "2020-01-01",
            "--min-tickets",
            "20",
            "--min-brief-chars",
            "400",
        ]
    )

    parameters = resolve_report_parameters(args)

    assert parameters.cutoff == "2020-01-01"
    assert parameters.min_tickets == 20
    assert parameters.min_brief_chars == 400
    assert parameters.recommended_projects == ("MESOS", "FAB", "TIMOB", "DM", "EVG")


def test_archive_verification_checks_size_and_sha256(tmp_path: Path):
    archive = tmp_path / "TAWOS.sql.zip"
    payload = b"pinned archive fixture"
    archive.write_bytes(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    metadata = verify_archive(
        archive,
        expected_size_bytes=len(payload),
        expected_sha256=expected_sha256,
    )

    assert metadata == ArchiveMetadata(archive.name, len(payload), expected_sha256)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_archive(
            archive,
            expected_size_bytes=len(payload),
            expected_sha256="0" * 64,
        )


def test_unverified_archive_cannot_overwrite_canonical_reports(tmp_path: Path):
    archive = tmp_path / "TAWOS.sql.zip"
    archive.write_bytes(b"not the pinned archive")
    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    csv_path = output_dir / "slice_report.csv"
    markdown_path = output_dir / "slice_report.md"
    csv_path.write_text("original csv\n")
    markdown_path.write_text("original markdown\n")

    with pytest.raises(ValueError, match="archive size mismatch"):
        build_report(archive, output_dir, "2019-01-01", 15, 300)

    assert csv_path.read_text() == "original csv\n"
    assert markdown_path.read_text() == "original markdown\n"


def test_report_metadata_records_archive_and_effective_parameters(tmp_path: Path):
    parameters = ReportParameters(
        cutoff="2019-01-01 00:00:00",
        min_tickets=15,
        min_brief_chars=300,
        recommended_projects=("MESOS", "FAB"),
    )
    archive_metadata = ArchiveMetadata("TAWOS.sql.zip", 123, "a" * 64)
    path = tmp_path / "slice_report.metadata.json"

    write_metadata(path, parameters, archive_metadata)

    metadata = json.loads(path.read_text())
    assert metadata["source_archive"] == {
        "filename": "TAWOS.sql.zip",
        "sha256": "a" * 64,
        "size_bytes": 123,
    }
    assert metadata["effective_parameters"] == {
        "cutoff": "2019-01-01 00:00:00",
        "min_brief_chars": 300,
        "min_tickets_per_person": 15,
        "recommended_projects": ["MESOS", "FAB"],
    }


def test_csv_report_uses_canonical_lf_line_endings(tmp_path: Path):
    path = tmp_path / "slice_report.csv"

    write_csv(path, [{"project_key": "ALPHA", "total_tickets": 10}])

    payload = path.read_bytes()
    assert payload == b"project_key,total_tickets\nALPHA,10\n"
    assert b"\r\n" not in payload


def test_markdown_project_prose_uses_effective_parameters(tmp_path: Path):
    row = {
        "project_key": "ALPHA",
        "total_tickets": 10,
        "resolved_tickets": 8,
        "assignee_coverage_pct": "80.00",
        "summary_coverage_pct": "100.00",
        "description_coverage_pct": "90.00",
        "distinct_assignees": 3,
        "first_created_at": "2018-01-01 00:00:00",
        "last_created_at": "2020-01-01 00:00:00",
        "pre_cutoff_created_tickets": 7,
        "post_cutoff_created_tickets": 3,
        "people_meeting_minimum_pre_cutoff_resolved_tickets": 2,
        "plausible_heldout_briefs": 1,
    }
    parameters = ReportParameters(
        cutoff="2019-01-01 00:00:00",
        min_tickets=4,
        min_brief_chars=50,
        recommended_projects=("ALPHA",),
    )
    path = tmp_path / "slice_report.md"

    write_markdown(
        path,
        [row],
        parameters,
        ArchiveMetadata("TAWOS.sql.zip", 123, "a" * 64),
    )

    markdown = path.read_text()
    assert "effective project selection (`ALPHA`) balances domain diversity" in markdown
    assert "MESOS" not in markdown
    assert "150 briefs" not in markdown


def _quoted(value: str | None) -> SqlField:
    if value is None:
        return SqlField(b"NULL", False)
    return SqlField(value.encode(), True)


def _number(value: int | None) -> SqlField:
    return SqlField(b"NULL" if value is None else str(value).encode(), False)


def _issue(
    *,
    issue_id: int,
    title: str,
    description: str | None,
    created: str,
    resolved: str | None,
    assignee: int | None,
    project: int = 1,
) -> list[SqlField]:
    row = [_quoted(None) for _ in range(30)]
    row[0] = _number(issue_id)
    row[2] = _quoted(f"PROJ-{issue_id}")
    row[4] = _quoted(title)
    row[6] = _quoted(description)
    row[8] = _quoted("Task")
    row[11] = _quoted("Fixed" if resolved else None)
    row[12] = _quoted(created)
    row[14] = _quoted(resolved)
    row[27] = _number(assignee)
    row[28] = _number(project)
    return row


def _assignee_change(
    *,
    change_id: int,
    issue_id: int,
    before: int | None,
    after: int | None,
    changed: str,
) -> list[SqlField]:
    return [
        _number(change_id),
        _quoted("assignee"),
        _number(before),
        _number(after),
        _number(before),
        _number(after),
        _quoted("PEOPLE"),
        _quoted(changed),
        _number(after),
        _number(issue_id),
    ]


def _field_change(
    *, change_id: int, issue_id: int, field: str, changed: str
) -> list[SqlField]:
    row = _assignee_change(
        change_id=change_id,
        issue_id=issue_id,
        before=None,
        after=None,
        changed=changed,
    )
    row[1] = _quoted(field)
    return row


def test_insert_parser_handles_mysql_escapes_and_chunk_boundaries():
    rows: list[list[SqlField]] = []
    parser = InsertParser(rows.append)

    consumed, done = parser.feed(b"(1,'line\\nwith ")
    assert consumed == len(b"(1,'line\\nwith ") and not done
    consumed, done = parser.feed(b"quote\\' ok',NULL),(2,'two',3);")

    assert done
    assert consumed == len(b"quote\\' ok',NULL),(2,'two',3);")
    assert field_text(rows[0][1]) == "line\nwith quote' ok"
    assert field_text(rows[0][2]) is None
    assert field_text(rows[1][1]) == "two"


def test_report_stats_use_creation_cutoff_and_pre_cutoff_resolved_roster():
    builder = ReportBuilder(
        cutoff="2019-01-01 00:00:00", min_tickets=1, min_brief_chars=20
    )
    builder.project(
        [
            _number(1),
            _quoted("PROJ"),
            _quoted("Project"),
            _quoted(None),
            _quoted(None),
            _quoted(None),
            _quoted(None),
            _quoted(None),
            _number(9),
        ]
    )
    builder.repository(
        [_number(9), _quoted("Repository"), _quoted(None), _quoted(None)]
    )
    builder.issue(
        _issue(
            issue_id=1,
            title="Prior work",
            description=None,
            created="2018-01-01 00:00:00",
            resolved="2018-02-01 00:00:00",
            assignee=7,
        )
    )
    builder.issue(
        _issue(
            issue_id=2,
            title="Future brief",
            description="Enough benchmark detail for a useful request",
            created="2019-02-01 00:00:00",
            resolved="2019-03-01 00:00:00",
            assignee=7,
        )
    )

    row = builder.rows()[0]
    assert row["total_tickets"] == 2
    assert row["resolved_tickets"] == 2
    assert row["pre_cutoff_created_tickets"] == 1
    assert row["post_cutoff_created_tickets"] == 1
    assert row["people_meeting_minimum_pre_cutoff_resolved_tickets"] == 1
    assert row["plausible_heldout_briefs"] == 1
    assert row["description_coverage_pct"] == "50.00"


def test_report_roster_and_truth_use_assignee_at_resolution():
    builder = ReportBuilder(
        cutoff="2019-01-01 00:00:00", min_tickets=1, min_brief_chars=20
    )
    # Both final snapshots point to 99, but post-resolution changes prove that 7
    # owned each ticket when it was resolved.
    builder.change_log(
        _assignee_change(
            change_id=1,
            issue_id=1,
            before=7,
            after=99,
            changed="2018-03-01 00:00:00",
        )
    )
    builder.change_log(
        _assignee_change(
            change_id=2,
            issue_id=2,
            before=7,
            after=99,
            changed="2019-04-01 00:00:00",
        )
    )
    builder.issue(
        _issue(
            issue_id=1,
            title="Prior work",
            description=None,
            created="2018-01-01 00:00:00",
            resolved="2018-02-01 00:00:00",
            assignee=99,
        )
    )
    builder.issue(
        _issue(
            issue_id=2,
            title="Future brief",
            description="Enough benchmark detail for a useful request",
            created="2019-02-01 00:00:00",
            resolved="2019-03-01 00:00:00",
            assignee=99,
        )
    )

    row = builder.rows()[0]
    assert row["people_meeting_minimum_pre_cutoff_resolved_tickets"] == 1
    assert row["plausible_heldout_briefs"] == 1
    assert not builder.assignee_changes


def test_unresolved_issue_discards_irrelevant_assignee_changes():
    builder = ReportBuilder(
        cutoff="2019-01-01 00:00:00", min_tickets=1, min_brief_chars=20
    )
    builder.change_log(
        _assignee_change(
            change_id=1,
            issue_id=1,
            before=7,
            after=99,
            changed="2018-03-01 00:00:00",
        )
    )
    builder.issue(
        _issue(
            issue_id=1,
            title="Open work",
            description=None,
            created="2018-01-01 00:00:00",
            resolved=None,
            assignee=99,
        )
    )

    assert not builder.assignee_changes


def test_report_excludes_moved_and_late_resolution_boundary_issues():
    builder = ReportBuilder(
        cutoff="2019-01-01 00:00:00", min_tickets=1, min_brief_chars=20
    )
    builder.change_log(
        _field_change(
            change_id=1,
            issue_id=1,
            field="project",
            changed="2018-03-01 00:00:00",
        )
    )
    late_resolution = _field_change(
        change_id=2,
        issue_id=2,
        field="resolution",
        changed="2019-02-01 00:00:00",
    )
    late_resolution[3] = _quoted("1")
    late_resolution[5] = _quoted("Fixed")
    builder.change_log(late_resolution)
    builder.change_log(
        _field_change(
            change_id=3,
            issue_id=3,
            field="resolution",
            changed="2018-03-01 00:00:00",
        )
    )
    for issue_id in (1, 2, 3):
        builder.issue(
            _issue(
                issue_id=issue_id,
                title="Prior work",
                description=None,
                created="2018-01-01 00:00:00",
                resolved="2018-02-01 00:00:00",
                assignee=7,
            )
        )

    row = builder.rows()[0]
    assert row["resolved_tickets"] == 3  # final-snapshot source statistic
    assert row["people_meeting_minimum_pre_cutoff_resolved_tickets"] == 0
