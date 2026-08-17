"""The wave-1 improvement flags: off by default, and each one correct when switched on.

Two things are being pinned here, and the first matters more than the second.

The first is that **off is genuinely off**. Every flag in this order changes ranking,
retrieval or vocabulary behaviour that the retired test split can no longer validate, so
the acceptance criterion is that a default run is indistinguishable from the run that
produced the frozen benchmark — down to the configuration digest, which is what decides
whether a frozen checkpoint may still be read and extended.

The second is that each flag does what it claims when it is on, on toy data small enough
to check by hand.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from capgraph import improvements
from capgraph.eval import wave1
from capgraph.models import (
    CandidateProfile,
    Contribution,
    PersonCapability,
    RankedPerson,
    RoleSpec,
    SkillRef,
    SpecializationRef,
)
from capgraph.pipeline import stage3_normalize as stage3
from capgraph.pipeline.stage0_load import strip_markup, truncate_at_boundary
from capgraph.pipeline.stage4_project import build_capabilities
from capgraph.query import rank
from capgraph.query.retrieve import TermResolution

ROLE = RoleSpec(role="platform engineer", specializations=["Streaming"], skills=["Kafka"])
RESOLUTION = TermResolution(
    specializations={"streaming": ["Streaming"]}, skills={"kafka": ["Kafka"]}
)


def cap(term, kind, *, count=4, decay=0.8, primary=0, person="p1"):
    return PersonCapability(
        person_id=person, term=term, kind=kind, evidence_count=count,
        contribution_ids=[], last_used=date(2018, 12, 31), decay_score=decay,
        primary_evidence_count=primary,
    )


def contribution(cid, *, confidence="high", person="p1", skills=("Kafka",),
                 specializations=("Streaming",)):
    return Contribution(
        contribution_id=cid, person_id=person, project_key="MESOS", period="2018-Q4",
        contribution_summary=f"summary {cid}",
        specializations=[SpecializationRef(name=s) for s in specializations],
        skills=[SkillRef(name=s) for s in skills],
        confidence=confidence, reason="", evidence_ticket_keys=["MESOS-1"],
    )


def candidate(**kwargs):
    kwargs.setdefault("specializations", [cap("Streaming", "specialization", primary=4)])
    kwargs.setdefault("skills", [cap("Kafka", "skill")])
    kwargs.setdefault("contributions", [contribution("c1")])
    return CandidateProfile(person_id="p1", person_name="Person p1", **kwargs)


def scored(**kwargs):
    return rank.score_candidate(candidate(**kwargs), ROLE, RESOLUTION)


# ---------- off is off ----------

def test_every_flag_defaults_off_and_records_nothing():
    assert not improvements.any_enabled()
    assert improvements.enabled() == {}
    config = {"retrieval": {"vector_top_k": 40}}
    assert improvements.record(dict(config)) == config


def test_the_run_configuration_digest_is_unchanged_while_flags_are_off():
    from capgraph.eval.run_eval import config_digest, run_config

    assert "improvements" not in run_config()
    baseline = config_digest()
    with improvements.overridden({improvements.FLAG_ORDER: improvements.ORDER_REVERSE}):
        assert "improvements" in run_config()
        assert config_digest() != baseline
    assert config_digest() == baseline


def test_the_score_component_sidecar_is_unchanged_while_flags_are_off():
    from capgraph.eval.scores import retrieval_config

    assert "improvements" not in retrieval_config()


def test_an_override_is_restored_and_an_unknown_flag_is_refused():
    with pytest.raises(KeyError):
        with improvements.overridden({"improvements.not_a_flag": 1}):
            pass
    with improvements.overridden({improvements.FLAG_CONFIDENCE: improvements.COMPONENT}):
        assert improvements.confidence_mode() == improvements.COMPONENT
    assert improvements.confidence_mode() == improvements.OFF


def test_a_mode_outside_its_allowed_set_is_refused():
    with improvements.overridden({improvements.FLAG_CONFIDENCE: "sometimes"}):
        with pytest.raises(ValueError, match="confidence_signal.mode"):
            improvements.confidence_mode()


# ---------- G1: sentence-boundary truncation ----------

def test_short_text_is_returned_unchanged():
    assert truncate_at_boundary("One sentence.", 100) == "One sentence."


def test_truncation_prefers_the_last_sentence_end_inside_the_budget():
    text_ = "First sentence here. Second sentence here. Third runs past the budget."
    assert truncate_at_boundary(text_, 45) == "First sentence here. Second sentence here."


def test_truncation_falls_back_to_a_word_boundary_and_never_splits_a_word():
    text_ = "no sentence terminator anywhere in this particular string of words"
    cut = truncate_at_boundary(text_, 30)
    assert cut == "no sentence terminator"
    assert not text_[len(cut):len(cut) + 1].strip() or text_.startswith(cut + " ")


def test_a_budget_landing_on_a_space_keeps_the_whole_head():
    # Regression: the v1 rule's output is already boundary-correct here, so the new rule
    # must not give characters away for nothing.
    assert strip_markup("<p>See [the docs|https://example.test] {code}x = 1{code}</p>", 14) == (
        "See the docs x"
    )


def test_one_enormous_sentence_loses_characters_rather_than_the_description():
    # The sentence end sits at 5 characters, far below the keep fraction, so taking it
    # would throw away almost everything; the word-boundary fallback is used instead.
    text_ = "Hi. " + "word " * 40
    cut = truncate_at_boundary(text_, 60)
    assert len(cut) > 0.6 * 60
    assert cut.endswith("word")


# ---------- G3a: vocabulary frequency gating ----------

def test_document_frequency_counts_contributions_not_mentions():
    mapping = {"kafka": "Kafka", "kafka streams": "Kafka", "helm": "Helm"}
    counts = stage3.Counter({"kafka": 3, "kafka streams": 2, "helm": 1})
    documents = [["kafka", "kafka streams"], ["kafka"], ["helm"]]
    exact = stage3.canonical_document_frequency(mapping, counts, documents)
    assert exact == stage3.Counter({"Kafka": 2, "Helm": 1})
    # Without the per-contribution lists the mentions are summed, which over-counts the
    # contribution that named two members of one cluster.
    assert stage3.canonical_document_frequency(mapping, counts)["Kafka"] == 5


def test_a_floor_of_one_or_zero_leaves_the_mapping_untouched():
    mapping = {"kafka": "Kafka", "helm": "Helm"}
    frequencies = stage3.Counter({"Kafka": 9, "Helm": 1})
    for floor in (0, 1):
        gated, demoted = stage3.apply_frequency_gate(
            dict(mapping), unique=["Helm", "Kafka"],
            vectors=np.eye(2), frequencies=frequencies, floor=floor,
        )
        assert (gated, demoted) == (mapping, 0)


def test_a_thin_canonical_becomes_an_alias_of_its_nearest_survivor():
    unique = ["Kafka", "Kafka streaming", "Helm"]
    # Kafka and "Kafka streaming" point the same way; Helm is orthogonal to both.
    vectors = np.array([[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]])
    mapping = {
        "Kafka": "Kafka", "Kafka streaming": "Kafka streaming", "Helm": "Helm",
    }
    frequencies = stage3.Counter({"Kafka": 12, "Kafka streaming": 1, "Helm": 8})

    gated, demoted = stage3.apply_frequency_gate(
        mapping, unique=unique, vectors=vectors, frequencies=frequencies, floor=3
    )

    assert demoted == 1
    assert gated["Kafka streaming"] == "Kafka"      # attached, not deleted
    assert gated["Kafka"] == "Kafka" and gated["Helm"] == "Helm"


def test_an_operator_forced_canonical_survives_the_gate():
    unique = ["Kafka", "k8s"]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]])
    mapping = {"Kafka": "Kafka", "k8s": "Kubernetes"}
    frequencies = stage3.Counter({"Kafka": 12, "Kubernetes": 1})

    gated, demoted = stage3.apply_frequency_gate(
        mapping, unique=unique, vectors=vectors, frequencies=frequencies, floor=5,
        protected=frozenset({"Kubernetes"}),
    )

    assert (gated, demoted) == (mapping, 0)


def test_a_floor_nothing_reaches_is_refused_rather_than_emptying_the_vocabulary():
    with pytest.raises(ValueError, match="no canonical term reaches"):
        stage3.apply_frequency_gate(
            {"Kafka": "Kafka"}, unique=["Kafka"], vectors=np.eye(1),
            frequencies=stage3.Counter({"Kafka": 2}), floor=50,
        )


# ---------- G5: extraction confidence ----------

def test_confidence_is_not_a_component_while_the_flag_is_off():
    assert "confidence" not in scored().score_parts
    assert improvements.CONFIDENCE_COMPONENT not in rank.weights()


def test_the_confidence_signal_reads_only_the_evidence_behind_the_match():
    profile = candidate(
        contributions=[
            contribution("c1", confidence="low"),
            contribution("c2", confidence="high", skills=(), specializations=("Unrelated",)),
        ]
    )
    rank.score_candidate(profile, ROLE, RESOLUTION)
    # Only c1 demonstrates a matched term, so the unrelated high-confidence record must
    # not lift the signal.
    assert rank.confidence_signal(profile) == pytest.approx(
        improvements.confidence_value("low")
    )


def test_confidence_as_a_component_enters_the_score_with_its_own_weight():
    with improvements.overridden({improvements.FLAG_CONFIDENCE: improvements.COMPONENT}):
        high = scored(contributions=[contribution("c1", confidence="high")])
        low = scored(contributions=[contribution("c1", confidence="low")])
        assert high.score_parts["confidence"] == pytest.approx(1.0)
        assert low.score_parts["confidence"] == pytest.approx(0.3)
        assert high.score > low.score


def test_confidence_as_a_multiplier_discounts_evidence_strength_instead():
    baseline = scored(contributions=[contribution("c1", confidence="low")])
    with improvements.overridden({improvements.FLAG_CONFIDENCE: improvements.MULTIPLIER}):
        discounted = scored(contributions=[contribution("c1", confidence="low")])
    assert "confidence" not in discounted.score_parts
    assert discounted.score_parts["evidence_strength"] == pytest.approx(
        baseline.score_parts["evidence_strength"] * improvements.confidence_value("low"),
        abs=1e-4,
    )


def test_a_candidate_with_no_contributions_drops_the_component_rather_than_scoring_zero():
    with improvements.overridden({improvements.FLAG_CONFIDENCE: improvements.COMPONENT}):
        profile = scored(contributions=[])
    assert "confidence" not in profile.score_parts


# ---------- G6: primary/secondary specialization strength ----------

def test_stage4_counts_primary_evidence_per_specialization_edge():
    contribs = [
        Contribution(
            contribution_id="p1|MESOS|2018-Q1|0", person_id="p1", project_key="MESOS",
            period="2018-Q1", contribution_summary="s",
            specializations=[SpecializationRef(name="Streaming", strength="primary")],
            skills=[SkillRef(name="Kafka")], confidence="high", reason="",
            evidence_ticket_keys=["MESOS-1"],
        ),
        Contribution(
            contribution_id="p1|MESOS|2018-Q2|0", person_id="p1", project_key="MESOS",
            period="2018-Q2", contribution_summary="s",
            specializations=[SpecializationRef(name="Streaming", strength="secondary")],
            skills=[SkillRef(name="Kafka")], confidence="high", reason="",
            evidence_ticket_keys=["MESOS-2"],
        ),
    ]
    caps = {(c.kind, c.term): c for c in build_capabilities(contribs, as_of=date(2019, 1, 1))}

    specialization = caps[("specialization", "Streaming")]
    assert (specialization.evidence_count, specialization.primary_evidence_count) == (2, 1)
    assert specialization.primary_share == pytest.approx(0.5)
    # A skill carries no strength label, so its share is 0 and G6 never reads it.
    assert caps[("skill", "Kafka")].primary_evidence_count == 0
    assert caps[("skill", "Kafka")].primary_share == 0.0


def test_specialization_match_is_unweighted_while_the_flag_is_off():
    secondary = scored(specializations=[cap("Streaming", "specialization", primary=0)])
    primary = scored(specializations=[cap("Streaming", "specialization", primary=4)])
    assert secondary.score_parts["specialization_match"] == 1.0
    assert primary.score_parts["specialization_match"] == 1.0


def test_a_secondary_specialization_earns_less_credit_when_the_flag_is_on():
    with improvements.overridden({improvements.FLAG_STRENGTH: True}):
        secondary = scored(specializations=[cap("Streaming", "specialization", primary=0)])
        half = scored(specializations=[cap("Streaming", "specialization", count=4, primary=2)])
        primary = scored(specializations=[cap("Streaming", "specialization", primary=4)])
    assert primary.score_parts["specialization_match"] == pytest.approx(1.0)
    assert secondary.score_parts["specialization_match"] == pytest.approx(
        improvements.secondary_weight()
    )
    assert half.score_parts["specialization_match"] == pytest.approx(
        improvements.strength_credit(0.5)
    )
    assert primary.score > half.score > secondary.score


# ---------- G11a: activity currency ----------

def test_activity_currency_is_absent_while_the_flag_is_off():
    assert improvements.ACTIVITY_COMPONENT not in scored().score_parts


def test_activity_currency_enters_the_score_when_the_flag_is_on():
    with improvements.overridden({improvements.FLAG_ACTIVITY: improvements.COMPONENT}):
        current = scored(activity_currency=0.9)
        departed = scored(activity_currency=0.05)
        assert current.score_parts[improvements.ACTIVITY_COMPONENT] == pytest.approx(0.9)
        assert current.score > departed.score


def test_the_activity_distribution_is_measured_at_a_fixed_snapshot():
    contribs = [
        contribution("a", person="p1"),                       # 2018-Q4
        Contribution(
            contribution_id="b", person_id="p2", project_key="MESOS", period="2015-Q1",
            contribution_summary="s", specializations=[], skills=[], confidence="high",
            reason="", evidence_ticket_keys=["MESOS-9"],
        ),
    ]
    latest = wave1.last_activity(contribs)
    assert latest["p1"] == date(2018, 12, 31)
    assert latest["p2"] == date(2015, 3, 31)

    measured = wave1.measure_activity(contribs, as_of=date(2019, 1, 1))
    assert measured.people == 2
    assert measured.stale_beyond_12_quarters == 1


# ---------- G7: re-rank presentation order ----------

def test_a_single_sample_keeps_score_order_by_default():
    assert rank.sample_orders(["a", "b", "c"], 1) == [["a", "b", "c"]]


def test_the_probe_flag_reverses_that_single_sample_only():
    with improvements.overridden({improvements.FLAG_ORDER: improvements.ORDER_REVERSE}):
        assert rank.sample_orders(["a", "b", "c"], 1) == [["c", "b", "a"]]
        # Self-consistency already shuffles, so there is no presentation order to
        # reverse and the arm must be left alone.
        shuffled = rank.sample_orders(["a", "b", "c"], 3)
    assert shuffled == rank.sample_orders(["a", "b", "c"], 3)


# ---------- G8: the parsed headcount ----------

def person(person_id):
    return RankedPerson(
        person_id=person_id, person_name=f"Person {person_id}", fit="good", reason="r",
        score=0.5, evidence_ticket_keys=["MESOS-1"],
    )


def test_the_top_count_is_proposed_and_the_rest_are_alternates():
    ranking = [person("a"), person("b"), person("c")]
    assert rank.split_by_count(ranking, 2) == (["a", "b"], ["c"])
    assert rank.split_by_count(ranking, 1) == (["a"], ["b", "c"])
    # A count of zero is not a role; the intent prompt defaults it to one.
    assert rank.split_by_count(ranking, 0) == (["a"], ["b", "c"])
    assert rank.split_by_count(ranking, 9) == (["a", "b", "c"], [])


# ---------- the wave-1 measurements themselves ----------

def test_recovered_lines_split_on_the_space_runs_tawos_left_behind():
    assert wave1.segments("A sentence.    Another one.") == ["A sentence.", "Another one."]


def test_pasted_machine_output_reads_as_noise_and_prose_does_not():
    assert wave1.is_noise_line("at org.apache.mesos.Foo(Bar.java:42)")
    assert wave1.is_noise_line("2018-04-01 10:11:12 ERROR something blew up")
    assert not wave1.is_noise_line(
        "The agent fails to recover its containers after a restart, which loses work."
    )
    assert wave1.noise_char_share("Prose here that is long enough.    ") == 0.0


def test_the_code_block_share_counts_what_stage_zero_keeps():
    body = "Some prose. {code:python}x = 1{code} more prose."
    assert 0.0 < wave1.code_char_share(body) < 1.0
    assert wave1.code_char_share("no fences at all") == 0.0
