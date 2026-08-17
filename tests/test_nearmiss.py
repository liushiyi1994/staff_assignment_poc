"""Near-miss quality study: the rules a reviewer cannot check by reading the report.

Everything here is offline toy data — no graph, no model call, no Docker. What is under
test is the machinery the study's honesty depends on:

* the manifest structure is rebuilt without buying a rewrite, and a drift against the
  published record is a hard failure rather than a warning;
* the test split's rows exist and are unrunnable, so the reserved exposure cannot be
  consumed by accident;
* each similarity definition maximises over the truth set independently, the control is
  seeded and reproducible, and adjacency is over the whole sprint calendar rather than
  over the sampled cases;
* the report-ready statement follows the data — including when the data goes *against*
  the plausible-substitute reading, which is the case a hand-written sentence would get
  wrong.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from capgraph.eval import nearmiss, packages, report_nearmiss
from capgraph.eval.metrics import query_context
from capgraph.eval.nearmiss import (
    DEFINITIONS,
    EMBEDDING_COSINE,
    SKILL_JACCARD,
    SPECIALIZATION_JACCARD,
    NearMissBudgetError,
    NearMissDriftError,
    Profile,
)
from capgraph.eval.packages import PACKAGE_MANIFEST_VERSION, PackageManifestEntry
from capgraph.settings import settings

AS_OF = datetime(2020, 3, 1)
CUTOFF = "2019-01-01"
HALF_LIFE = 540

FILLER = (
    "The scheduler must isolate container resources across the cluster, and the change "
    "touches the allocator, the executor, and the isolation modules that back them. "
)


# ---------- toy Stage 0 sources ----------

def _ticket(
    issue_id: str,
    *,
    project: str = "PROJ",
    person: str = "PROJ:10",
    created: datetime,
    resolved: datetime | None,
    summary: str = "",
) -> dict:
    return {
        "source_issue_id": issue_id,
        "key": f"{project}-{issue_id}",
        "project_key": project,
        "person_id": person,
        "person_name": f"Person {project}-{person.split(':')[-1]}",
        "evidence_person_id": person,
        "evidence_person_name": f"Person {project}-{person.split(':')[-1]}",
        "summary": summary or f"Issue {issue_id} summary",
        "summary_provenance": "snapshot_no_recorded_change",
        "description": FILLER,
        "description_provenance": "snapshot_no_recorded_change",
        "components": [],
        "components_provenance": "snapshot_no_recorded_change",
        "labels": [],
        "snapshot_resolved_at": resolved,
        "created_at": created,
        "query_time_source": "created_at",
        "resolved_at": resolved,
        "resolved_at_provenance": "snapshot_no_recorded_resolution_change",
        "temporal_exclusion_reason": None,
    }


def _history(person: str, n: int, start_id: int) -> list[dict]:
    return [
        _ticket(str(start_id + index), person=person,
                created=datetime(2018, 1, 1), resolved=datetime(2018, 6, 1))
        for index in range(n)
    ]


def _sprint(jira_id: int, start: datetime) -> dict:
    return {
        "sprint_id": str(jira_id), "sprint_jira_id": jira_id, "project_key": "PROJ",
        "name": f"PROJ Sprint {jira_id}", "state": "CLOSED", "start_date": start,
        "end_date": start, "activated_date": None, "complete_date": start,
    }


def _join(issue_id: str, jira_id: int, added_at: datetime) -> dict:
    return {
        "source_issue_id": issue_id, "project_key": "PROJ", "sprint_jira_id": jira_id,
        "added_at": added_at, "provenance": packages.CHANGE_LOG_JOIN,
    }


@pytest.fixture
def toy_sources():
    """Two post-cutoff sprint packages in one project, each with its own truth."""
    tickets = [
        *_history("PROJ:10", 3, 1000),
        *_history("PROJ:11", 3, 1100),
        *_history("PROJ:12", 3, 1200),
        # package 500's brief material and truth
        _ticket("1", created=datetime(2020, 1, 5), resolved=datetime(2020, 3, 20),
                summary="Cluster allocator rebalancing", person="PROJ:10"),
        _ticket("2", created=datetime(2020, 1, 6), resolved=datetime(2020, 3, 21),
                summary="Executor isolation follow-up", person="PROJ:11"),
        # package 501's brief material and truth
        _ticket("3", created=datetime(2020, 4, 5), resolved=datetime(2020, 6, 20),
                summary="Allocator metrics endpoint", person="PROJ:12"),
        _ticket("4", created=datetime(2020, 4, 6), resolved=datetime(2020, 6, 21),
                summary="Metrics endpoint authentication", person="PROJ:12"),
    ]
    sprints = pd.DataFrame([_sprint(500, AS_OF), _sprint(501, datetime(2020, 6, 1))])
    membership = pd.DataFrame(
        [
            _join("1", 500, datetime(2020, 2, 20)),
            _join("2", 500, datetime(2020, 2, 21)),
            _join("3", 501, datetime(2020, 5, 20)),
            _join("4", 501, datetime(2020, 5, 21)),
        ]
    )
    return pd.DataFrame(tickets), pd.DataFrame({"person_id": []}), sprints, membership


@pytest.fixture
def study_root(tmp_path, monkeypatch, toy_sources):
    """Point the study at a scratch data root and a toy Stage 0 export."""
    tickets, _people, sprints, membership = toy_sources
    monkeypatch.setenv("CAPGRAPH_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        nearmiss, "load_sources", lambda root=None: (tickets, None, sprints, membership)
    )
    return tmp_path


@pytest.fixture
def loose_thresholds():
    """The toy fixture is small, so relax only the size thresholds, never the rules."""
    with settings.overridden(
        {
            "eval.v4.min_brief_issues": 2,
            "eval.v4.min_brief_chars": 100,
            "eval.v4.min_rewritten_chars": 20,
            "eval.v4.n_packages": 10,
            "eval.v4.validation_fraction": 0.5,
            "dataset.min_tickets_per_person": 2,
            "bucketing.min_tickets_per_bucket": 1,
        }
    ):
        yield


@pytest.fixture
def toy_record(study_root, loose_thresholds):
    """Replace the published record with the toy build's own shape.

    The verification gate exists to compare a rebuild against the *real* v4 record, so a
    toy rebuild trips it by construction. Deriving the expectation from one build and
    pinning it lets the rest of the pipeline be exercised; the gate's own behaviour is
    tested directly against hand-written entries above and below.
    """
    entries = nearmiss.build_structure()
    validation = nearmiss.verify_validation_split(entries)
    structure = nearmiss.reconcile_structure(entries)
    with settings.overridden(
        {
            "eval.nearmiss.expected_validation": {
                **dict(validation["observed"]), "mean_truth_set_decimals": 2
            },
            "eval.nearmiss.expected_structure": dict(structure["observed"]),
        }
    ):
        yield entries


# ---------- the structure rebuild ----------

def test_the_structure_is_derived_without_buying_a_single_rewrite(
    study_root, loose_thresholds
):
    entries = nearmiss.build_structure()

    selected = [entry for entry in entries if entry.split != "excluded"]
    assert len(selected) == 2, "both packages should be selected from placeholder structure"
    assert {entry.split for entry in selected} == {"validation", "test"}
    # No rewrite was purchased, so nothing carries rewritten text...
    assert all(not entry.brief_rewritten for entry in entries)
    # ...but every row keeps the structure the study needs.
    assert all(entry.brief_raw for entry in selected)
    assert all(entry.truth_person_ids for entry in selected)
    assert all(entry.eligible_roster for entry in selected)
    # The placeholder never leaks into a shipped row.
    assert all(nearmiss.STRUCTURE_PLACEHOLDER not in entry.query_text for entry in entries)
    assert all(nearmiss.STRUCTURE_PLACEHOLDER not in entry.brief_rewritten
               for entry in entries)


def test_every_row_is_relabelled_a_sibling_never_the_frozen_manifest(
    study_root, loose_thresholds
):
    entries = nearmiss.build_structure()

    version = nearmiss.manifest_version()
    assert version != PACKAGE_MANIFEST_VERSION
    assert "sibling" in version
    assert "sibling" in nearmiss.manifest_path().name
    assert {entry.manifest_version for entry in entries} == {version}


def test_an_unrewritten_row_cannot_be_run_by_accident(study_root, loose_thresholds):
    """The reserved test exposure is protected structurally, not by a comment."""
    entries = nearmiss.build_structure()
    test_rows = [entry for entry in entries if entry.split == "test"]
    assert test_rows

    for entry in test_rows:
        assert entry.query_text == ""
        with pytest.raises(ValueError, match="incomplete query context"):
            query_context(entry, expected_version=nearmiss.manifest_version())


def test_a_purchased_rewrite_reaches_its_own_row_and_no_other(
    study_root, loose_thresholds, monkeypatch
):
    first = nearmiss.build_structure()
    target = next(entry for entry in first if entry.split == "validation")
    brief = "Two engineers are needed for allocator and isolation work this sprint."
    monkeypatch.setattr(
        nearmiss,
        "load_rewrites",
        lambda: {
            target.package_key: {
                "brief": brief,
                "input_digest": packages.brief_digest(target.brief_raw),
                "model": "test-model",
                "prompt_digest": "abc123",
            }
        },
    )

    entries = nearmiss.build_structure()
    rewritten = [entry for entry in entries if entry.brief_rewritten]
    assert [entry.package_key for entry in rewritten] == [target.package_key]
    assert rewritten[0].query_text == brief
    assert query_context(
        rewritten[0], expected_version=nearmiss.manifest_version()
    ).query_text == brief


def test_a_stale_purchased_rewrite_is_refused_rather_than_paired_with_new_text(
    study_root, loose_thresholds, monkeypatch
):
    """Inherited from the v4 builder, asserted here because this study depends on it."""
    first = nearmiss.build_structure()
    target = next(entry for entry in first if entry.split == "validation")
    monkeypatch.setattr(
        nearmiss,
        "load_rewrites",
        lambda: {
            target.package_key: {
                "brief": "A brief written from text this package no longer holds.",
                "input_digest": "0000000000000000",
                "model": "test-model",
                "prompt_digest": "abc123",
            }
        },
    )

    entries = nearmiss.build_structure()
    row = next(e for e in entries if e.package_key == target.package_key)
    assert row.brief_rewritten == ""
    assert row.query_text == ""


# ---------- the verification gate ----------

def _entry(package_key: str, project: str, truth: list[str], split: str,
           as_of: datetime = AS_OF) -> PackageManifestEntry:
    return PackageManifestEntry(
        seed=1, issue_id=package_key, package_key=package_key, project_key=project,
        query_text="brief", as_of_time=as_of, truth_person_ids=truth,
        eligible_roster=sorted({*truth, f"{project}:99"}), split=split,
        exclusion_reason=None if split != "excluded" else "sampled_out",
    )


def test_a_matching_split_verifies_and_a_drifted_one_does_not():
    expected = {"cases": 2, "projects": {"DM": 2}, "mean_truth_set_size": 1.5,
                "mean_truth_set_decimals": 2}
    entries = [
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation"),
        _entry("DM:sprint:2", "DM", ["DM:1", "DM:2"], "validation"),
    ]
    with settings.overridden({"eval.nearmiss.expected_validation": expected}):
        assert nearmiss.verify_validation_split(entries)["matches"] is True

        drifted = [*entries, _entry("DM:sprint:3", "DM", ["DM:3"], "validation")]
        result = nearmiss.verify_validation_split(drifted)
        assert result["matches"] is False
        assert "cases" in result["mismatches"]
        assert "mean_truth_set_size" in result["mismatches"]


def test_a_project_mix_change_alone_is_caught():
    expected = {"cases": 2, "projects": {"DM": 2}, "mean_truth_set_size": 1.0,
                "mean_truth_set_decimals": 2}
    entries = [
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation"),
        _entry("TIMOB:sprint:2", "TIMOB", ["TIMOB:1"], "validation"),
    ]
    with settings.overridden({"eval.nearmiss.expected_validation": expected}):
        result = nearmiss.verify_validation_split(entries)

    assert result["matches"] is False
    assert result["mismatches"] == ["projects"]


def test_split_drift_stops_the_study_instead_of_being_reported(toy_record):
    """A rebuild whose split does not match the record must not reach a measurement."""
    with settings.overridden(
        {
            "eval.nearmiss.expected_validation": {
                "cases": 99, "projects": {"PROJ": 99}, "mean_truth_set_size": 9.0,
                "mean_truth_set_decimals": 2,
            }
        }
    ):
        with pytest.raises(NearMissDriftError, match="validation split"):
            nearmiss.structure(write=False)
    assert not nearmiss.manifest_path().exists()


def test_structure_drift_stops_the_study_too(toy_record):
    """Same gate, wider: the whole manifest has to reproduce, not only the split."""
    expected = dict(settings["eval.nearmiss.expected_structure"])
    with settings.overridden(
        {"eval.nearmiss.expected_structure": dict(expected, candidates=999)}
    ):
        with pytest.raises(NearMissDriftError, match="manifest structure"):
            nearmiss.structure(write=False)
    assert not nearmiss.manifest_path().exists()


def test_the_structure_reconciliation_checks_the_test_split_too():
    entries = [
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation"),
        _entry("DM:sprint:2", "DM", ["DM:1", "DM:2"], "test"),
        _entry("DM:sprint:3", "DM", ["DM:3"], "excluded"),
    ]
    expected = {
        "candidates": 3, "selected": 2,
        "exclusion_reasons": {"sampled_out": 1},
        "truth_people_total": 3, "truth_people_dropped_ineligible": 0,
        "briefs_hitting_a_cap": 0, "test_cases": 1,
        "test_projects": {"DM": 1}, "test_mean_truth_set_size": 2.0,
    }
    with settings.overridden({"eval.nearmiss.expected_structure": expected}):
        assert nearmiss.reconcile_structure(entries)["matches"] is True

        wrong = dict(expected, test_cases=7)
        with settings.overridden({"eval.nearmiss.expected_structure": wrong}):
            result = nearmiss.reconcile_structure(entries)
    assert result["matches"] is False
    assert result["mismatches"] == ["test_cases"]


# ---------- the three similarity definitions ----------

def _profile(person_id: str, specs: list[str], skills: list[tuple[str, int, date]],
             vector: list[float] | None) -> Profile:
    return Profile(
        person_id=person_id,
        specializations=frozenset(specs),
        skills=tuple(skills),
        mean_embedding=None if vector is None else np.asarray(vector, dtype=np.float64),
    )


def test_jaccard_and_cosine_handle_the_degenerate_cases():
    assert nearmiss.jaccard(frozenset("ab"), frozenset("ab")) == 1.0
    assert nearmiss.jaccard(frozenset("ab"), frozenset("bc")) == pytest.approx(1 / 3)
    assert nearmiss.jaccard(frozenset(), frozenset()) == 0.0
    assert nearmiss.jaccard(frozenset("a"), frozenset()) == 0.0

    unit = np.asarray([1.0, 0.0])
    assert nearmiss.cosine(unit, unit) == pytest.approx(1.0)
    assert nearmiss.cosine(unit, np.asarray([0.0, 1.0])) == pytest.approx(0.0)
    assert nearmiss.cosine(unit, None) == 0.0
    assert nearmiss.cosine(unit, np.zeros(2)) == 0.0


def test_recency_weighting_can_overturn_raw_evidence_volume():
    """The point of definition (b): the busiest skill is not automatically the top one."""
    profile = _profile(
        "P:1", [],
        [("stale", 10, date(2014, 1, 1)), ("current", 4, date(2018, 12, 1))],
        None,
    )

    assert profile.top_skills(date(2019, 1, 1), k=1, half_life_days=HALF_LIFE) == (
        frozenset({"current"})
    ), "10 pieces of five-year-old evidence lose to 4 recent ones at a 540-day half-life"
    assert profile.top_skills(date(2019, 1, 1), k=1, half_life_days=10**6) == (
        frozenset({"stale"})
    ), "with decay effectively switched off, volume wins again"


def test_the_top_skill_ranking_is_as_of_invariant_on_a_graph_frozen_before_every_case():
    """A property of exponential decay, recorded because the report leans on it.

    Weight is ``count x exp(-λ(as_of - last_used))``, so the ratio between two skills'
    weights is ``(c1/c2) x exp(λ(t1 - t2))`` — ``as_of`` cancels. Computing definition (b)
    at each case's own as-of time is the right temporal discipline and is what the harness
    does, but on this graph — frozen at the holdout cutoff, with every case's as-of time
    after it — it cannot change which skills come top.
    """
    profile = _profile(
        "P:1", [],
        [("a", 10, date(2016, 1, 1)), ("b", 4, date(2018, 12, 1)),
         ("c", 7, date(2017, 6, 1))],
        None,
    )

    ranked = [
        profile.top_skills(as_of, k=2, half_life_days=HALF_LIFE)
        for as_of in (date(2019, 1, 2), date(2019, 7, 1), date(2020, 10, 12))
    ]

    assert len(set(ranked)) == 1, "every as-of after the cutoff gives the same top-2"


def test_a_last_use_after_the_as_of_time_is_the_only_thing_that_moves_the_ranking():
    """The clamp, which is unreachable here — every as-of time is after the cutoff."""
    profile = _profile(
        "P:1", [],
        [("stale", 10, date(2016, 1, 1)), ("future", 4, date(2018, 12, 1))],
        None,
    )

    # as_of before "future"'s last use: its decay clamps to 1.0 and volume decides.
    assert profile.top_skills(date(2016, 6, 1), k=1, half_life_days=HALF_LIFE) == (
        frozenset({"stale"})
    )
    assert profile.top_skills(date(2019, 1, 1), k=1, half_life_days=HALF_LIFE) == (
        frozenset({"future"})
    )


def test_top_skills_is_capped_and_breaks_ties_on_name():
    skills = [(f"skill{index}", 1, date(2018, 1, 1)) for index in range(20)]
    profile = _profile("P:1", [], skills, None)

    top = profile.top_skills(date(2019, 1, 1), k=3, half_life_days=HALF_LIFE)

    assert top == frozenset({"skill0", "skill1", "skill10"})


def test_each_definition_picks_its_own_nearest_truth_person():
    subject = _profile("P:0", ["alloc"], [("a", 5, date(2018, 1, 1))], [1.0, 0.0])
    spec_twin = _profile("P:1", ["alloc"], [("z", 5, date(2018, 1, 1))], [0.0, 1.0])
    vector_twin = _profile("P:2", ["other"], [("y", 5, date(2018, 1, 1))], [1.0, 0.0])
    profiles = {p.person_id: p for p in (subject, spec_twin, vector_twin)}

    best, who = nearmiss.nearest(
        subject, [spec_twin, vector_twin], as_of=date(2019, 1, 1), top_k=10,
        half_life_days=HALF_LIFE,
    )

    assert best[SPECIALIZATION_JACCARD] == 1.0
    assert who[SPECIALIZATION_JACCARD] == "P:1"
    assert best[EMBEDDING_COSINE] == pytest.approx(1.0)
    assert who[EMBEDDING_COSINE] == "P:2"
    assert best[SKILL_JACCARD] == 0.0
    assert set(profiles) == {"P:0", "P:1", "P:2"}


def test_intra_truth_similarity_does_not_depend_on_what_the_system_answered():
    """The supplementary yardstick: how alike two people on the same package are."""
    a = _profile("P:1", ["alloc"], [("x", 5, date(2018, 1, 1))], [1.0, 0.0])
    b = _profile("P:2", ["alloc"], [("y", 5, date(2018, 1, 1))], [1.0, 0.0])
    c = _profile("P:3", ["other"], [("z", 5, date(2018, 1, 1))], [0.0, 1.0])

    pair = nearmiss.intra_truth_similarity(
        [a, b], as_of=date(2019, 1, 1), top_k=10, half_life_days=HALF_LIFE
    )
    assert pair[SPECIALIZATION_JACCARD] == 1.0, "both are 'alloc' specialists"
    assert pair[SKILL_JACCARD] == 0.0, "no shared skill term"
    assert pair[EMBEDDING_COSINE] == pytest.approx(1.0)

    trio = nearmiss.intra_truth_similarity(
        [a, b, c], as_of=date(2019, 1, 1), top_k=10, half_life_days=HALF_LIFE
    )
    # a and b still find each other; c finds nobody alike, so the mean drops to 2/3.
    assert trio[SPECIALIZATION_JACCARD] == pytest.approx(2 / 3)


def test_a_single_person_truth_set_has_no_intra_similarity():
    lone = _profile("P:1", ["alloc"], [("x", 5, date(2018, 1, 1))], [1.0, 0.0])

    values = nearmiss.intra_truth_similarity(
        [lone], as_of=date(2019, 1, 1), top_k=10, half_life_days=HALF_LIFE
    )

    assert all(value != value for value in values.values()), "NaN, never 0.0 or 1.0"


def test_a_truth_person_scores_one_against_themselves():
    """The hit reference is 1.0 by construction; this is that arithmetic, checked."""
    person = _profile("P:1", ["alloc"], [("a", 5, date(2018, 1, 1))], [0.3, 0.4])

    best, _ = nearmiss.nearest(
        person, [person], as_of=date(2019, 1, 1), top_k=10, half_life_days=HALF_LIFE
    )

    assert [best[name] for name in DEFINITIONS] == pytest.approx([1.0, 1.0, 1.0])


# ---------- the control ----------

def test_the_control_is_reproducible_and_depends_on_the_case():
    assert nearmiss.control_seed_for("DM:sprint:1", 7) == nearmiss.control_seed_for(
        "DM:sprint:1", 7
    )
    assert nearmiss.control_seed_for("DM:sprint:1", 7) != nearmiss.control_seed_for(
        "DM:sprint:2", 7
    )
    assert nearmiss.control_seed_for("DM:sprint:1", 7) != nearmiss.control_seed_for(
        "DM:sprint:1", 8
    )


def test_the_control_median_is_the_typical_roster_member_not_the_best_one():
    """One roster member matches truth exactly and eight do not; the median must not."""
    truth = _profile("P:1", ["alloc"], [("a", 5, date(2018, 1, 1))], [1.0, 0.0])
    twin = _profile("P:2", ["alloc"], [("a", 5, date(2018, 1, 1))], [1.0, 0.0])
    strangers = [
        _profile(f"P:{index}", [f"other{index}"], [(f"s{index}", 1, date(2018, 1, 1))],
                 [0.0, 1.0])
        for index in range(10, 18)
    ]
    profiles = {p.person_id: p for p in (truth, twin, *strangers)}
    roster = sorted(profiles)

    control = nearmiss.control_median(
        roster, [truth], profiles, package_key="DM:sprint:1", as_of=date(2019, 1, 1),
        top_k=10, half_life_days=HALF_LIFE, draws=101, base_seed=20260817,
    )
    again = nearmiss.control_median(
        roster, [truth], profiles, package_key="DM:sprint:1", as_of=date(2019, 1, 1),
        top_k=10, half_life_days=HALF_LIFE, draws=101, base_seed=20260817,
    )

    assert control == again, "the same seed must give the same control"
    assert control[SPECIALIZATION_JACCARD] == 0.0, "8 of 10 roster members share nothing"
    assert control[EMBEDDING_COSINE] == pytest.approx(0.0)


# ---------- adjacency ----------

def test_adjacency_is_the_projects_next_sprint_not_the_next_sampled_case():
    entries = [
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation", datetime(2019, 1, 1)),
        _entry("DM:sprint:2", "DM", ["DM:2"], "excluded", datetime(2019, 2, 1)),
        _entry("DM:sprint:3", "DM", ["DM:3"], "validation", datetime(2019, 3, 1)),
        _entry("TIMOB:sprint:9", "TIMOB", ["TIMOB:9"], "validation", datetime(2019, 2, 1)),
    ]

    adjacency = nearmiss.adjacent_truth(entries)

    # The excluded sprint 2 sits between 1 and 3 in the calendar, so it is 1's neighbour.
    assert adjacency["DM:sprint:1"]["next_package"] == "DM:sprint:2"
    assert adjacency["DM:sprint:1"]["next_truth"] == ["DM:2"]
    assert adjacency["DM:sprint:1"]["previous_package"] is None
    assert adjacency["DM:sprint:3"]["previous_package"] == "DM:sprint:2"
    # Projects never neighbour each other, even at the same date.
    assert adjacency["TIMOB:sprint:9"]["previous_package"] is None
    assert adjacency["TIMOB:sprint:9"]["next_package"] is None


def test_concurrent_sprint_starts_are_counted_from_dates_not_names():
    """Context for the adjacency diagnostic: one live board or several changes what it means.

    Names are deliberately not consulted. A name-based version of this reported every
    sprint of the project whose sprints are called "2019 Sprint 4" as its own board.
    """
    entries = [
        # Three start inside the same week; the fourth is a month later, on its own.
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation", datetime(2019, 1, 1)),
        _entry("DM:sprint:2", "DM", ["DM:2"], "test", datetime(2019, 1, 3)),
        _entry("DM:sprint:3", "DM", ["DM:3"], "test", datetime(2019, 1, 5)),
        _entry("DM:sprint:4", "DM", ["DM:4"], "test", datetime(2019, 3, 1)),
    ]
    for entry, name in zip(
        entries, ("2019 Sprint 1", "2019 Sprint 2", "2019 Sprint 3", "2019 Sprint 4"),
        strict=True,
    ):
        entry.package_name = name

    result = nearmiss.concurrent_starts(entries, project_key="DM", window_days=7)

    assert result["post_cutoff_sprints"] == 4
    # Counts per sprint are 2, 2, 2, 0 -> median 2, max 2. A name-based count would have
    # said four separate boards.
    assert result["median_concurrent_starts"] == 2
    assert result["max_concurrent_starts"] == 2
    assert nearmiss.concurrent_starts(
        entries, project_key="TIMOB", window_days=7
    )["post_cutoff_sprints"] == 0


def test_a_project_running_one_board_has_no_concurrent_starts():
    entries = [
        _entry(f"DM:sprint:{index}", "DM", [f"DM:{index}"], "test",
               datetime(2019, 1 + index, 1))
        for index in range(3)
    ]

    result = nearmiss.concurrent_starts(entries, project_key="DM", window_days=7)

    assert result["median_concurrent_starts"] == 0
    assert result["max_concurrent_starts"] == 0


def test_pre_cutoff_sprints_are_left_out_of_the_calendar_density():
    entries = [
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation", datetime(2019, 1, 1)),
        _entry("DM:sprint:0", "DM", ["DM:0"], "excluded", datetime(2016, 1, 1)),
    ]
    entries[1].exclusion_reason = "sprint_start_not_post_cutoff"

    result = nearmiss.concurrent_starts(entries, project_key="DM", window_days=7)

    assert result["post_cutoff_sprints"] == 1


def test_a_package_without_a_recorded_start_has_no_place_in_the_calendar():
    entries = [
        _entry("DM:sprint:1", "DM", ["DM:1"], "validation", datetime(2019, 1, 1)),
        PackageManifestEntry(
            seed=1, issue_id="DM:sprint:2", package_key="DM:sprint:2", project_key="DM",
            query_text="", as_of_time=None, split="excluded",
            exclusion_reason="sprint_start_missing",
        ),
    ]

    adjacency = nearmiss.adjacent_truth(entries)

    assert "DM:sprint:2" not in adjacency
    assert adjacency["DM:sprint:1"]["next_package"] is None


# ---------- spend discipline ----------

def test_the_studys_own_ceiling_is_checked_against_the_ledger(tmp_path, monkeypatch):
    from capgraph import llm

    ledger = tmp_path / "llm_costs.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps({"stage": stage, "cost_usd": cost, "model": "m",
                        "input_tokens": 1, "output_tokens": 1, "ts": 0})
            for stage, cost in (("nearmiss_rewrite", 0.01), ("nearmiss_val", 3.90),
                                ("bench4_val", 6.83))
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "_COST_LOG", ledger)

    # $3.91 logged for this study; $0.05 more fits under $4.00 and $0.20 does not.
    assert nearmiss.enforce_ceiling(0.05) == pytest.approx(3.91)
    with pytest.raises(NearMissBudgetError, match="escalate to the orchestrator"):
        nearmiss.enforce_ceiling(0.20)


def test_the_ceiling_ignores_other_studies_spend(tmp_path, monkeypatch):
    """$14.21 of v4 spend must not consume this study's $4."""
    from capgraph import llm

    ledger = tmp_path / "llm_costs.jsonl"
    ledger.write_text(
        json.dumps({"stage": "bench4_test", "cost_usd": 14.21, "model": "m",
                    "input_tokens": 1, "output_tokens": 1, "ts": 0}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "_COST_LOG", ledger)

    assert nearmiss.enforce_ceiling(3.99) == 0.0


def test_binding_the_data_root_moves_the_ledger_and_the_bucket_corpus(
    tmp_path, monkeypatch
):
    """The incident's no-symlink rule leaves absolute paths, which is what this is."""
    from capgraph import evidence, llm

    monkeypatch.setenv("CAPGRAPH_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(llm, "_COST_LOG", tmp_path / "unset")
    monkeypatch.setattr(evidence, "BUCKETS_PATH", tmp_path / "unset")

    root = nearmiss.bind_data_root()

    assert root == tmp_path
    assert llm._COST_LOG == tmp_path / "llm_costs.jsonl"
    assert evidence.BUCKETS_PATH == tmp_path / "buckets" / "buckets.jsonl"
    assert nearmiss.study_dir() == tmp_path / "eval" / "nearmiss"
    assert nearmiss.study_dir() in nearmiss.manifest_path().parents


def test_this_study_only_ever_writes_inside_its_own_namespace(tmp_path, monkeypatch):
    """The concurrent restoration order owns data/eval/ elsewhere; stay out of it."""
    monkeypatch.setenv("CAPGRAPH_DATA_ROOT", str(tmp_path))
    namespace = tmp_path / "eval" / "nearmiss"

    for path in (nearmiss.manifest_path(), nearmiss.manifest_meta_path(),
                 nearmiss.rewrites_path(), nearmiss.runs_dir(),
                 nearmiss.study_json_path()):
        assert namespace in path.parents or path == namespace, path


# ---------- the run is one split, and only one ----------

def test_the_study_loads_its_own_split_and_nothing_else(toy_record):
    entries, _ = nearmiss.structure()

    assert {entry.split for entry in entries if entry.split != "excluded"} == {
        "validation", "test"
    }
    assert [entry.split for entry in nearmiss.cases()] == ["validation"]


def test_running_before_paying_for_a_rewrite_is_refused(toy_record):
    nearmiss.structure()

    with pytest.raises(SystemExit, match="no purchased rewrite"):
        nearmiss.run()


def test_only_the_study_split_is_ever_rewritten(toy_record, monkeypatch):
    """The acceptance criterion, asserted: zero test-case rewrites, ever."""
    asked: list[str] = []

    def fake_rewrite_one(entry, sanitizer):
        asked.append(entry.package_key)
        return {
            "package_key": entry.package_key, "project_key": entry.project_key,
            "brief": "A rewritten brief for allocator and isolation work.",
            "model": "test-model", "prompt": "brief_rewrite", "prompt_digest": "abc123",
            "input_digest": packages.brief_digest(entry.brief_raw),
            "rewritten_at": "2026-08-17T00:00:00Z",
        }

    monkeypatch.setattr(nearmiss, "rewrite_one", fake_rewrite_one)
    monkeypatch.setattr(nearmiss, "enforce_ceiling", lambda projected: 0.0)

    entries = nearmiss.build_structure()
    validation = {e.package_key for e in entries if e.split == "validation"}
    test = {e.package_key for e in entries if e.split == "test"}
    assert validation and test, "the fixture needs both splits for this to mean anything"

    counts = nearmiss.rewrite_validation()

    assert set(asked) == validation
    assert not set(asked) & test, "no test package may ever be sent to the rewriter"
    assert counts == {"pending": len(validation), "rewritten": len(validation),
                      "failed": 0}
    # And the second call is free: the checkpoint makes it a no-op.
    asked.clear()
    assert nearmiss.rewrite_validation()["rewritten"] == 0
    assert asked == []


def test_a_rewritten_validation_row_leaves_the_test_rows_unrunnable(
    toy_record, monkeypatch
):
    monkeypatch.setattr(
        nearmiss,
        "rewrite_one",
        lambda entry, sanitizer: {
            "package_key": entry.package_key, "project_key": entry.project_key,
            "brief": "A rewritten brief for allocator and isolation work.",
            "model": "test-model", "prompt": "brief_rewrite", "prompt_digest": "abc123",
            "input_digest": packages.brief_digest(entry.brief_raw),
            "rewritten_at": "2026-08-17T00:00:00Z",
        },
    )
    monkeypatch.setattr(nearmiss, "enforce_ceiling", lambda projected: 0.0)

    nearmiss.rewrite_validation()

    rows = nearmiss.load_manifest()
    assert all(row.query_text for row in rows if row.split == "validation")
    assert all(row.query_text == "" for row in rows if row.split == "test")
    meta = json.loads(nearmiss.manifest_meta_path().read_text())
    assert meta["sibling"] is True
    assert meta["sibling_of"] == PACKAGE_MANIFEST_VERSION
    assert meta["rewrites_purchased_for_splits"] == ["validation"]
    assert "untouched" in meta["test_split_exposure"]


# ---------- the report follows the data ----------

def _delta(low: float, high: float, mean: float = 0.0) -> dict:
    return {"n": 12, "control_mean": 0.1, "similarity_mean": mean, "mean_delta": mean,
            "ci_low": low, "ci_high": high, "above_control": 8, "below_control": 4,
            "ties": 0}


def test_the_reading_rule_needs_the_interval_to_clear_zero():
    assert report_nearmiss.classify(_delta(0.05, 0.30)) == report_nearmiss.SUPPORTS
    assert report_nearmiss.classify(_delta(-0.30, -0.05)) == report_nearmiss.CONTRADICTS
    assert report_nearmiss.classify(_delta(-0.05, 0.30)) == report_nearmiss.INCONCLUSIVE
    assert report_nearmiss.classify(_delta(0.0, 0.30)) == report_nearmiss.INCONCLUSIVE
    assert report_nearmiss.classify({"n": 0}) == report_nearmiss.INCONCLUSIVE


def _payload(deltas: dict[str, tuple[float, float]]) -> dict:
    def group(n: int) -> dict:
        return {
            "n": n,
            "packages": [f"DM:sprint:{index}" for index in range(n)],
            "similarity": {name: {"n": n, "mean": 0.4, "ci_low": 0.3, "ci_high": 0.5}
                           for name in DEFINITIONS},
            "control": {name: {"n": n, "mean": 0.2, "ci_low": 0.1, "ci_high": 0.3}
                        for name in DEFINITIONS},
            "delta_vs_control": {
                name: _delta(*deltas[name], mean=0.4) for name in DEFINITIONS
            },
            "intra_truth_set_supplementary": {
                name: {"n": n, "mean": 0.5, "ci_low": 0.4, "ci_high": 0.6}
                for name in DEFINITIONS
            },
            "top1_concentration": {
                "n": n, "distinct_people": 4, "most_frequent": "DM:145735",
                "most_frequent_count": 9, "counts": {"DM:145735": 9},
            },
            "adjacent_sprint": {
                "n": n, "in_previous_or_next": 5, "in_previous": 3, "in_next": 4,
                "share": 5 / n, "packages": [], "neighbour_truth_size_median": 7,
                "cases_with_no_neighbour_truth": 0,
                "own_truth_jaccard_with_neighbours": {
                    "n": n, "mean": 0.12, "ci_low": 0.05, "ci_high": 0.2
                },
            },
        }

    return {
        "generated_at": "2026-08-17T00:00:00+00:00",
        "manifest": "/tmp/m.jsonl", "manifest_version": "sibling",
        "runs_dir": "/tmp/runs", "config_digest": "deadbeef",
        "split": "validation", "engine": "v3frozen", "brief_variant": "rewritten",
        "definitions": {name: name for name in DEFINITIONS},
        "settings": {"top_skills": 10, "control_draws": 100, "control_seed": 1,
                     "recency_half_life_days": 540, "bootstrap_resamples": 10000,
                     "bootstrap_seed": 1},
        "verification": {
            "record": "r", "split": "validation",
            "observed": {"cases": 28, "projects": {"DM": 15}, "mean_truth_set_size": 3.39},
            "expected": {"cases": 28, "projects": {"DM": 15}, "mean_truth_set_size": 3.39},
            "mismatches": [], "matches": True, "truth_set_sizes": {}, "as_of_span": [],
            "structure": {"record": "r2", "observed": {"candidates": 1061},
                          "expected": {"candidates": 1061}, "mismatches": [],
                          "matches": True},
        },
        "sprint_calendar": [
            {"project_key": "DM", "post_cutoff_sprints": 150, "window_days": 7,
             "median_concurrent_starts": 3, "max_concurrent_starts": 8},
            {"project_key": "MESOS", "post_cutoff_sprints": 83, "window_days": 7,
             "median_concurrent_starts": 2, "max_concurrent_starts": 5},
        ],
        "cases_in_split": 28, "cases_scored": 28, "run_failures": [],
        "cases": [
            {
                "package_key": "DM:sprint:1", "project_key": "DM",
                "as_of": "2019-01-02T00:00:00", "roster_size": 105, "truth_size": 3,
                "top1": "DM:1", "outcome": outcome, "first_truth_rank": 1 if index else 4,
                "hit_at_1": 1.0, "recall_at_10": 0.5, "reciprocal_rank": 1.0,
                "similarity_to_nearest_truth": {name: 0.4 for name in DEFINITIONS},
                "nearest_truth_person": {name: "DM:2" for name in DEFINITIONS},
                "control_median": {name: 0.2 for name in DEFINITIONS},
                "intra_truth_set_supplementary": {name: 0.5 for name in DEFINITIONS},
                "adjacent_sprint_truth": {"previous": True, "next": False,
                                          "either": True},
            }
            for index, outcome in enumerate(("miss", "hit"))
        ],
        "distributions": {"misses": group(16), "hits": group(12)},
        "spend": {
            "stages": [{"stage": "nearmiss_rewrite", "calls": 29, "cost_usd": 0.01},
                       {"stage": "nearmiss_val", "calls": 80, "cost_usd": 1.7}],
            "by_purpose": {"rerank": {"calls": 51, "cost_usd": 1.6}},
            "ceiling_usd": 4.0,
        },
    }


def test_the_statement_supports_the_reading_only_when_the_data_does():
    supportive = _payload({name: (0.05, 0.30) for name in DEFINITIONS})

    markdown = report_nearmiss.render(supportive)

    assert "all three" in report_nearmiss.verdict(supportive["distributions"])["direction"]
    assert "sit closer to the truth set" in markdown
    assert "not supported by this measurement" not in markdown


def test_the_statement_reverses_itself_when_the_data_goes_the_other_way():
    """The case a hand-written sentence would get wrong."""
    against = _payload({name: (-0.30, -0.05) for name in DEFINITIONS})

    markdown = report_nearmiss.render(against)

    assert report_nearmiss.verdict(against["distributions"])["direction"] == "against"
    assert "not supported by this measurement" in markdown
    assert "no closer" in markdown


def test_an_inconclusive_study_says_so_rather_than_leaning():
    unclear = _payload({name: (-0.05, 0.30) for name in DEFINITIONS})

    markdown = report_nearmiss.render(unclear)

    assert report_nearmiss.verdict(unclear["distributions"])["direction"] == "none"
    assert "inconclusive" in markdown


def test_the_report_surfaces_how_concentrated_the_misses_top_picks_are():
    """A mean over misses hides one profile taking most of the top slots."""
    markdown = report_nearmiss.render(_payload({n: (0.05, 0.3) for n in DEFINITIONS}))

    assert "4 distinct people" in markdown
    assert "`DM:145735`" in markdown
    assert "takes the top slot in 9 of them" in markdown


def test_top1_concentration_counts_distinct_first_picks():
    cases = [
        nearmiss.CaseResult(
            package_key=f"DM:sprint:{index}", project_key="DM", as_of=AS_OF,
            roster_size=105, truth_size=2, top1=top1, is_hit=False,
            first_truth_rank=3, hit_at_1=0.0, recall_at_10=0.5, reciprocal_rank=0.33,
            similarity=dict.fromkeys(DEFINITIONS, 0.2),
            control=dict.fromkeys(DEFINITIONS, 0.1),
            intra_truth=dict.fromkeys(DEFINITIONS, 0.4),
        )
        for index, top1 in enumerate(("DM:1", "DM:1", "DM:1", "DM:2"))
    ]

    concentration = nearmiss.distributions(cases)["misses"]["top1_concentration"]

    assert concentration["n"] == 4
    assert concentration["distinct_people"] == 2
    assert concentration["most_frequent"] == "DM:1"
    assert concentration["most_frequent_count"] == 3


def test_the_report_labels_the_adjacent_sprint_metric_as_post_as_of_everywhere():
    markdown = report_nearmiss.render(_payload({n: (0.05, 0.3) for n in DEFINITIONS}))

    assert markdown.count("post-as-of") >= 3, "in the method, the table, and the claim"
    assert "never** available for tuning" in markdown
    assert "not** pre-specified" in markdown, "the supplementary block must be labelled"
    assert "does not rest on it" in markdown
    assert "descriptive study" in markdown
    assert "p-value" in markdown, "the report has to say it is not testing anything"
