"""Fixture tests for Stage 4 projections. Offline: pure arithmetic over toy contributions.

The temporal contract is what these tests exist to protect: recency is measured from a
configured snapshot rather than the day the suite happens to run, and a quarter that is
not wholly before that snapshot is rejected instead of being given an invented day.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from capgraph.models import Contribution, PersonCapability, SkillRef, SpecializationRef
from capgraph.pipeline import stage4_project as stage4
from capgraph.pipeline.stage4_project import build_capabilities, decay, period_end
from capgraph.settings import settings

CUTOFF = date.fromisoformat(settings["dataset.holdout_cutoff"])
HALF_LIFE = int(settings["projections.recency_half_life_days"])


def _contribution(person_id: str, period: str, skills, specializations=(), suffix="0"):
    return Contribution(
        contribution_id=f"{person_id}|PROJ|{period}|{suffix}",
        person_id=person_id,
        project_key="PROJ",
        period=period,
        contribution_summary="Kept the broker retry path from melting down.",
        skills=[SkillRef(name=name) for name in skills],
        specializations=[
            SpecializationRef(name=name, strength=strength) for name, strength in specializations
        ],
        confidence="high",
        reason="All 5 of the 5 tickets shown describe the same work.",
        evidence_ticket_keys=[f"PROJ-{index}" for index in range(5)],
    )


@pytest.fixture
def stage4_paths(tmp_path, monkeypatch):
    """Redirect Stage 4 input/output into tmp_path; returns the path namespace."""
    paths = {
        "NORM_PATH": tmp_path / "contributions" / "normalized.jsonl",
        "CAPS_PATH": tmp_path / "contributions" / "capabilities.jsonl",
    }
    for name, path in paths.items():
        monkeypatch.setattr(stage4, name, path)
    paths["NORM_PATH"].parent.mkdir(parents=True, exist_ok=True)
    return paths


def _write_normalized(path, contribs):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for contribution in contribs:
            handle.write(contribution.model_dump_json() + "\n")
    return path


# ---------- quarter arithmetic ----------

def test_period_end_is_the_last_calendar_day_of_the_quarter():
    assert period_end("2018-Q1") == date(2018, 3, 31)
    assert period_end("2018-Q2") == date(2018, 6, 30)
    assert period_end("2018-Q3") == date(2018, 9, 30)
    assert period_end("2018-Q4") == date(2018, 12, 31)   # Q4 rolls to December, not January
    assert period_end("2016-Q1") == date(2016, 3, 31)    # leap year: 91 days, still 31 March
    assert period_end("2016-Q4") == date(2016, 12, 31)


def test_period_end_rejects_a_malformed_period():
    for period in ["2018-Q0", "2018-Q5", "2018Q1", "18-Q1", "2018-q1", "2018-Q1 ", ""]:
        with pytest.raises(ValueError, match="YYYY-QN"):
            period_end(period)


# ---------- decay ----------

def test_decay_is_one_at_the_snapshot_and_halves_at_exactly_one_half_life():
    last_used = date(2018, 12, 31)

    assert decay(last_used, HALF_LIFE, as_of=last_used) == 1.0
    assert decay(last_used, HALF_LIFE, as_of=last_used + timedelta(days=HALF_LIFE)) == pytest.approx(
        0.5
    )
    assert decay(
        last_used, HALF_LIFE, as_of=last_used + timedelta(days=2 * HALF_LIFE)
    ) == pytest.approx(0.25)


def test_decay_rejects_a_non_positive_half_life():
    for half_life in (0, -540):
        with pytest.raises(ValueError, match="half_life_days must be positive"):
            decay(date(2018, 12, 31), half_life, as_of=CUTOFF)


# ---------- snapshot anchoring ----------

def test_projection_is_anchored_to_the_configured_cutoff_not_the_wall_clock():
    contribs = [_contribution("p1", "2018-Q4", ["Kafka"])]

    caps = build_capabilities(contribs)          # no as_of: must use the configured snapshot

    assert caps[0].last_used == date(2018, 12, 31)
    assert caps[0].decay_score == round(decay(date(2018, 12, 31), HALF_LIFE, as_of=CUTOFF), 4)
    # One day of decay at the 2019-01-01 cutoff. Anchoring to "today" would collapse this
    # to a near-zero score, so the assertion fails the day someone reaches for date.today().
    assert caps[0].decay_score > 0.99
    assert caps == build_capabilities(contribs, as_of=CUTOFF)


def test_default_snapshot_follows_the_configured_holdout_cutoff(monkeypatch):
    monkeypatch.setitem(settings._cfg["dataset"], "holdout_cutoff", "2018-07-01")
    contribs = [_contribution("p1", "2018-Q1", ["Kafka"])]

    caps = build_capabilities(contribs)

    assert stage4.snapshot_date() == date(2018, 7, 1)
    assert caps[0].decay_score == round(
        decay(date(2018, 3, 31), HALF_LIFE, as_of=date(2018, 7, 1)), 4
    )


def test_a_later_snapshot_decays_the_same_evidence_further():
    contribs = [_contribution("p1", "2018-Q4", ["Kafka"])]

    at_cutoff = build_capabilities(contribs, as_of=CUTOFF)
    two_years_on = build_capabilities(contribs, as_of=CUTOFF + timedelta(days=730))

    assert two_years_on[0].last_used == at_cutoff[0].last_used
    assert two_years_on[0].decay_score < at_cutoff[0].decay_score < 1.0


# ---------- snapshot rejection ----------

def test_a_period_ending_on_or_after_the_snapshot_is_rejected():
    contribution = _contribution("p1", "2018-Q4", ["Kafka"])

    with pytest.raises(ValueError, match="2018-Q4.*not wholly before snapshot 2018-12-31"):
        build_capabilities([contribution], as_of=date(2018, 12, 31))   # ends on the snapshot day
    with pytest.raises(ValueError, match="not wholly before"):
        build_capabilities([contribution], as_of=date(2018, 10, 1))    # snapshot mid-quarter
    with pytest.raises(ValueError, match="not wholly before"):
        build_capabilities([_contribution("p1", "2019-Q1", ["Kafka"])], as_of=CUTOFF)

    assert build_capabilities([contribution], as_of=CUTOFF)            # the day after is fine


# ---------- aggregation ----------

def test_evidence_is_aggregated_across_contributions_per_person_kind_and_term():
    contribs = [
        _contribution("p1", "2018-Q1", ["Kafka", "Python"], [("Streaming", "primary")]),
        _contribution("p1", "2018-Q3", ["Kafka"], [("Streaming", "secondary")]),
        _contribution("p2", "2017-Q2", ["Kafka"]),
    ]

    caps = {(c.person_id, c.kind, c.term): c for c in build_capabilities(contribs, as_of=CUTOFF)}

    kafka = caps[("p1", "skill", "Kafka")]
    assert kafka.evidence_count == 2
    assert kafka.contribution_ids == ["p1|PROJ|2018-Q1|0", "p1|PROJ|2018-Q3|0"]
    assert kafka.last_used == date(2018, 9, 30)                  # max, not first or last seen
    assert caps[("p1", "skill", "Python")].evidence_count == 1
    assert caps[("p1", "skill", "Python")].last_used == date(2018, 3, 31)
    # a term seen as both kinds stays two edges; strength is not an aggregation input
    assert caps[("p1", "specialization", "Streaming")].evidence_count == 2
    assert caps[("p2", "skill", "Kafka")].contribution_ids == ["p2|PROJ|2017-Q2|0"]
    assert kafka.decay_score > caps[("p2", "skill", "Kafka")].decay_score


def test_a_repeated_mention_within_one_contribution_counts_once():
    contribs = [_contribution("p1", "2018-Q1", ["Kafka", "Kafka"])]

    caps = build_capabilities(contribs, as_of=CUTOFF)

    assert [(cap.evidence_count, cap.contribution_ids) for cap in caps] == [
        (1, ["p1|PROJ|2018-Q1|0"])
    ]


def test_contributions_without_terms_produce_no_edges():
    assert build_capabilities([_contribution("p1", "2018-Q1", [])], as_of=CUTOFF) == []
    assert build_capabilities([], as_of=CUTOFF) == []


# ---------- determinism ----------

def test_rows_are_sorted_by_person_then_kind_then_term():
    contribs = [
        _contribution("p2", "2018-Q1", ["Zookeeper", "Ansible"], [("Streaming", "primary")]),
        _contribution("p1", "2018-Q2", ["Kafka"], [("Storage", "primary")]),
    ]

    caps = build_capabilities(contribs, as_of=CUTOFF)

    assert [(cap.person_id, cap.kind, cap.term) for cap in caps] == [
        ("p1", "skill", "Kafka"),
        ("p1", "specialization", "Storage"),
        ("p2", "skill", "Ansible"),
        ("p2", "skill", "Zookeeper"),
        ("p2", "specialization", "Streaming"),
    ]


def _corpus() -> list[Contribution]:
    return [
        _contribution("p1", "2018-Q3", ["Kafka", "Python"], [("Streaming", "primary")]),
        _contribution("p1", "2018-Q1", ["Kafka"], [("Storage", "secondary")]),
        _contribution("p2", "2017-Q4", ["Ansible", "Kafka"], [("Streaming", "primary")]),
        _contribution("p2", "2016-Q1", ["Zookeeper"]),
    ]


def test_main_writes_byte_identical_output_on_re_run(stage4_paths):
    _write_normalized(stage4_paths["NORM_PATH"], _corpus())

    stage4.main()
    first = stage4_paths["CAPS_PATH"].read_bytes()
    stage4.main()

    assert stage4_paths["CAPS_PATH"].read_bytes() == first


def test_output_does_not_depend_on_normalized_line_order(stage4_paths):
    corpus = _corpus()
    _write_normalized(stage4_paths["NORM_PATH"], corpus)
    stage4.main()
    expected = stage4_paths["CAPS_PATH"].read_bytes()

    _write_normalized(stage4_paths["NORM_PATH"], list(reversed(corpus)))
    stage4.main()

    assert stage4_paths["CAPS_PATH"].read_bytes() == expected


def test_written_rows_validate_and_carry_pre_snapshot_evidence(stage4_paths):
    _write_normalized(stage4_paths["NORM_PATH"], _corpus())

    stage4.main()

    rows = [
        PersonCapability.model_validate_json(line)
        for line in stage4_paths["CAPS_PATH"].read_text(encoding="utf-8").splitlines()
    ]
    contribution_ids = {c.contribution_id for c in _corpus()}
    assert rows == build_capabilities(_corpus(), as_of=CUTOFF)
    assert all(cap.last_used < CUTOFF for cap in rows)
    assert all(0.0 < cap.decay_score <= 1.0 for cap in rows)
    assert all(set(cap.contribution_ids) <= contribution_ids for cap in rows)
    assert all(cap.evidence_count == len(cap.contribution_ids) for cap in rows)


def test_summary_reports_edge_kinds_people_and_decay_spread():
    caps = build_capabilities(_corpus(), as_of=CUTOFF)

    summary = stage4.summarize(caps, as_of=CUTOFF)

    assert "snapshot (as_of): 2019-01-01" in summary
    assert f"edges: {len(caps)}" in summary
    assert "people covered: 2" in summary
    assert "decay_score: min" in summary
    assert "last_used: 2016-03-31 .. 2018-09-30" in summary
