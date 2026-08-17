"""The three baselines and the evidence view they share. Offline: no model, no Neo4j.

What these pin is not "does BM25 work" — that is rank_bm25's problem — but the four
properties that make a baseline a fair control: it sees only the pre-cutoff evidence
view, it ranks the whole frozen roster and nobody else, it is deterministic, and it
ranks the person the brief actually describes above the person it does not.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pytest

from capgraph.eval.baselines import (
    BASELINE_SYSTEMS,
    Bm25Baseline,
    MostActiveBaseline,
    VectorBaseline,
    build_baselines,
)
from capgraph.eval.contracts import BenchmarkQueryContext
from capgraph.evidence import EvidenceView, ticket_text
from capgraph.lexical import tokenize

ALICE, BOB, CARL, OUTSIDER = "ALPHA:1", "ALPHA:2", "ALPHA:3", "BETA:9"


def _ticket(key: str, summary: str, description: str = "") -> dict:
    return {"key": key, "summary": summary, "description": description}


def _bucket(person_id: str, project: str, period: str, tickets: list[dict]) -> dict:
    return {
        "bucket_id": f"{person_id}|{project}|{period}|0",
        "person_id": person_id,
        "person_name": f"Person {person_id.replace(':', '-')}",
        "project_key": project,
        "project_domain": "test domain",
        "period": period,
        "tickets": tickets,
    }


@pytest.fixture
def view(tmp_path) -> EvidenceView:
    buckets = [
        _bucket(ALICE, "ALPHA", "2018-Q1", [
            _ticket("ALPHA-1", "Kafka streaming consumer lag", "Fix consumer group rebalance"),
            _ticket("ALPHA-2", "Kafka broker partition rebalance", ""),
        ]),
        _bucket(BOB, "ALPHA", "2018-Q2", [
            _ticket("ALPHA-3", "CSS layout of the settings page", "Padding is wrong"),
            _ticket("ALPHA-4", "Button colour regression", ""),
            _ticket("ALPHA-5", "Icon alignment", ""),
        ]),
        _bucket(CARL, "ALPHA", "2018-Q3", [
            _ticket("ALPHA-6", "Documentation typo", ""),
        ]),
        _bucket(OUTSIDER, "BETA", "2018-Q1", [
            _ticket("BETA-1", "Kafka streaming consumer lag in the other project", ""),
        ]),
    ]
    path = tmp_path / "buckets.jsonl"
    path.write_text("\n".join(json.dumps(bucket) for bucket in buckets) + "\n")
    return EvidenceView.load(path)


def _context(query: str = "Kafka streaming consumer lag", roster=(ALICE, BOB, CARL)):
    return BenchmarkQueryContext(
        issue_id="1",
        query_text=query,
        as_of_time=datetime(2019, 6, 1),
        project_key="ALPHA",
        eligible_roster=tuple(roster),
    )


# ---------- the evidence view ----------

def test_ticket_text_joins_summary_and_description_in_a_fixed_order():
    assert ticket_text({"summary": "S", "description": "D"}) == "S\nD"
    assert ticket_text({"summary": "S", "description": None}) == "S"
    assert ticket_text({"summary": "", "description": ""}) == ""


def test_the_view_attributes_tickets_to_their_resolution_time_owner(view):
    assert view.ticket_counts == {ALICE: 2, BOB: 3, CARL: 1, OUTSIDER: 1}
    assert view.person_ids == (ALICE, BOB, CARL, OUTSIDER)


def test_the_view_orders_tickets_independently_of_bucket_order(tmp_path, view):
    reversed_path = tmp_path / "reversed.jsonl"
    lines = (tmp_path / "buckets.jsonl").read_text().strip().splitlines()
    reversed_path.write_text("\n".join(reversed(lines)) + "\n")

    assert [t.key for t in EvidenceView.load(reversed_path).tickets] == [
        t.key for t in view.tickets
    ]


def test_per_person_documents_concatenate_only_that_persons_tickets(view):
    assert "Kafka" in view.documents[ALICE]
    assert "CSS" not in view.documents[ALICE]
    assert set(view.documents) == {ALICE, BOB, CARL, OUTSIDER}


def test_the_document_cache_round_trips(view, tmp_path):
    path = view.write_document_cache(tmp_path / "docs.json")
    cached = json.loads(path.read_text())
    assert cached["documents"] == view.documents
    assert cached["ticket_counts"] == view.ticket_counts
    assert cached["n_tickets"] == len(view.tickets)


def test_the_shared_tokenizer_keeps_the_punctuation_that_carries_meaning():
    assert tokenize("Node.js and C++ on log4j-2!") == ["node.js", "and", "c++", "on", "log4j-2"]


# ---------- roster restriction, shared by all three ----------

def _baseline(view: EvidenceView, name: str):
    """Build one baseline. The vector arm gets injected vectors so no model is loaded."""
    if name == "vector_only":
        return _vector_baseline(view)
    return build_baselines(view, names=[name])[name]


@pytest.mark.parametrize("name", BASELINE_SYSTEMS)
def test_every_baseline_ranks_the_whole_roster_and_nobody_else(view, name):
    output = _baseline(view, name).rank(_context())

    assert sorted(output.ranked_ids) == sorted([ALICE, BOB, CARL])
    assert OUTSIDER not in output.ranked_ids
    assert sorted(output.candidate_ids) == sorted([ALICE, BOB, CARL])


@pytest.mark.parametrize("name", BASELINE_SYSTEMS)
def test_every_baseline_is_deterministic_across_instances(view, name):
    context = _context()
    first, second = _baseline(view, name), _baseline(view, name)

    assert first.rank(context).ranked_ids == first.rank(context).ranked_ids
    assert first.rank(context).ranked_ids == second.rank(context).ranked_ids


def test_build_baselines_refuses_an_unknown_name(view):
    with pytest.raises(ValueError, match="unknown baseline"):
        build_baselines(view, names=["telepathy"])


# ---------- BM25 ----------

def test_bm25_ranks_the_person_whose_evidence_matches_the_brief(view):
    ranked = Bm25Baseline(view).rank(_context()).ranked_ids
    assert ranked[0] == ALICE


def test_bm25_excludes_non_roster_documents_from_its_index(view):
    """A restricted roster must not merely filter the output: IDF is computed inside it."""
    baseline = Bm25Baseline(view)

    baseline.rank(_context(roster=(ALICE, BOB)))

    _, people = baseline.index._index((ALICE, BOB))
    assert people == [ALICE, BOB]


def test_bm25_keeps_a_person_with_no_evidence_document_in_the_ranking(view):
    ranked = Bm25Baseline(view).rank(_context(roster=(ALICE, "ALPHA:99"))).ranked_ids
    assert ranked == [ALICE, "ALPHA:99"]


# ---------- pure vector ----------

def _fake_embed(view: EvidenceView):
    """A deterministic stand-in for the sentence transformer: bag-of-words cosine."""
    vocabulary = sorted({token for t in view.tickets for token in tokenize(t.text)})
    index = {token: position for position, token in enumerate(vocabulary)}

    def embed(texts):
        rows = np.zeros((len(texts), max(len(vocabulary), 1)), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                if token in index:
                    rows[row, index[token]] += 1.0
            norm = np.linalg.norm(rows[row])
            if norm:
                rows[row] /= norm
        return rows

    return embed


def _vector_baseline(view: EvidenceView) -> VectorBaseline:
    embed = _fake_embed(view)
    vectors = embed([ticket.text for ticket in view.tickets])
    return VectorBaseline(view, embed_fn=embed, vectors=vectors)


def test_vector_baseline_ranks_by_the_single_nearest_ticket(view):
    ranked = _vector_baseline(view).rank(_context()).ranked_ids
    assert ranked[0] == ALICE


def test_vector_baseline_is_not_diluted_by_ticket_volume(view):
    """Bob has more tickets than Alice; nearest-ticket scoring must not reward that."""
    ranked = _vector_baseline(view).rank(_context("Kafka broker partition")).ranked_ids
    assert ranked[0] == ALICE


def test_vector_baseline_ignores_tickets_outside_the_roster(view):
    """The outsider owns the closest ticket of all and must still never be returned."""
    ranked = _vector_baseline(view).rank(
        _context("Kafka streaming consumer lag in the other project")
    ).ranked_ids
    assert OUTSIDER not in ranked
    assert ranked[0] == ALICE


# ---------- most active ----------

def test_most_active_orders_by_pre_cutoff_ticket_count(view):
    assert MostActiveBaseline(view).rank(_context()).ranked_ids == [BOB, ALICE, CARL]


def test_most_active_ignores_the_brief_entirely(view):
    baseline = MostActiveBaseline(view)
    assert (
        baseline.rank(_context("Kafka")).ranked_ids
        == baseline.rank(_context("CSS layout")).ranked_ids
    )


def test_most_active_breaks_ties_on_person_id(view):
    ranked = MostActiveBaseline(view).rank(_context(roster=(CARL, "ALPHA:0"))).ranked_ids
    assert ranked == [CARL, "ALPHA:0"]      # CARL has one ticket, ALPHA:0 has none
