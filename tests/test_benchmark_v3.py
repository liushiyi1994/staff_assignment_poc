"""Benchmark v3 levers, offline: no model call, no Neo4j, no network.

Each lever is switched on by one setting and off by default, so every test here sets
the setting it is about and asserts two things: that the lever does what it claims, and
that the v1/v2 path is unchanged when it is off. The evidence-citation validator is
exercised through every new code path, because nothing in v3 is allowed to relax it.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from capgraph.eval import labelnoise, paired, run_v3
from capgraph.eval.holdout import BenchmarkManifestEntry
from capgraph.eval.scores import CaseScores, RoleScores
from capgraph.evidence import EvidenceTicket, EvidenceView
from capgraph.lexical import PersonBm25Index, tokenize
from capgraph.models import (
    CandidateProfile,
    Contribution,
    PersonCapability,
    RoleSpec,
    SkillRef,
    SpecializationRef,
)
from capgraph.query import rank, retrieve
from capgraph.settings import settings

ALICE, BOB, CARL = "MESOS:1", "MESOS:2", "MESOS:3"

ROLE = RoleSpec(role="backend engineer", specializations=["Cluster orchestration"],
                skills=["Docker", "Kafka"])
RESOLUTION = retrieve.TermResolution(
    specializations={"cluster orchestration": ["Cluster orchestration"]},
    skills={"docker": ["Docker image build tooling"], "kafka": ["Kafka stream processing"]},
)


def cap(term, kind, count=5, decay=0.9, person=ALICE):
    return PersonCapability(person_id=person, term=term, kind=kind, evidence_count=count,
                            contribution_ids=[], last_used=date(2018, 12, 31),
                            decay_score=decay)


def contribution(cid, person=ALICE, keys=("MESOS-1",), skills=(), specializations=()):
    return Contribution(
        contribution_id=cid, person_id=person, project_key="MESOS", period="2018-Q4",
        contribution_summary=f"summary {cid}",
        specializations=[SpecializationRef(name=s) for s in specializations],
        skills=[SkillRef(name=s) for s in skills],
        confidence="high", reason="", evidence_ticket_keys=list(keys),
    )


def candidate(person=ALICE, **kwargs):
    return CandidateProfile(person_id=person, person_name=f"Person {person}", **kwargs)


@pytest.fixture
def retrieval_settings(monkeypatch):
    """Mutate retrieval settings for one test without leaking into the next."""
    original = dict(settings._cfg["retrieval"])

    def apply(**values):
        for key, value in values.items():
            monkeypatch.setitem(settings._cfg["retrieval"], key, value)

    yield apply
    settings._cfg["retrieval"].clear()
    settings._cfg["retrieval"].update(original)


# ---------- lever 1: the lexical retrieval arm ----------

@pytest.fixture
def view() -> EvidenceView:
    return EvidenceView(tickets=(
        EvidenceTicket(ALICE, ALICE, "MESOS", "Kafka streaming consumer lag on the agent"),
        EvidenceTicket("MESOS-2", ALICE, "MESOS", "More Kafka streaming work"),
        EvidenceTicket("MESOS-3", BOB, "MESOS", "Docker containerizer image pull"),
        EvidenceTicket("MESOS-4", CARL, "MESOS", "Documentation cleanup"),
    ))


def test_the_lexical_arm_is_the_bm25_baselines_ranking_truncated(view):
    index = PersonBm25Index(view)
    everyone = [person for person, _ in index.top_people("Kafka streaming", k=99)]

    top_one = retrieve.lexical_candidates("Kafka streaming", index, top_k=1)

    assert [person for person, _ in top_one] == everyone[:1] == [ALICE]


def test_the_lexical_arm_is_switched_off_by_a_width_of_zero(view):
    assert retrieve.lexical_candidates("Kafka streaming", PersonBm25Index(view), top_k=0) == []


def test_the_lexical_arm_reads_the_configured_width(view, retrieval_settings):
    retrieval_settings(bm25_top_k=2)
    assert len(retrieve.lexical_candidates("Kafka streaming", PersonBm25Index(view))) == 2


def test_the_lexical_arm_is_restricted_to_the_roster(view):
    index = PersonBm25Index(view)
    found = retrieve.lexical_candidates("Kafka streaming", index, roster=[BOB, CARL], top_k=5)
    assert ALICE not in {person for person, _ in found}


def test_the_union_records_which_arm_found_each_person():
    vector = [retrieve.VectorHit(ALICE, f"Person {ALICE}", ("c1",), 0.9)]
    structured = [{"person_id": BOB, "person_name": f"Person {BOB}", "strength": 3.0}]

    profiles = retrieve.union_candidates(
        vector, structured, [(ALICE, 4.0), (CARL, 2.0)],
        names={CARL: f"Person {CARL}"},
    )

    by_id = {profile.person_id: profile for profile in profiles}
    assert by_id[ALICE].retrieval_sources == ["vector", "lexical"]
    assert by_id[BOB].retrieval_sources == ["structured"]
    assert by_id[CARL].retrieval_sources == ["lexical"]
    assert by_id[CARL].person_name == f"Person {CARL}"
    assert by_id[ALICE].lexical_score == 4.0


def test_the_union_is_never_an_intersection():
    """A person only one arm found still reaches the pool. Non-negotiable #3."""
    profiles = retrieve.union_candidates([], [], [(CARL, 1.0)])
    assert [profile.person_id for profile in profiles] == [CARL]


def test_a_lexical_only_candidate_scores_from_its_retained_contributions():
    scored = rank.score_candidate(
        candidate(
            skills=[cap("Mesos agent recovery", "skill", 4, 0.75)],
            contributions=[contribution("c9", skills=["Mesos agent recovery"]),
                           contribution("c8", skills=["Mesos agent recovery"])],
            retrieval_sources=["lexical"],
        ),
        ROLE, RESOLUTION,
    )

    assert scored.score_parts["recency"] == 0.75
    assert scored.score_parts["evidence_strength"] > 0
    # Without the fallback this candidate is a structural zero and can never be ranked.
    assert scored.score > 0


def test_the_profile_fallback_cannot_change_a_v1_or_v2_candidates_score():
    """Every v1/v2 pool member had a matched term or a vector hit; both take priority."""
    matched = candidate(
        specializations=[cap("Cluster orchestration", "specialization", 3, 0.6)],
        contributions=[contribution("c1", specializations=["Cluster orchestration"])],
        retrieval_sources=["structured"],
    )
    vector_only = candidate(
        BOB,
        skills=[cap("Mesos agent recovery", "skill", 4, 0.75, person=BOB)],
        contributions=[contribution("c9", person=BOB, skills=["Mesos agent recovery"]),
                       contribution("c8", person=BOB, skills=["Mesos agent recovery"])],
        vector_hit_contribution_ids=["c9"],
        retrieval_sources=["vector"],
    )

    assert rank.score_candidate(matched, ROLE, RESOLUTION).score_parts["recency"] == 0.6
    parts = rank.score_candidate(vector_only, ROLE, RESOLUTION).score_parts
    # One vector hit, not the two retained contributions the fallback would have used.
    assert parts["evidence_strength"] == round(rank.evidence_strength(1), 4)


# ---------- lever 2: compact candidate cards ----------

def _carded():
    return candidate(
        specializations=[cap(f"spec {i}", "specialization", 30 - i) for i in range(20)],
        skills=[cap(f"skill {i}", "skill", 30 - i) for i in range(40)],
        contributions=[
            contribution(f"c{i}", keys=tuple(f"MESOS-{i * 100 + j}" for j in range(20)))
            for i in range(12)
        ],
    )


def test_a_card_is_bounded_and_carries_no_contribution_summaries():
    profile = _carded()
    profile.score = 0.612

    view = rank.card_view(profile)

    assert len(view["specializations"]) == settings["retrieval.card_specializations_per_candidate"]
    assert len(view["skills"]) == settings["retrieval.card_skills_per_candidate"]
    assert len(view["evidence_tickets"]) == settings["retrieval.card_evidence_keys_per_candidate"]
    assert view["score"] == 0.612
    assert "contributions" not in view
    assert "summary" not in json.dumps(view)
    assert view["skills"][0].startswith("skill 0 (x30, last 2018-12-31)")


def test_a_card_is_much_shorter_than_the_profile_view_it_replaces():
    profile = _carded()
    assert len(json.dumps(rank.card_view(profile))) < len(json.dumps(rank.profile_view(profile)))


def test_every_key_a_card_shows_is_one_the_validator_accepts():
    profile = _carded()
    for key in rank.card_view(profile)["evidence_tickets"]:
        keys, problem = rank.validated_evidence(
            {"reason": "work", "evidence_ticket_keys": [key]}, profile
        )
        assert problem is None and keys == [key]


def test_the_card_view_does_not_widen_what_may_be_cited():
    """The card shows fewer keys; validation still checks against every key owned."""
    profile = _carded()
    _, problem = rank.validated_evidence(
        {"reason": "work", "evidence_ticket_keys": ["MESOS-999999"]}, profile
    )
    assert "cites evidence not in this person's contributions" in problem


def test_the_configured_view_is_what_the_prompt_receives(retrieval_settings):
    profile = _carded()
    retrieval_settings(rerank_candidate_view="card")
    assert rank.candidate_view(profile) == rank.card_view(profile)
    retrieval_settings(rerank_candidate_view="profile")
    assert rank.candidate_view(profile) == rank.profile_view(profile)


def test_an_unknown_candidate_view_is_refused_rather_than_guessed(retrieval_settings):
    retrieval_settings(rerank_candidate_view="postcard")
    with pytest.raises(ValueError, match="rerank_candidate_view"):
        rank.candidate_view(_carded())


# ---------- lever 3: window width ----------

def test_the_window_is_the_configured_width(retrieval_settings):
    candidates = []
    for index in range(40):
        profile = candidate(f"MESOS:{index:02d}")
        profile.score = 1.0 - index / 100
        candidates.append(profile)

    retrieval_settings(rerank_top_k=32)
    assert len(rank.rerank_input(candidates)) == 32
    retrieval_settings(rerank_top_k=15)
    assert len(rank.rerank_input(candidates)) == 15


# ---------- lever 4: permutation self-consistency ----------

def test_one_sample_sends_the_deterministic_order_unchanged():
    ids = [ALICE, BOB, CARL]
    assert rank.sample_orders(ids, 1) == [ids]


def test_several_samples_are_shuffled_permutations_and_reproducible():
    ids = [f"MESOS:{i}" for i in range(12)]

    orders = rank.sample_orders(ids, 3)

    assert len(orders) == 3
    assert all(sorted(order) == sorted(ids) for order in orders)
    assert any(order != ids for order in orders)
    assert orders == rank.sample_orders(ids, 3)          # seeded from the shortlist
    assert orders != rank.sample_orders(list(reversed(ids)), 3)


def test_borda_counts_positions_across_samples():
    shortlist = [candidate(ALICE), candidate(BOB), candidate(CARL)]
    # BOB is second, first, first: 2 + 3 + 3 = 8 against ALICE's 3 + 2 + 1 = 6.
    order = rank.borda_order(
        [[ALICE, BOB, CARL], [BOB, ALICE, CARL], [BOB, CARL, ALICE]], shortlist
    )
    assert order == [BOB, ALICE, CARL]


def test_borda_only_returns_people_at_least_one_sample_ranked():
    """Aggregation must not widen coverage, or the A/B stops comparing orderings."""
    shortlist = [candidate(ALICE), candidate(BOB), candidate(CARL)]
    assert rank.borda_order([[ALICE], [ALICE, BOB]], shortlist) == [ALICE, BOB]


def test_borda_breaks_ties_on_the_deterministic_score():
    first, second = candidate(ALICE), candidate(BOB)
    first.score, second.score = 0.2, 0.8
    assert rank.borda_order([[ALICE, BOB], [BOB, ALICE]], [first, second]) == [BOB, ALICE]


def _sc_shortlist():
    first = candidate(ALICE, contributions=[contribution("c1", keys=("MESOS-1",))],
                      retrieval_sources=["vector"])
    second = candidate(BOB, contributions=[contribution("c2", person=BOB, keys=("MESOS-9",))],
                       retrieval_sources=["structured"])
    return [first, second]


def test_self_consistency_runs_every_sample_and_aggregates_them(monkeypatch, retrieval_settings):
    retrieval_settings(rerank_samples=3)
    answers = [
        {"ranking": [
            {"person_id": BOB, "fit": "good", "reason": "MESOS-9 work",
             "evidence_ticket_keys": ["MESOS-9"]},
            {"person_id": ALICE, "fit": "good", "reason": "MESOS-1 work",
             "evidence_ticket_keys": ["MESOS-1"]},
        ]},
        {"ranking": [
            {"person_id": ALICE, "fit": "good", "reason": "MESOS-1 work",
             "evidence_ticket_keys": ["MESOS-1"]},
            {"person_id": BOB, "fit": "good", "reason": "MESOS-9 work",
             "evidence_ticket_keys": ["MESOS-9"]},
        ]},
        {"ranking": [
            {"person_id": BOB, "fit": "strong", "reason": "MESOS-9 work",
             "evidence_ticket_keys": ["MESOS-9"]},
        ]},
    ]
    calls = []

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        calls.append(purpose)
        return answers[len(calls) - 1]

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    ranking, rejected = rank.rerank("brief", ROLE, _sc_shortlist())

    assert calls == ["rerank"] * 3
    assert [person.person_id for person in ranking] == [BOB, ALICE]
    assert rejected == []
    # Each entry keeps the citations of the first sample that ranked that person.
    assert ranking[0].evidence_ticket_keys == ["MESOS-9"]


def test_a_sample_that_cites_foreign_evidence_is_still_rejected(monkeypatch,
                                                                retrieval_settings):
    retrieval_settings(rerank_samples=2)

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        return {"ranking": [
            {"person_id": ALICE, "fit": "strong", "reason": "Led MESOS-9",
             "evidence_ticket_keys": ["MESOS-1"]},
        ]}

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    ranking, rejected = rank.rerank("brief", ROLE, _sc_shortlist())

    assert ranking == []
    assert len(rejected) == 2
    assert all("cites evidence not in this person's contributions" in item for item in rejected)
    assert rejected[0].endswith(" [sample 0]")
    # run_diagnostics reads the reason class off the front, so the marker must not
    # displace it — an aggregated run has to report the same reason names as a single one.
    assert rejected[0].split(": ", 1)[-1].split(":")[0] == (
        "cites evidence not in this person's contributions"
    )


def test_a_single_sample_keeps_the_v2_call_shape(monkeypatch, retrieval_settings):
    retrieval_settings(rerank_samples=1)
    seen = {}

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        seen.update(model=model, stage=stage, max_tokens=max_tokens, purpose=purpose)
        return {"ranking": [{"person_id": ALICE, "fit": "good", "reason": "MESOS-1",
                             "evidence_ticket_keys": ["MESOS-1"]}]}

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    ranking, rejected = rank.rerank("brief", ROLE, _sc_shortlist())

    assert [person.person_id for person in ranking] == [ALICE]
    assert rejected == []
    assert seen["purpose"] == "rerank"
    assert seen["max_tokens"] == rank.rerank_output_tokens(2)


def test_the_output_allowance_is_sized_from_the_window_and_capped():
    """A wider window answers with more entries; a fixed allowance truncates them."""
    assert rank.rerank_output_tokens(15) == 3000       # exactly the v1/v2 allowance
    assert rank.rerank_output_tokens(32) > rank.rerank_output_tokens(15)
    assert rank.rerank_output_tokens(10_000) == settings["llm.rerank_max_output_tokens"]


def test_a_window_32_card_rerank_fits_the_per_call_cost_ceiling():
    """The order requires this check before the first paid call at the wider window."""
    from capgraph import llm

    profile = _carded()
    profile.score = 0.5
    card = json.dumps(rank.card_view(profile), indent=1)
    # 32 cards, the prompt template, and a brief four times the manifest minimum.
    prompt = card * 32 + "x" * 6_000
    estimate = llm.estimate_call_cost_usd(
        prompt,
        model=str(settings["llm.rerank_model"]),
        max_tokens=rank.rerank_output_tokens(32),
    )
    assert estimate <= float(settings["llm.max_call_cost_usd"])


# ---------- lever 5: the strong-model finisher ----------

def _ranked(person_ids):
    return [
        rank.RankedPerson(person_id=person_id, person_name=f"Person {person_id}",
                          fit="good", reason=f"{person_id} work", score=0.5,
                          evidence_ticket_keys=["MESOS-1"])
        for person_id in person_ids
    ]


def test_the_finisher_is_inert_until_it_is_switched_on(monkeypatch, retrieval_settings):
    retrieval_settings(finisher_top_k=0)
    monkeypatch.setattr(rank, "call_json", lambda *a, **k: pytest.fail("no call expected"))

    ranking = _ranked([ALICE, BOB, CARL])
    assert rank.finish("brief", ROLE, ranking, _sc_shortlist()) == (ranking, [])


def test_the_finisher_reorders_the_head_and_leaves_the_tail_alone(monkeypatch,
                                                                  retrieval_settings):
    retrieval_settings(finisher_top_k=2)
    seen = {}

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        seen.update(model=model, purpose=purpose, max_tokens=max_tokens)
        return {"order": [BOB, ALICE]}

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    ranking, rejected = rank.finish(
        "brief", ROLE, _ranked([ALICE, BOB, CARL]), _sc_shortlist() + [candidate(CARL)]
    )

    assert [person.person_id for person in ranking] == [BOB, ALICE, CARL]
    assert rejected == []
    assert seen["model"] == settings["llm.finisher_model"]
    assert seen["purpose"] == "finish"
    assert seen["max_tokens"] == settings["llm.finisher_max_output_tokens"]


def test_the_finisher_cannot_add_drop_or_duplicate_a_person(monkeypatch, retrieval_settings):
    retrieval_settings(finisher_top_k=3)

    def fake_call_json(prompt, model, stage, max_tokens=None, purpose=None):
        return {"order": [BOB, BOB, "MESOS:9999"]}

    monkeypatch.setattr(rank, "call_json", fake_call_json)
    ranking, rejected = rank.finish(
        "brief", ROLE, _ranked([ALICE, BOB, CARL]),
        _sc_shortlist() + [candidate(CARL)],
    )

    assert [person.person_id for person in ranking] == [BOB, ALICE, CARL]
    assert rejected == ["finisher: MESOS:2 named twice",
                        "finisher: MESOS:9999 is not in the head"]


def test_the_finisher_carries_the_validated_reasons_through_unchanged(monkeypatch,
                                                                      retrieval_settings):
    retrieval_settings(finisher_top_k=2)
    monkeypatch.setattr(rank, "call_json",
                        lambda *a, **k: {"order": [BOB, ALICE], "reason": "ignored"})

    ranking, _ = rank.finish("brief", ROLE, _ranked([ALICE, BOB]), _sc_shortlist())

    assert [person.reason for person in ranking] == [f"{BOB} work", f"{ALICE} work"]
    assert all(person.evidence_ticket_keys == ["MESOS-1"] for person in ranking)


def _maximal_finisher_prompt(brief_chars: int) -> str:
    """The finisher prompt at its worst: five maximal cards and a long brief."""
    from capgraph.settings import load_prompt

    card = json.dumps(
        {
            "person_id": "PROJECT:123456",
            "person_name": "Person PROJECT-123456",
            "score": 0.6123,
            "found_by": ["vector", "structured", "lexical"],
            "specializations": ["a fairly long specialization term (x99, last 2018-12-31)"]
            * int(settings["retrieval.card_specializations_per_candidate"]),
            "skills": ["a fairly long skill term written out (x99, last 2018-12-31)"]
            * int(settings["retrieval.card_skills_per_candidate"]),
            "evidence_tickets": ["PROJECT-123456"]
            * int(settings["retrieval.card_evidence_keys_per_candidate"]),
        },
        indent=1,
    )
    role = json.dumps({"role": "x" * 80, "specializations": ["y" * 40] * 6,
                       "skills": ["z" * 40] * 12, "count": 3})
    return load_prompt(
        "rerank_finisher", brief="b" * brief_chars, role_json=role,
        candidates_json="[" + ",".join([card] * 5) + "]",
    )


def test_a_finisher_call_fits_the_per_call_cost_ceiling():
    """The order requires this check before the first paid call is ever sent.

    The bound that matters is the manifest's own worst case: sol bills output at
    $30/MTok, so the allowance and the ceiling are only a factor of two apart and the
    margin has to be measured rather than assumed. The longest selected brief is 1,337
    characters (MESOS-9590); 2,000 is comfortably above it.
    """
    from capgraph import llm

    model = str(settings["llm.finisher_model"])
    assert llm.provider_for_model(model) == "openrouter"
    assert llm.model_price_usd_per_mtok(model) == (5.0, 30.0)

    estimate = llm.estimate_call_cost_usd(
        _maximal_finisher_prompt(2_000),
        model=model,
        max_tokens=int(settings["llm.finisher_max_output_tokens"]),
    )
    assert estimate <= float(settings["llm.max_call_cost_usd"])


def test_an_oversized_finisher_call_is_refused_rather_than_sent():
    """The margin is thin by design, so the guard — not luck — is what bounds spend."""
    from capgraph import llm

    with pytest.raises(llm.CallCostCeilingError):
        llm.enforce_call_budget(
            llm.estimate_call_cost_usd(
                _maximal_finisher_prompt(40_000),
                model=str(settings["llm.finisher_model"]),
                max_tokens=int(settings["llm.finisher_max_output_tokens"]),
            ),
            stage="test-only",
            model=str(settings["llm.finisher_model"]),
        )


# ---------- offline pool analysis ----------

def _case_scores(sources):
    parts = {person: {"recency": 0.5, "evidence_strength": 0.5} for person in sources}
    return CaseScores(
        issue_id="1", issue_key="MESOS-1", project_key="MESOS", truth=frozenset({CARL}),
        roles=(RoleScores(role="engineer", parts=parts, sources=dict(sources)),),
    )


def test_dropping_an_arm_removes_only_the_people_it_alone_found():
    case = _case_scores({ALICE: ["vector", "lexical"], BOB: ["structured"], CARL: ["lexical"]})

    assert case.pool() == {ALICE, BOB, CARL}
    assert case.without_arm("lexical").pool() == {ALICE, BOB}


def test_a_v2_score_checkpoint_without_sources_still_loads():
    record = {"issue_id": "1", "issue_key": "MESOS-1", "project_key": "MESOS",
              "truth_person_ids": [ALICE],
              "roles": [{"role": "engineer", "parts": {ALICE: {"recency": 0.5}}}]}
    case = CaseScores.from_json(record)
    assert case.roles[0].sources == {}


# ---------- paired per-query statistics ----------

@pytest.mark.parametrize(
    ("wins", "losses", "expected"),
    [(0, 0, 1.0), (5, 5, 1.0), (10, 0, 2 / 2**10), (0, 3, 0.25)],
)
def test_mcnemar_exact_p_is_the_two_sided_binomial_tail(wins, losses, expected):
    assert paired.mcnemar_exact_p(wins, losses) == pytest.approx(expected)


def test_paired_binary_counts_wins_and_losses_case_by_case():
    row = paired.paired_binary("Hit@1", {"a": 1, "b": 0, "c": 0}, {"a": 0, "b": 1, "c": 0})

    assert (row.wins, row.losses, row.ties) == (1, 1, 1)
    assert row.delta == pytest.approx(0.0)
    assert row.p_value == pytest.approx(1.0)


def test_paired_statistics_use_only_the_cases_both_arms_scored():
    row = paired.paired_binary("Hit@1", {"a": 1, "b": 1}, {"a": 0})
    assert row.n == 1


def test_the_bootstrap_interval_is_seeded_and_reproducible():
    before = {f"c{i}": 0.2 for i in range(30)}
    after = {f"c{i}": 0.5 if i % 2 else 0.1 for i in range(30)}

    first = paired.paired_bootstrap("MRR", before, after, resamples=500)
    second = paired.paired_bootstrap("MRR", before, after, resamples=500)

    assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)
    assert first.ci_low <= first.mean_delta <= first.ci_high
    assert (first.better, first.worse) == (15, 15)


# ---------- label-noise audit ----------

def _entry(issue_id, truth, roster=(ALICE, BOB, CARL)):
    return BenchmarkManifestEntry(
        seed=1, issue_id=issue_id, issue_key=f"MESOS-{issue_id}", query_text="x" * 400,
        as_of_time=datetime(2019, 2, 1), project_key="MESOS",
        eligible_roster=list(roster), truth_person_ids=[truth], split="test",
    )


def test_the_evidence_class_is_read_out_of_the_provenance_string():
    assert labelnoise._evidence_class(
        "final_assignment_from_change_log;evidence=change_log_to_at_resolution"
    ) == labelnoise.CHANGE_LOG_LABEL
    assert labelnoise._evidence_class("something_else") == "something_else"


def test_a_reassignment_is_what_the_audit_calls_label_noise():
    audit = labelnoise.LabelAudit(
        issue_id="1", issue_key="MESOS-1", project_key="MESOS",
        truth_person_ids=(ALICE,), snapshot_person_id=BOB,
        provenance="", evidence_class=labelnoise.CHANGE_LOG_LABEL,
    )
    assert audit.reassigned
    assert audit.corroborated
    assert audit.accepted_ids([ALICE, BOB]) == {ALICE, BOB}
    # A snapshot assignee off the roster cannot be ranked, so it is not accepted.
    assert audit.accepted_ids([ALICE]) == {ALICE}


def test_the_audit_reports_metrics_separately_for_each_label_class():
    cases = [_entry("1", ALICE), _entry("2", BOB)]
    audits = {
        "1": labelnoise.LabelAudit("1", "MESOS-1", "MESOS", (ALICE,), ALICE, "",
                                   labelnoise.CHANGE_LOG_LABEL),
        "2": labelnoise.LabelAudit("2", "MESOS-2", "MESOS", (BOB,), BOB, "",
                                   labelnoise.SNAPSHOT_LABEL),
    }
    per_case = {
        "1": {"hit_at_1": 1.0, "hit_at_5": 1.0, "hit_at_10": 1.0, "mrr": 1.0},
        "2": {"hit_at_1": 0.0, "hit_at_5": 0.0, "hit_at_10": 1.0, "mrr": 0.1},
    }

    summary = labelnoise.summarize(cases, audits, per_case)

    assert (summary.n, summary.reassigned) == (2, 0)
    assert (summary.corroborated, summary.uncorroborated) == (1, 1)
    assert summary.by_class[labelnoise.CHANGE_LOG_LABEL]["hit_at_1"] == 1.0
    assert summary.by_class[labelnoise.SNAPSHOT_LABEL]["hit_at_1"] == 0.0
    assert summary.misses_by_class == {labelnoise.SNAPSHOT_LABEL: 1}


# ---------- spend control ----------

def test_the_projection_scales_with_the_levers_that_are_switched_on(retrieval_settings):
    projection = dict(settings["eval.v3.projection"])
    retrieval_settings(rerank_candidate_view="card", rerank_samples=1, finisher_top_k=0)
    single = run_v3.project_case_cost()

    retrieval_settings(rerank_samples=3)
    assert run_v3.project_case_cost() == pytest.approx(
        single + 2 * projection["roles_per_case"] * projection["rerank_card_call_usd"]
    )

    retrieval_settings(rerank_samples=1, finisher_top_k=5)
    assert run_v3.project_case_cost() == pytest.approx(
        single + projection["roles_per_case"] * projection["finisher_call_usd"]
    )


def test_a_split_over_the_authorized_ceiling_is_refused_before_it_runs(monkeypatch):
    monkeypatch.setattr(run_v3, "spend_by_stage", lambda stages: [(s, 0, 0.0) for s in stages])
    ceiling = float(settings["eval.v3.max_total_cost_usd"])

    run_v3.enforce_v3_budget(1)
    with pytest.raises(run_v3.V3BudgetError, match="escalate to the orchestrator"):
        run_v3.enforce_v3_budget(int(ceiling / run_v3.project_case_cost()) + 10)


def test_rejections_are_counted_against_the_entries_actually_offered(tmp_path):
    """A wider window offers more chances to mis-cite, so the raw count is not the rate."""
    path = tmp_path / "test.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"system": "capgraph_full", "issue_id": "1", "ranked_ids": [],
                 "detail": {"rejected": ["a", "b"],
                            "candidate_counts": [{"reranked": 32}]}},
                {"system": "capgraph_full", "issue_id": "2", "ranked_ids": [],
                 "detail": {"rejected": [],
                            "candidate_counts": [{"reranked": 15}, {"reranked": 15}]}},
                # A record with no diagnostics contributes neither count.
                {"system": "capgraph_full", "issue_id": "3", "ranked_ids": []},
                {"system": "bm25", "issue_id": "1", "ranked_ids": []},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert run_v3.rerank_validity("test", runs_dir_=tmp_path) == (2, 62)


def test_the_arms_verdicts_are_read_off_the_run_plan(monkeypatch):
    """The arms are cumulative, so the frozen one is the cut between kept and rejected."""
    monkeypatch.setitem(settings._cfg["eval"]["v3"], "ab_arms", ["a", "b", "c", "d"])
    monkeypatch.setitem(settings._cfg["eval"]["v3"], "frozen_validation_variant", "b")

    assert run_v3.arm_verdicts() == {
        "a": "in the adopted configuration",
        "b": "adopted",
        "c": "measured, not adopted",
        "d": "measured, not adopted",
    }


def test_the_finisher_is_absent_from_the_configuration_digest_until_it_is_used(
    retrieval_settings,
):
    retrieval_settings(finisher_top_k=0)
    assert "finisher_model" not in run_v3.v3_config("test")
    retrieval_settings(finisher_top_k=5)
    assert run_v3.v3_config("test")["finisher_model"] == settings["llm.finisher_model"]


# ---------- report writing: three sections, three owners ----------

V1 = "# Temporal benchmark results\n\nv1 body\n"
V2 = "# Benchmark v2\n\nv2 body\n"


def test_the_v3_writer_leaves_the_v1_and_v2_sections_byte_identical(tmp_path):
    from capgraph.eval.run_eval import V2_MARKER, V3_MARKER

    report = tmp_path / "eval-results.md"
    report.write_text(f"{V1}\n{V2_MARKER}\n\n{V2}", encoding="utf-8")
    before = report.read_text(encoding="utf-8")

    run_v3.write_tracked_section("# Benchmark v3\n\nv3 body\n", path=report)

    written = report.read_text(encoding="utf-8")
    assert written.startswith(before.rstrip("\n"))
    assert V3_MARKER in written
    assert written.endswith("# Benchmark v3\n\nv3 body\n")


def test_rewriting_the_v3_section_replaces_only_itself(tmp_path):
    from capgraph.eval.run_eval import V2_MARKER

    report = tmp_path / "eval-results.md"
    report.write_text(f"{V1}\n{V2_MARKER}\n\n{V2}", encoding="utf-8")

    run_v3.write_tracked_section("# Benchmark v3\n\nfirst\n", path=report)
    run_v3.write_tracked_section("# Benchmark v3\n\nsecond\n", path=report)

    written = report.read_text(encoding="utf-8")
    assert "first" not in written
    assert written.count("# Benchmark v3") == 1
    assert V2 in written


def test_the_v2_writer_no_longer_destroys_a_v3_section(tmp_path):
    from capgraph.eval import run_v2
    from capgraph.eval.run_eval import V2_MARKER, V3_MARKER

    report = tmp_path / "eval-results.md"
    report.write_text(
        f"{V1}\n{V2_MARKER}\n\n{V2}\n{V3_MARKER}\n\n# Benchmark v3\n\nv3 body\n",
        encoding="utf-8",
    )

    run_v2.write_tracked_section("# Benchmark v2\n\nrewritten\n", path=report)

    written = report.read_text(encoding="utf-8")
    assert "rewritten" in written
    assert written.endswith("# Benchmark v3\n\nv3 body\n")


# ---------- shared lexical foundation ----------

def test_the_engine_arm_and_the_baseline_share_one_tokenizer():
    from capgraph.eval import baselines

    assert baselines.Bm25Baseline.__init__.__doc__ is None      # thin wrapper, no re-impl
    assert tokenize("Node.js and C++") == ["node.js", "and", "c++"]
