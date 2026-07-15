"""Focused tests for the deterministic, leakage-safe benchmark foundation."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from capgraph.eval import holdout as holdout_module
from capgraph.eval.holdout import (
    MANIFEST_VERSION,
    BenchmarkManifestEntry,
    build_manifest,
    contains_leakage,
    filter_history_as_of,
    freeze_eligible_roster,
    roster_identifiers,
    write_manifest,
)
from capgraph.eval.run_eval import (
    BenchmarkQueryContext,
    RankingOutput,
    adapt_text_ranker,
    candidate_recall,
    evaluate,
    hit_at_k,
    mrr,
    recall_at_k,
    write_results,
)
from capgraph.models import Contribution, SkillRef
from capgraph.pipeline.stage1_bucket import build_buckets, retained_profile_person_ids
from capgraph.pipeline.stage4_project import build_capabilities
from capgraph.settings import settings


def _ticket(
    key: str,
    person_id: object,
    *,
    project: str = "ALPHA",
    created: str | None,
    resolved: str | None,
    summary: str = "Implement a substantial distributed systems improvement",
    description: str | None = "Detailed work on replication and failure recovery.",
) -> dict:
    missing_person = person_id is None or bool(pd.isna(person_id))
    person_text = None if missing_person else str(person_id)
    suffix = "unknown" if missing_person else person_text.split(":")[-1]
    return {
        "source_issue_id": key,
        "key": key,
        "project_key": project,
        "person_id": person_id,
        "person_name": f"Person {project}-{suffix}" if not missing_person else pd.NA,
        "evidence_person_id": person_id,
        "evidence_person_name": (
            f"Person {project}-{suffix}" if not missing_person else pd.NA
        ),
        "type": "Task",
        "summary": summary,
        "summary_provenance": "snapshot_no_recorded_change",
        "description": description,
        "description_provenance": "snapshot_no_recorded_change",
        "components": [],
        "components_provenance": "snapshot_no_recorded_change",
        "labels": [],
        "resolution": "Fixed" if resolved else None,
        "snapshot_resolved_at": pd.Timestamp(resolved) if resolved else pd.NaT,
        "created_at": pd.Timestamp(created) if created else pd.NaT,
        "resolved_at": pd.Timestamp(resolved) if resolved else pd.NaT,
        "resolved_at_provenance": (
            "snapshot_no_recorded_resolution_change" if resolved else "missing_resolution"
        ),
        "query_time_source": "created_at",
        "temporal_exclusion_reason": None,
    }


def _history(person_id: str, project: str, n: int = 3) -> list[dict]:
    return [
        _ticket(
            f"{project}-H{i}",
            person_id,
            project=project,
            created=f"2018-01-{i + 1:02d}",
            resolved=f"2018-02-{i + 1:02d}",
        )
        for i in range(n)
    ]


def test_manifest_uses_creation_time_and_strips_roster_leakage() -> None:
    rows = _history("ALPHA:1", "ALPHA")
    rows.append(
        _ticket(
            "ALPHA-NEW",
            "ALPHA:1",
            created="2019-02-03",
            resolved="2021-08-09",
            summary="Ask ALPHA:1 and Person ALPHA-1 to own replication",
            description=(
                "Contact alice@example.com, @reviewer, [~legacy.user], or "
                "[user:account-42] for the migration plan."
            ),
        )
    )
    tickets = pd.DataFrame(rows)

    entries = build_manifest(
        tickets,
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_brief_chars=10,
        n_briefs=1,
        seed=17,
        validation_fraction=0,
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.manifest_version == MANIFEST_VERSION
    assert entry.query_time_source == "created_at"
    assert entry.as_of_time == datetime(2019, 2, 3)
    assert entry.as_of_time != entry.resolved_at
    assert entry.eligible_roster == ["ALPHA:1"]
    assert entry.truth_person_ids == ["ALPHA:1"]
    assert entry.split == "test"
    identifiers = roster_identifiers(tickets)
    assert not contains_leakage(entry.query_text, identifiers)
    assert "ALPHA:1" not in entry.query_text
    assert "Person ALPHA-1" not in entry.query_text
    assert "alice@example.com" not in entry.query_text
    assert "@reviewer" not in entry.query_text
    assert "[~legacy.user]" not in entry.query_text
    assert "[user:account-42]" not in entry.query_text


def test_history_and_roster_are_strictly_pre_query_and_pre_cutoff() -> None:
    tickets = pd.DataFrame(
        [
            _ticket(
                "ALPHA-1",
                "ALPHA:1",
                created="2018-01-01",
                resolved="2018-02-01",
            ),
            _ticket(
                "ALPHA-2",
                "ALPHA:1",
                created="2018-03-01",
                resolved="2019-01-01",  # equal to cutoff: not prior
            ),
            _ticket(
                "ALPHA-3",
                "ALPHA:2",
                created="2018-01-01",
                resolved="2018-02-01",
            ),
            _ticket(
                "ALPHA-4",
                "ALPHA:2",
                created="2018-02-02",
                resolved="2018-03-01",
            ),
            _ticket(
                "ALPHA-5",
                "ALPHA:2",
                created="2018-03-02",
                resolved="2019-06-01",  # created early, resolved after query/cutoff
            ),
        ]
    )
    history = filter_history_as_of(
        tickets, query_time="2020-01-01", cutoff="2019-01-01"
    )
    assert set(history["key"]) == {"ALPHA-1", "ALPHA-3", "ALPHA-4"}
    assert history["person_id"].isna().all()
    assert set(history["evidence_person_id"]) == {"ALPHA:1", "ALPHA:2"}
    assert freeze_eligible_roster(
        tickets,
        query_time="2020-01-01",
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_profile_bucket_tickets=2,
    ) == ["ALPHA:2"]


def test_selected_truth_and_roster_ids_have_retained_profiles(monkeypatch) -> None:
    rows = _history("ALPHA:1", "ALPHA", n=3)
    sparse_dates = (
        ("2018-01-05", "2018-02-05"),
        ("2018-04-05", "2018-05-05"),
        ("2018-07-05", "2018-08-05"),
    )
    rows.extend(
        _ticket(
            f"ALPHA-SPARSE-{index}",
            "ALPHA:2",
            created=created,
            resolved=resolved,
        )
        for index, (created, resolved) in enumerate(sparse_dates)
    )
    rows.extend(
        [
            _ticket(
                "ALPHA-QUALIFIED-NEW",
                "ALPHA:1",
                created="2019-02-01",
                resolved="2019-03-01",
            ),
            _ticket(
                "ALPHA-SPARSE-NEW",
                "ALPHA:2",
                created="2019-02-02",
                resolved="2019-03-02",
            ),
        ]
    )
    tickets = pd.DataFrame(rows)
    people = pd.DataFrame(
        [{"person_id": "ALPHA:1", "person_name": "Person ALPHA-1"}]
    )
    monkeypatch.setitem(
        settings._cfg["dataset"],
        "project_domains",
        {"ALPHA": "distributed systems"},
    )
    retained_ids = retained_profile_person_ids(
        tickets,
        cutoff="2019-01-01",
        min_tickets_per_bucket=3,
        max_tickets_per_bucket=30,
    )

    entries = build_manifest(
        tickets,
        people,
        cutoff="2019-01-01",
        min_resolved_tickets=3,
        min_brief_chars=10,
        n_briefs=10,
        validation_fraction=0,
        min_profile_bucket_tickets=3,
        max_profile_bucket_tickets=30,
    )
    buckets = build_buckets(tickets, eligible_person_ids={"ALPHA:1"})
    bucket_person_ids = {bucket.person_id for bucket in buckets}

    assert retained_ids == {"ALPHA:1"}
    assert bucket_person_ids == retained_ids
    selected = [entry for entry in entries if entry.split != "excluded"]
    assert [entry.issue_id for entry in selected] == ["ALPHA-QUALIFIED-NEW"]
    reasons = {entry.issue_id: entry.exclusion_reason for entry in entries}
    assert reasons["ALPHA-SPARSE-NEW"] == "truth_not_eligible"
    for entry in selected:
        assert set(entry.eligible_roster) <= bucket_person_ids
        assert set(entry.truth_person_ids) <= bucket_person_ids


def test_manifest_rejects_stale_stage0_people_roster() -> None:
    rows = _history("ALPHA:1", "ALPHA", n=3)
    rows.append(
        _ticket(
            "ALPHA-NEW",
            "ALPHA:1",
            created="2019-02-01",
            resolved="2019-03-01",
        )
    )
    stale_people = pd.DataFrame(
        [{"person_id": "ALPHA:999", "person_name": "Person ALPHA-999"}]
    )

    with pytest.raises(ValueError, match="people roster is stale or inconsistent"):
        build_manifest(
            pd.DataFrame(rows),
            stale_people,
            cutoff="2019-01-01",
            min_resolved_tickets=3,
            min_brief_chars=10,
            n_briefs=1,
            min_profile_bucket_tickets=3,
            max_profile_bucket_tickets=30,
        )


def test_build_briefs_requires_stage0_people_roster(monkeypatch, tmp_path) -> None:
    (tmp_path / "parquet").mkdir()
    monkeypatch.setattr(holdout_module, "DATA_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="missing Stage 0 people roster"):
        holdout_module.build_briefs()


def test_manifest_is_order_independent_seeded_and_project_stratified(tmp_path) -> None:
    rows = _history("ALPHA:1", "ALPHA") + _history("BETA:2", "BETA")
    for project, person_id in (("ALPHA", "ALPHA:1"), ("BETA", "BETA:2")):
        rows.extend(
            _ticket(
                f"{project}-{index}",
                person_id,
                project=project,
                created=f"2019-02-{index + 1:02d}",
                resolved=f"2019-03-{index + 1:02d}",
            )
            for index in range(6)
        )
    tied = [row for row in rows if row["source_issue_id"] in {"ALPHA-0", "ALPHA-1"}]
    for row in tied:
        row["key"] = "ALPHA-DUPLICATE-KEY"
        row["created_at"] = pd.Timestamp("2019-02-01")
        row["resolved_at"] = pd.Timestamp("2019-03-01")
    tickets = pd.DataFrame(rows)
    kwargs = {
        "cutoff": "2019-01-01",
        "min_resolved_tickets": 2,
        "min_brief_chars": 10,
        "n_briefs": 4,
        "seed": 1234,
        "validation_fraction": 0.5,
    }

    first = build_manifest(tickets, **kwargs)
    second = build_manifest(tickets.sample(frac=1, random_state=99), **kwargs)

    assert [entry.model_dump(mode="json") for entry in first] == [
        entry.model_dump(mode="json") for entry in second
    ]
    selected = [entry for entry in first if entry.split != "excluded"]
    assert len(selected) == 4
    assert {entry.project_key for entry in selected} == {"ALPHA", "BETA"}
    assert {
        entry.project_key: entry.eligible_roster for entry in selected
    } == {"ALPHA": ["ALPHA:1"], "BETA": ["BETA:2"]}
    assert {entry.split for entry in selected} == {"validation", "test"}
    assert sum(entry.exclusion_reason == "sampled_out" for entry in first) == 8

    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    write_manifest(
        first,
        manifest_path=first_path,
        briefs_path=tmp_path / "first-briefs.jsonl",
    )
    write_manifest(
        second,
        manifest_path=second_path,
        briefs_path=tmp_path / "second-briefs.jsonl",
    )
    assert first_path.read_bytes() == second_path.read_bytes()


def test_manifest_keeps_auditable_exclusions() -> None:
    rows = _history("ALPHA:1", "ALPHA")
    rows.extend(
        [
            _ticket(
                "ALPHA-OLD",
                "ALPHA:1",
                created="2018-12-01",
                resolved="2019-02-01",
            ),
            _ticket(
                "ALPHA-OPEN",
                "ALPHA:1",
                created="2019-02-01",
                resolved=None,
            ),
            _ticket(
                "ALPHA-SHORT",
                "ALPHA:1",
                created="2019-02-02",
                resolved="2019-02-03",
                summary="tiny",
                description=None,
            ),
        ]
    )
    entries = build_manifest(
        pd.DataFrame(rows),
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_brief_chars=20,
        n_briefs=10,
        seed=7,
    )
    reasons = {entry.issue_id: entry.exclusion_reason for entry in entries}
    assert reasons == {
        "ALPHA-OLD": "query_not_post_cutoff",
        "ALPHA-OPEN": "unresolved_at_manifest_build",
        "ALPHA-SHORT": "brief_too_short",
    }
    assert all(entry.split == "excluded" for entry in entries)


def test_nullable_unassigned_and_ineligible_people_are_excluded_cleanly() -> None:
    rows = _history("ALPHA:1", "ALPHA")
    rows.extend(
        [
            _ticket(
                "ALPHA-UNASSIGNED",
                pd.NA,
                created="2019-02-01",
                resolved="2019-02-03",
            ),
            _ticket(
                "ALPHA-INELIGIBLE",
                "ALPHA:999",
                created="2019-02-02",
                resolved="2019-02-04",
            ),
        ]
    )
    entries = build_manifest(
        pd.DataFrame(rows),
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_brief_chars=10,
        n_briefs=10,
    )
    reasons = {entry.issue_id: entry.exclusion_reason for entry in entries}
    assert reasons == {
        "ALPHA-INELIGIBLE": "truth_not_eligible",
        "ALPHA-UNASSIGNED": "missing_truth_assignee",
    }
    assert all("<NA>" not in entry.truth_person_ids for entry in entries)


def test_manifest_uses_resolution_owner_for_roster_and_truth() -> None:
    rows = _history("ALPHA:99", "ALPHA")
    for row in rows:
        row["evidence_person_id"] = "ALPHA:1"
        row["evidence_person_name"] = "Person ALPHA-1"
    heldout = _ticket(
        "ALPHA-NEW",
        "ALPHA:99",
        created="2019-02-01",
        resolved="2019-03-01",
    )
    heldout["evidence_person_id"] = "ALPHA:1"
    heldout["evidence_person_name"] = "Person ALPHA-1"
    rows.append(heldout)

    entries = build_manifest(
        pd.DataFrame(rows),
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_brief_chars=10,
        n_briefs=1,
        validation_fraction=0,
    )

    assert entries[0].eligible_roster == ["ALPHA:1"]
    assert entries[0].truth_person_ids == ["ALPHA:1"]
    assert entries[0].split == "test"


def test_manifest_rejects_explicitly_unsafe_text_provenance() -> None:
    rows = _history("ALPHA:1", "ALPHA")
    heldout = _ticket(
        "ALPHA-NEW",
        "ALPHA:1",
        created="2019-02-01",
        resolved="2019-03-01",
    )
    heldout["summary_provenance"] = "final_snapshot_after_recorded_change"
    heldout["description_provenance"] = "snapshot_no_recorded_change"
    rows.append(heldout)

    entries = build_manifest(
        pd.DataFrame(rows),
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_brief_chars=10,
        n_briefs=1,
    )

    assert entries[0].exclusion_reason == "unsafe_query_text_provenance"


def test_manifest_rejects_legacy_snapshot_without_temporal_provenance() -> None:
    tickets = pd.DataFrame(_history("ALPHA:1", "ALPHA")).drop(
        columns=["summary_provenance"]
    )

    with pytest.raises(ValueError, match="hardened Stage 0 columns"):
        build_manifest(
            tickets,
            cutoff="2019-01-01",
            min_resolved_tickets=2,
            min_brief_chars=10,
            n_briefs=1,
        )


def test_manifest_records_stage0_temporal_exclusion() -> None:
    rows = _history("ALPHA:1", "ALPHA")
    heldout = _ticket(
        "ALPHA-NEW",
        "ALPHA:1",
        created="2019-02-01",
        resolved="2019-03-01",
    )
    heldout["temporal_exclusion_reason"] = "project_or_key_changed"
    heldout["evidence_person_id"] = pd.NA
    rows.append(heldout)

    entries = build_manifest(
        pd.DataFrame(rows),
        cutoff="2019-01-01",
        min_resolved_tickets=2,
        min_brief_chars=10,
        n_briefs=1,
    )

    assert entries[0].issue_id == "ALPHA-NEW"
    assert entries[0].issue_key == "ALPHA-NEW"
    assert entries[0].exclusion_reason == "stage0_temporal:project_or_key_changed"


def test_hit_recall_mrr_and_candidate_recall_are_distinct() -> None:
    ranked = ["x", "a", "a"]
    truth = {"a", "b"}
    assert hit_at_k(ranked, truth, 1) == 0.0
    assert hit_at_k(ranked, truth, 5) == 1.0
    assert recall_at_k(ranked, truth, 5) == 0.5
    assert candidate_recall(["a", "b", "z"], truth) == 1.0
    assert mrr(ranked, truth) == 0.5


def test_evaluate_enforces_frozen_roster_and_groups_by_project() -> None:
    cases = [
        BenchmarkManifestEntry(
            seed=1,
            issue_id="ALPHA-1",
            query_text="alpha query",
            as_of_time=datetime(2020, 1, 1),
            project_key="ALPHA",
            eligible_roster=["a", "b"],
            truth_person_ids=["a"],
            split="test",
        ),
        BenchmarkManifestEntry(
            seed=1,
            issue_id="BETA-1",
            query_text="beta query",
            as_of_time=datetime(2020, 1, 1),
            project_key="BETA",
            eligible_roster=["b"],
            truth_person_ids=["b"],
            split="test",
        ),
    ]

    seen_contexts: list[BenchmarkQueryContext] = []

    def rank(context: BenchmarkQueryContext) -> RankingOutput:
        seen_contexts.append(context)
        assert context.as_of_time == datetime(2020, 1, 1)
        if context.query_text.startswith("alpha"):
            return RankingOutput(
                ranked_ids=["a"],
                candidate_ids=["a", "b"],
                latency_ms=12.5,
                cost_usd=0.01,
            )
        return RankingOutput(
            ranked_ids=["b"],
            candidate_ids=["b"],
            latency_ms=7.5,
            cost_usd=0.0,
        )

    result = evaluate("fixture", rank, cases)
    assert result.hit_at_1 == 1.0
    assert result.hit_at_5 == 1.0
    assert result.mrr == 1.0
    assert result.candidate_recall == 1.0
    assert result.latency_ms_mean == 10.0
    assert result.cost_usd_total == 0.01
    assert set(result.per_project) == {"ALPHA", "BETA"}
    assert {context.project_key for context in seen_contexts} == {"ALPHA", "BETA"}
    assert all(not hasattr(context, "truth_person_ids") for context in seen_contexts)


def test_evaluate_rejects_ids_outside_frozen_roster() -> None:
    case = BenchmarkManifestEntry(
        seed=1,
        issue_id="ALPHA-1",
        query_text="alpha query",
        as_of_time=datetime(2020, 1, 1),
        project_key="ALPHA",
        eligible_roster=["a"],
        truth_person_ids=["a"],
        split="test",
    )

    with pytest.raises(ValueError, match="outside frozen roster"):
        evaluate("unsafe", lambda _: ["future-person", "a"], [case])


def test_text_ranker_requires_adapter_and_missing_candidate_recall_is_na(
    tmp_path,
) -> None:
    case = BenchmarkManifestEntry(
        seed=1,
        issue_id="ALPHA-1",
        query_text="alpha query",
        as_of_time=datetime(2020, 1, 1),
        project_key="ALPHA",
        eligible_roster=["a"],
        truth_person_ids=["a"],
        split="test",
    )
    def text_ranker(text: str) -> list[str]:
        return ["a"] if text == "alpha query" else []

    result = evaluate("legacy-frozen", adapt_text_ranker(text_ranker), [case])
    assert result.hit_at_1 == 1.0
    assert result.candidate_recall is None

    path = tmp_path / "results.md"
    write_results([result], path)
    assert "| N/A |" in path.read_text()


def test_capabilities_reject_non_pre_snapshot_contributions() -> None:
    prior = Contribution(
        contribution_id="ALPHA:1|ALPHA|2018-Q4|0",
        person_id="ALPHA:1",
        project_key="ALPHA",
        period="2018-Q4",
        contribution_summary="Replication work",
        specializations=[],
        skills=[SkillRef(name="replication")],
        confidence="high",
        reason="Resolved historical work",
        evidence_ticket_keys=["ALPHA-1"],
    )
    capabilities = build_capabilities([prior], as_of=date(2019, 1, 1))
    assert capabilities[0].last_used == date(2018, 12, 31)
    assert capabilities[0].decay_score < 1.0

    overlapping = prior.model_copy(
        update={"contribution_id": "ALPHA:1|ALPHA|2019-Q1|0", "period": "2019-Q1"}
    )
    with pytest.raises(ValueError, match="not wholly before"):
        build_capabilities([overlapping], as_of=date(2019, 2, 1))
