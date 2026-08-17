"""Benchmark v4: work-package manifest, leakage guards, and run plumbing.

Everything here runs on toy data, offline. The rules under test are the ones a
reviewer cannot check by reading a number in a report: that nothing after a package's
as-of time reaches its brief, that undated membership can never claim to be planned,
that truth is multi-person and roster-filtered, that a rewrite is frozen and stale
rewrites are refused, and that rebuilding the manifest is deterministic and free.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from capgraph.eval import packages, rewrite, run_v4
from capgraph.eval.holdout import MANIFEST_VERSION
from capgraph.eval.metrics import query_context
from capgraph.eval.packages import (
    PACKAGE_MANIFEST_VERSION,
    RAW,
    REWRITTEN,
    brief_digest,
    build_package_manifest,
    load_package_manifest,
    manifest_summary,
    write_package_manifest,
)
from capgraph.settings import settings

AS_OF = datetime(2020, 3, 1)
CUTOFF = "2019-01-01"

# Long enough to clear eval.v4.min_brief_chars in the fixtures below.
FILLER = (
    "The scheduler must isolate container resources across the cluster, and the change "
    "touches the allocator, the executor, and the isolation modules that back them. "
)


def _ticket(
    issue_id: str,
    *,
    project: str = "PROJ",
    person: str | None = "PROJ:10",
    created: datetime,
    resolved: datetime | None,
    summary: str = "",
    description: str | None = None,
    exclusion: str | None = None,
    summary_provenance: str = "snapshot_no_recorded_change",
    description_provenance: str = "snapshot_no_recorded_change",
    resolution_provenance: str = "snapshot_no_recorded_resolution_change",
) -> dict:
    return {
        "source_issue_id": issue_id,
        "key": f"{project}-{issue_id}",
        "project_key": project,
        "person_id": person,
        "person_name": None if person is None else f"Person {project}-{person.split(':')[-1]}",
        "evidence_person_id": person,
        "evidence_person_name": (
            None if person is None else f"Person {project}-{person.split(':')[-1]}"
        ),
        "summary": summary or f"Issue {issue_id} summary",
        "summary_provenance": summary_provenance,
        "description": description if description is not None else FILLER,
        "description_provenance": description_provenance,
        "components": [],
        "components_provenance": "snapshot_no_recorded_change",
        "labels": [],
        "snapshot_resolved_at": resolved,
        "created_at": created,
        "query_time_source": "created_at",
        "resolved_at": resolved,
        "resolved_at_provenance": resolution_provenance,
        "temporal_exclusion_reason": exclusion,
    }


def _history(person: str, project: str, n: int, start_id: int) -> list[dict]:
    """Pre-cutoff resolved work, which is what makes a person roster-eligible."""
    return [
        _ticket(
            str(start_id + index),
            project=project,
            person=person,
            created=datetime(2018, 1, 1),
            resolved=datetime(2018, 6, 1),
        )
        for index in range(n)
    ]


@pytest.fixture
def toy_sources():
    """One sprint package with planned, late-joining, and snapshot-only issues."""
    tickets = [
        # --- pre-cutoff history: three eligible people, one ineligible ---
        *_history("PROJ:10", "PROJ", 3, 1000),
        *_history("PROJ:11", "PROJ", 3, 1100),
        *_history("PROJ:12", "PROJ", 3, 1200),
        *_history("PROJ:99", "PROJ", 1, 1300),          # too little history
        # --- brief material: planned before the sprint started ---
        _ticket("1", created=datetime(2020, 1, 5), resolved=datetime(2020, 3, 20),
                summary="Cluster allocator rebalancing"),
        _ticket("2", created=datetime(2020, 1, 6), resolved=datetime(2020, 3, 21),
                summary="Executor isolation follow-up", person="PROJ:11"),
        _ticket("3", created=datetime(2020, 1, 7), resolved=datetime(2020, 3, 22),
                summary="Allocator metrics endpoint", person="PROJ:12"),
        # --- joined the package AFTER it started: truth only, never the brief ---
        _ticket("4", created=datetime(2020, 3, 10), resolved=datetime(2020, 3, 25),
                summary="POSTASOF late scope added mid sprint", person="PROJ:11"),
        # --- known to the final snapshot only, with no dated join: truth only ---
        _ticket("5", created=datetime(2020, 1, 8), resolved=datetime(2020, 3, 26),
                summary="SNAPSHOTONLY membership with unknown timing", person="PROJ:99"),
        # --- resolved before the package started: not the package's work ---
        _ticket("6", created=datetime(2020, 1, 9), resolved=datetime(2020, 2, 1),
                summary="Closed before the sprint began", person="PROJ:10"),
    ]
    sprints = pd.DataFrame(
        [
            {
                "sprint_id": "1", "sprint_jira_id": 500, "project_key": "PROJ",
                "name": "PROJ Sprint 12", "state": "CLOSED", "start_date": AS_OF,
                "end_date": datetime(2020, 3, 15), "activated_date": None,
                "complete_date": datetime(2020, 3, 15),
            }
        ]
    )
    membership = pd.DataFrame(
        [
            {"source_issue_id": "1", "project_key": "PROJ", "sprint_jira_id": 500,
             "added_at": datetime(2020, 2, 20), "provenance": packages.CHANGE_LOG_JOIN},
            {"source_issue_id": "2", "project_key": "PROJ", "sprint_jira_id": 500,
             "added_at": datetime(2020, 2, 21), "provenance": packages.CHANGE_LOG_JOIN},
            {"source_issue_id": "3", "project_key": "PROJ", "sprint_jira_id": 500,
             "added_at": datetime(2020, 2, 22), "provenance": packages.CHANGE_LOG_JOIN},
            {"source_issue_id": "6", "project_key": "PROJ", "sprint_jira_id": 500,
             "added_at": datetime(2020, 2, 23), "provenance": packages.CHANGE_LOG_JOIN},
            {"source_issue_id": "4", "project_key": "PROJ", "sprint_jira_id": 500,
             "added_at": datetime(2020, 3, 10), "provenance": packages.CHANGE_LOG_JOIN},
            {"source_issue_id": "5", "project_key": "PROJ", "sprint_jira_id": 500,
             "added_at": None, "provenance": packages.SNAPSHOT_JOIN},
        ]
    )
    return pd.DataFrame(tickets), sprints, membership


def build(toy_sources, **overrides):
    tickets, sprints, membership = toy_sources
    kwargs = {
        "cutoff": CUTOFF,
        "min_resolved_tickets": 2,
        "min_brief_issues": 2,
        "max_brief_issues": 30,
        "max_brief_chars": 8000,
        "min_brief_chars": 100,
        "min_rewritten_chars": 50,
        "n_packages": 10,
        "seed": 20260814,
        "validation_fraction": 0.0,
        "min_profile_bucket_tickets": 1,
        "max_profile_bucket_tickets": 30,
    }
    kwargs.update(overrides)
    return build_package_manifest(tickets, sprints, membership, **kwargs)


def only(entries):
    assert len(entries) == 1
    return entries[0]


REWRITE = "We need engineers for cluster allocator and executor isolation work."


def rewrites_for(entry, brief: str = REWRITE):
    return {
        entry.package_key: {
            "brief": brief,
            "model": "test-model",
            "prompt_digest": "abc123",
            "input_digest": brief_digest(entry.brief_raw),
        }
    }


# ---------- brief construction and as-of discipline ----------

def test_brief_holds_only_material_planned_and_created_before_the_as_of_time(toy_sources):
    entry = only(build(toy_sources))

    assert entry.as_of_time == AS_OF
    assert entry.query_time_source == "sprint_start"
    assert entry.brief_issue_keys == ["PROJ-1", "PROJ-2", "PROJ-3", "PROJ-6"]
    assert entry.brief_issue_count == 4
    # The three ways an issue can be in the package but out of the brief.
    assert "POSTASOF" not in entry.brief_raw        # joined after the as-of time
    assert "SNAPSHOTONLY" not in entry.brief_raw    # undated final-snapshot membership
    assert "Cluster allocator rebalancing" in entry.brief_raw
    assert entry.package_issue_count == 6


def test_late_and_undated_members_still_count_toward_truth(toy_sources):
    entry = only(build(toy_sources))

    # PROJ:11 worked a late-joining issue, PROJ:99 a snapshot-only one. Both worked the
    # package; only roster eligibility decides whether they are usable truth.
    assert entry.truth_person_count_all == 4       # 10, 11, 12 and the ineligible 99
    assert entry.truth_person_ids == ["PROJ:10", "PROJ:11", "PROJ:12"]
    assert entry.truth_dropped_ineligible == 1
    assert set(entry.truth_person_ids).issubset(entry.eligible_roster)


def test_work_finished_before_the_package_started_is_not_its_truth(toy_sources):
    tickets, sprints, membership = toy_sources
    # PROJ-6 resolved a month before the sprint began and is the only work of PROJ:10
    # inside the package window once the other issues are reassigned.
    tickets = tickets.copy()
    for issue_id in ("1",):
        tickets.loc[tickets["source_issue_id"].eq(issue_id), "evidence_person_id"] = "PROJ:11"

    entry = only(build((tickets, sprints, membership)))

    assert "PROJ:10" not in entry.truth_person_ids
    # Issues 1-5 all resolved at or after the as-of time and carry an owner, so all
    # five contribute a truth candidate; issue 6 closed before the package began.
    assert entry.truth_issue_count == 5


def test_unsafe_provenance_and_temporal_exclusions_never_reach_a_brief(toy_sources):
    tickets, sprints, membership = toy_sources
    tickets = tickets.copy()
    tickets.loc[tickets["source_issue_id"].eq("1"), "summary_provenance"] = "snapshot_edited"
    tickets.loc[tickets["source_issue_id"].eq("2"), "temporal_exclusion_reason"] = (
        "project_or_key_changed"
    )

    entry = only(build((tickets, sprints, membership)))

    assert entry.brief_issue_keys == ["PROJ-3", "PROJ-6"]
    assert "Cluster allocator rebalancing" not in entry.brief_raw
    assert "Executor isolation follow-up" not in entry.brief_raw


def test_identifiers_are_stripped_and_a_leaky_brief_is_excluded(toy_sources):
    tickets, sprints, membership = toy_sources
    tickets = tickets.copy()
    tickets.loc[tickets["source_issue_id"].eq("1"), "description"] = (
        FILLER + "assigned to PROJ:11 (Person PROJ-11), ping them at dev@example.org"
    )

    entry = only(build((tickets, sprints, membership)))

    assert "PROJ:11" not in entry.brief_raw
    assert "Person PROJ-11" not in entry.brief_raw
    assert "dev@example.org" not in entry.brief_raw
    assert "[PERSON]" in entry.brief_raw and "[EMAIL]" in entry.brief_raw


def test_brief_material_is_capped_by_issues_and_characters(toy_sources):
    entry = only(build(toy_sources, max_brief_issues=2))
    assert entry.brief_issue_count == 2
    assert entry.brief_issues_omitted == 2
    assert entry.brief_issue_keys == ["PROJ-1", "PROJ-2"]        # creation order

    entry = only(build(toy_sources, max_brief_chars=400))
    assert entry.brief_issue_count < 4
    assert len(entry.brief_raw) <= 400 + len(FILLER)


# ---------- exclusions ----------

@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"min_brief_issues": 9}, "too_few_brief_issues"),
        ({"min_brief_chars": 100_000}, "brief_too_short"),
    ],
)
def test_offline_guards_exclude_with_a_recorded_reason(toy_sources, overrides, reason):
    assert only(build(toy_sources, **overrides)).exclusion_reason == reason


def test_a_package_before_the_cutoff_is_never_a_case(toy_sources):
    tickets, sprints, membership = toy_sources
    sprints = sprints.copy()
    sprints["start_date"] = datetime(2018, 5, 1)

    assert only(build((tickets, sprints, membership))).exclusion_reason == (
        "sprint_start_not_post_cutoff"
    )


def test_a_package_with_no_eligible_truth_is_excluded_not_relaxed(toy_sources):
    tickets, sprints, membership = toy_sources
    tickets = tickets.copy()
    # Everyone who worked the package is now the person with too little history.
    in_package = tickets["source_issue_id"].isin(["1", "2", "3", "4", "5"])
    tickets.loc[in_package, "evidence_person_id"] = "PROJ:99"
    tickets.loc[in_package, "evidence_person_name"] = "Person PROJ-99"

    entry = only(build((tickets, sprints, membership)))
    assert entry.exclusion_reason == "truth_not_eligible"
    assert entry.truth_person_ids == []
    assert entry.truth_person_count_all == 1


def test_a_stale_people_roster_is_refused_rather_than_silently_narrowing(toy_sources):
    tickets, sprints, membership = toy_sources
    people = pd.DataFrame([{"person_id": "PROJ:10"}, {"person_id": "PROJ:11"}])

    with pytest.raises(ValueError, match="stale or inconsistent"):
        build_package_manifest(
            tickets, sprints, membership, people, cutoff=CUTOFF, min_resolved_tickets=2,
            min_brief_issues=2, max_brief_issues=30, max_brief_chars=8000,
            min_brief_chars=100, min_rewritten_chars=50, n_packages=10,
            min_profile_bucket_tickets=1, max_profile_bucket_tickets=30,
        )


def test_a_sprint_without_dated_membership_cannot_be_a_package(toy_sources):
    tickets, sprints, membership = toy_sources
    membership = membership.copy()
    membership["provenance"] = packages.SNAPSHOT_JOIN
    membership["added_at"] = None

    assert only(build((tickets, sprints, membership))).exclusion_reason == (
        "no_dated_membership"
    )


# ---------- the frozen rewrite ----------

def test_the_manifest_freezes_the_rewrite_and_selects_only_rewritten_cases(toy_sources):
    pending = only(build(toy_sources))
    assert pending.exclusion_reason == "rewrite_pending"
    assert pending.split == "excluded"
    assert pending.query_text == ""

    entry = only(build(toy_sources, rewrites=rewrites_for(pending)))
    assert entry.exclusion_reason is None
    assert entry.split == "test"
    assert entry.query_text == REWRITE
    assert entry.brief_rewritten == REWRITE
    assert entry.brief_raw and entry.brief_raw != entry.query_text


def test_a_rewrite_of_different_source_text_is_refused_as_stale(toy_sources):
    first = only(build(toy_sources))
    stale = rewrites_for(first)
    stale[first.package_key]["input_digest"] = brief_digest("something else entirely")

    entry = only(build(toy_sources, rewrites=stale))
    assert entry.exclusion_reason == "rewrite_pending"
    assert entry.query_text == ""


def test_a_leaky_or_truncated_rewrite_is_excluded(toy_sources):
    first = only(build(toy_sources))

    leaky = only(build(toy_sources, rewrites=rewrites_for(first, "Ask PROJ:11 about it. "
                                                                 + FILLER)))
    assert leaky.exclusion_reason == "rewrite_leakage_guard_failed"

    short = only(build(toy_sources, rewrites=rewrites_for(first, "too short")))
    assert short.exclusion_reason == "rewrite_too_short"


def test_rebuilding_from_the_frozen_manifest_is_deterministic(toy_sources, tmp_path, monkeypatch):
    from capgraph import llm

    def refuse(*args, **kwargs):                       # a rebuild must not call a model
        raise AssertionError("rebuilding the manifest must not call an LLM")

    monkeypatch.setattr(llm, "call_json", refuse)
    pending = only(build(toy_sources))
    rewrites = rewrites_for(pending)

    first = build(toy_sources, rewrites=rewrites)
    second = build(toy_sources, rewrites=rewrites)
    assert [entry.model_dump_json() for entry in first] == [
        entry.model_dump_json() for entry in second
    ]

    manifest = tmp_path / "manifest.jsonl"
    briefs = tmp_path / "briefs.jsonl"
    write_package_manifest(first, manifest_path=manifest, briefs_path=briefs)
    before = manifest.read_bytes()
    write_package_manifest(second, manifest_path=manifest, briefs_path=briefs)
    assert manifest.read_bytes() == before

    reloaded = load_package_manifest(manifest, splits=("validation", "test"))
    assert [entry.package_key for entry in reloaded] == [
        entry.package_key for entry in first if entry.split != "excluded"
    ]
    assert json.loads(briefs.read_text().splitlines()[0])["true_person_ids"] == (
        first[0].truth_person_ids
    )


def test_the_raw_variant_changes_the_words_and_nothing_else(toy_sources, tmp_path):
    pending = only(build(toy_sources))
    entries = build(toy_sources, rewrites=rewrites_for(pending))
    manifest = tmp_path / "manifest.jsonl"
    write_package_manifest(entries, manifest_path=manifest, briefs_path=tmp_path / "b.jsonl")

    rewritten = only(load_package_manifest(manifest, brief_variant=REWRITTEN))
    raw = only(load_package_manifest(manifest, brief_variant=RAW))

    assert rewritten.query_text == REWRITE
    assert raw.query_text == rewritten.brief_raw
    assert raw.brief_variant == RAW
    assert raw.as_of_time == rewritten.as_of_time
    assert raw.truth_person_ids == rewritten.truth_person_ids
    assert raw.eligible_roster == rewritten.eligible_roster


def test_manifest_summary_reconciles_every_candidate(toy_sources):
    pending = only(build(toy_sources))
    entries = build(toy_sources, rewrites=rewrites_for(pending))
    summary = manifest_summary(entries)

    assert summary["candidates"] == summary["selected"] + summary["excluded"]
    assert summary["version"] == PACKAGE_MANIFEST_VERSION
    assert summary["truth_set_sizes"] == {3: 1}
    assert summary["truth_people_dropped_ineligible"] == 1


# ---------- scoring plumbing ----------

def test_a_v4_case_is_a_valid_query_context_only_under_its_own_version(toy_sources):
    pending = only(build(toy_sources))
    entry = only(build(toy_sources, rewrites=rewrites_for(pending)))

    context = query_context(entry, expected_version=PACKAGE_MANIFEST_VERSION)
    assert context.query_text == REWRITE
    assert context.as_of_time == AS_OF
    assert set(entry.truth_person_ids).issubset(context.eligible_roster)

    with pytest.raises(ValueError, match="manifest version"):
        query_context(entry, expected_version=MANIFEST_VERSION)


# ---------- the rewrite stage ----------

def test_the_rewriter_sees_pre_as_of_text_and_nothing_else(toy_sources):
    entry = only(build(toy_sources))
    prompt = rewrite.render_prompt(entry)

    assert "Cluster allocator rebalancing" in prompt
    assert "POSTASOF" not in prompt
    assert "SNAPSHOTONLY" not in prompt
    assert "PROJ:10" not in prompt and "Person PROJ-10" not in prompt
    for forbidden in ("truth", "assignee", "resolved"):
        assert forbidden not in prompt.lower().split("<planned_work>")[0]


def test_the_rewrite_stage_stores_a_sanitized_answer_with_its_input_digest(
    toy_sources, monkeypatch
):
    from capgraph.privacy import LeakageSanitizer

    entry = only(build(toy_sources))
    monkeypatch.setattr(
        rewrite, "call_json", lambda *args, **kwargs: {"brief": f"Work for PROJ:11. {FILLER}"}
    )
    record = rewrite.rewrite_one(entry, LeakageSanitizer(["PROJ:11"]))

    assert record["package_key"] == entry.package_key
    assert "PROJ:11" not in str(record["brief"])
    assert record["input_digest"] == brief_digest(entry.brief_raw)
    assert record["model"] == settings["eval.v4.rewrite_model"]


def test_the_rewrite_stage_refuses_an_answer_it_cannot_clean(toy_sources, monkeypatch):
    from capgraph.privacy import LeakageSanitizer

    entry = only(build(toy_sources))
    monkeypatch.setattr(rewrite, "call_json", lambda *args, **kwargs: {"brief": "   "})
    with pytest.raises(ValueError, match="no brief"):
        rewrite.rewrite_one(entry, LeakageSanitizer([]))


# ---------- engine configurations and spend control ----------

def test_engine_overrides_change_the_run_configuration_and_its_digest():
    from capgraph.eval.run_eval import config_digest

    default = run_v4.v4_config("test", "v3frozen", REWRITTEN)
    with settings.overridden(run_v4.engine_overrides("v2frozen")):
        v2 = run_v4.v4_config("test", "v2frozen", REWRITTEN)

    assert default["retrieval"]["rerank_candidate_view"] == "card"
    assert v2["retrieval"]["rerank_candidate_view"] == "profile"
    assert v2["retrieval"]["rerank_top_k"] == 15
    assert v2["retrieval"]["bm25_top_k"] == 0
    assert v2["rerank_prompt"] == "rerank"
    assert config_digest(default) != config_digest(v2)
    # and the override is scoped: the live settings are back to the default arm
    assert settings["retrieval.rerank_candidate_view"] == "card"


def test_unknown_settings_and_engines_are_refused():
    with pytest.raises(KeyError):
        with settings.overridden({"retrieval.no_such_knob": 1}):
            pass
    with pytest.raises(ValueError, match="unknown engine"):
        run_v4.engine_overrides("v9frozen")
    with pytest.raises(ValueError, match="unknown brief variant"):
        run_v4.runs_dir("v3frozen", "paraphrased")


def test_the_track_ceiling_is_checked_against_the_ledger(monkeypatch, tmp_path):
    ledger = tmp_path / "llm_costs.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps({"stage": stage, "cost_usd": 7.0, "model": "m"})
            for stage in ("bench4_rewrite", "bench4_val")
        )
        + "\n"
    )
    monkeypatch.setattr("capgraph.eval.costs.cost_log_path", lambda: ledger)

    assert run_v4.enforce_v4_budget(0) == 0.0            # $14 logged, nothing projected
    with pytest.raises(run_v4.V4BudgetError, match="exceeds"):
        run_v4.enforce_v4_budget(100)
    with pytest.raises(Exception, match="exceeds"):
        rewrite.enforce_track_ceiling(5.0)


def test_run_namespaces_never_mix_engines_or_brief_variants():
    paths = {
        (engine, variant): run_v4.runs_dir(engine, variant)
        for engine in ("v2frozen", "v3frozen")
        for variant in (REWRITTEN, RAW)
    }
    assert len(set(paths.values())) == 4
    assert run_v4.runs_dir() == paths[("v3frozen", REWRITTEN)]
