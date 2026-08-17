"""Fixture tests for the Stage 5 Neo4j load. Offline: no database, no embedding model.

Row building is pure by design, so everything the loader would send to Neo4j can be
asserted here. Three invariants are worth the ceremony:

- no raw ticket payload ever reaches a row (non-negotiable #2 in CLAUDE.md);
- every write statement MERGEs on a stable key, which is what makes a second
  `make stage5` produce identical counts instead of duplicates;
- the vector index dimension in schema.cypher tracks `embedding.dims`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pytest

from capgraph.models import (
    CanonicalTerm,
    Contribution,
    PersonCapability,
    SkillRef,
    SpecializationRef,
)
from capgraph.pipeline import stage5_graph as stage5
from capgraph.settings import settings

DIMS = int(settings["embedding.dims"])


def _bucket(person_id: str, project_key: str, period: str, chunk: str = "0") -> stage5.BucketRef:
    return stage5.BucketRef(
        bucket_id=f"{person_id}|{project_key}|{period}|{chunk}",
        person_id=person_id,
        person_name=f"Person {project_key}-{person_id.split(':')[-1]}",
        project_key=project_key,
        period=period,
    )


def _contribution(
    person_id: str = "DM:1",
    project_key: str = "DM",
    period: str = "2018-Q1",
    *,
    skills: tuple[str, ...] = ("Kafka",),
    specializations: tuple[tuple[str, str], ...] = (("Streaming", "primary"),),
    chunk: str = "0",
    skip: bool = False,
) -> Contribution:
    return Contribution(
        contribution_id=f"{person_id}|{project_key}|{period}|{chunk}",
        person_id=person_id,
        project_key=project_key,
        period=period,
        contribution_summary="Kept the broker retry path from melting down.",
        skills=[SkillRef(name=name) for name in skills],
        specializations=[
            SpecializationRef(name=name, strength=strength)
            for name, strength in specializations
        ],
        confidence="high",
        reason="All 5 of the 5 tickets shown describe the same work.",
        evidence_ticket_keys=[f"{project_key}-{index}" for index in range(5)],
        skip=skip,
        skip_reason="not enough signal" if skip else None,
    )


# ---------- fake driver ----------

@dataclass
class _Call:
    statement: str
    rows: list[dict]


class _FakeSession:
    def __init__(self, driver):
        self._driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, unit_of_work):
        return unit_of_work(self)

    def run(self, statement, **params):
        self._driver.calls.append(_Call(statement=statement, rows=params.get("rows", [])))
        return _FakeResult(self._driver.results.get(statement.strip()))


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def consume(self):
        return None

    def single(self):
        return self._value


class FakeDriver:
    """Records every statement and batch a loader would execute."""

    def __init__(self, results: dict | None = None):
        self.calls: list[_Call] = []
        self.results = results or {}

    def session(self):
        return _FakeSession(self)


# ---------- quarter arithmetic ----------

def test_period_start_is_the_first_calendar_day_of_the_quarter():
    assert stage5.period_start("2018-Q1") == date(2018, 1, 1)
    assert stage5.period_start("2018-Q2") == date(2018, 4, 1)
    assert stage5.period_start("2018-Q3") == date(2018, 7, 1)
    assert stage5.period_start("2018-Q4") == date(2018, 10, 1)


def test_period_start_rejects_a_malformed_period():
    for period in ["2018-Q0", "2018-Q5", "2018Q1", "2018-q1", ""]:
        with pytest.raises(ValueError, match="YYYY-QN"):
            stage5.period_start(period)


# ---------- input parsing ----------

def test_bucket_parsing_drops_the_ticket_payload():
    line = (
        '{"bucket_id": "DM:1|DM|2018-Q1|0", "person_id": "DM:1", "person_name": "Person DM-1", '
        '"project_key": "DM", "project_domain": "science", "period": "2018-Q1", '
        '"tickets": [{"key": "DM-1", "summary": "secret ticket text"}]}'
    )

    refs = stage5._bucket_refs([line, "", "\n"])

    assert refs == [_bucket("DM:1", "DM", "2018-Q1")]
    assert not hasattr(refs[0], "tickets")


def test_skipped_contributions_are_not_loaded(tmp_path):
    path = tmp_path / "normalized.jsonl"
    rows = [_contribution(period="2018-Q1"), _contribution(period="2018-Q2", skip=True)]
    path.write_text("".join(c.model_dump_json() + "\n" for c in rows), encoding="utf-8")

    kept, n_skipped = stage5.read_contributions(path)

    assert [c.period for c in kept] == ["2018-Q1"]
    assert n_skipped == 1


# ---------- node rows ----------

def test_people_span_their_bucket_periods_and_keep_project_qualified_identity():
    buckets = [
        _bucket("DM:2", "DM", "2017-Q3"),
        _bucket("DM:1", "DM", "2018-Q2"),
        _bucket("DM:1", "DM", "2016-Q4"),
        _bucket("DM:1", "DM", "2016-Q4", chunk="1"),
    ]

    rows = stage5.build_people(buckets)

    assert rows == [
        {
            "id": "DM:1",
            "pseudonym": "Person DM-1",
            "project_key": "DM",
            "active_from": date(2016, 10, 1),
            "active_to": date(2018, 6, 30),
        },
        {
            "id": "DM:2",
            "pseudonym": "Person DM-2",
            "project_key": "DM",
            "active_from": date(2017, 7, 1),
            "active_to": date(2017, 9, 30),
        },
    ]


def test_a_person_appearing_in_two_projects_is_refused():
    buckets = [_bucket("DM:1", "DM", "2018-Q1"), _bucket("DM:1", "EVG", "2018-Q1")]

    with pytest.raises(ValueError, match="multiple projects"):
        stage5.build_people(buckets)


def test_projects_take_their_domain_from_settings():
    rows = stage5.build_projects(["EVG", "DM", "DM"])

    assert [row["key"] for row in rows] == ["DM", "EVG"]
    assert rows[0]["domain"] == settings["dataset.project_domains"]["DM"]


def test_an_unconfigured_project_is_refused():
    with pytest.raises(ValueError, match="project_domains entry for NOPE"):
        stage5.build_projects(["NOPE"])


def test_contribution_rows_carry_evidence_pointers_and_a_sized_embedding(monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)
    contrib = _contribution()

    rows = stage5.build_contributions([contrib], {contrib.contribution_id: np.float32([1, 2, 3])})

    assert rows == [
        {
            "id": "DM:1|DM|2018-Q1|0",
            "summary": "Kept the broker retry path from melting down.",
            "period": "2018-Q1",
            "confidence": "high",
            "evidence_ticket_keys": ["DM-0", "DM-1", "DM-2", "DM-3", "DM-4"],
            "embedding": [1.0, 2.0, 3.0],
        }
    ]
    assert all(isinstance(value, float) for value in rows[0]["embedding"])


def test_a_wrong_sized_or_missing_embedding_is_refused(monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)
    contrib = _contribution()

    with pytest.raises(ValueError, match="embedding has 2 dims, expected 3"):
        stage5.build_contributions([contrib], {contrib.contribution_id: np.float32([1, 2])})
    with pytest.raises(KeyError, match="no embedding for contribution"):
        stage5.build_contributions([contrib], {})


def test_term_rows_are_split_by_kind_and_keep_aliases():
    terms = [
        CanonicalTerm(canonical="Kafka", aliases=["kafka streams"], kind="skill"),
        CanonicalTerm(canonical="Ansible", aliases=[], kind="skill"),
        CanonicalTerm(canonical="Streaming", aliases=["stream processing"], kind="specialization"),
    ]

    assert stage5.build_terms(terms, "skill") == [
        {"name": "Ansible", "aliases": []},
        {"name": "Kafka", "aliases": ["kafka streams"]},
    ]
    assert stage5.build_terms(terms, "specialization") == [
        {"name": "Streaming", "aliases": ["stream processing"]}
    ]


# ---------- edge rows ----------

def test_made_and_on_rows_link_contributions_to_person_and_project():
    contrib = _contribution()

    assert stage5.build_made([contrib]) == [
        {"person_id": "DM:1", "contribution_id": "DM:1|DM|2018-Q1|0"}
    ]
    assert stage5.build_on([contrib]) == [
        {"contribution_id": "DM:1|DM|2018-Q1|0", "project_key": "DM"}
    ]


def test_demonstrates_carries_strength_for_specializations_and_null_for_skills():
    contrib = _contribution(
        skills=("Kafka", "Python"),
        specializations=(("Streaming", "primary"), ("Storage", "secondary")),
    )

    skills = stage5.build_demonstrates([contrib], "skill")
    specializations = stage5.build_demonstrates([contrib], "specialization")

    assert [row["term"] for row in skills] == ["Kafka", "Python"]
    assert {row["strength"] for row in skills} == {None}
    assert [(row["term"], row["strength"]) for row in specializations] == [
        ("Streaming", "primary"),
        ("Storage", "secondary"),
    ]


def test_demonstrates_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match="unknown term kind"):
        stage5.build_demonstrates([_contribution()], "language")


def test_capability_rows_are_split_by_kind_with_projection_properties():
    caps = [
        PersonCapability(
            person_id="DM:1", term="Kafka", kind="skill", evidence_count=2,
            contribution_ids=["DM:1|DM|2018-Q1|0", "DM:1|DM|2018-Q2|0"],
            last_used=date(2018, 6, 30), decay_score=0.75,
        ),
        PersonCapability(
            person_id="DM:1", term="Streaming", kind="specialization", evidence_count=1,
            contribution_ids=["DM:1|DM|2018-Q1|0"],
            last_used=date(2018, 3, 31), decay_score=0.62,
            primary_evidence_count=1,
        ),
    ]

    skills = stage5.build_capabilities(caps, "skill")
    specializations = stage5.build_capabilities(caps, "specialization")

    assert skills == [
        {
            "person_id": "DM:1",
            "term": "Kafka",
            "evidence_count": 2,
            "last_used": date(2018, 6, 30),
            "decay_score": 0.75,
            # A skill carries no primary/secondary label, so its G6 count is always 0.
            "primary_evidence_count": 0,
        }
    ]
    assert [row["term"] for row in specializations] == ["Streaming"]
    assert specializations[0]["primary_evidence_count"] == 1
    # contribution_ids stay out of the edge: evidence is traversed through MADE.
    assert "contribution_ids" not in skills[0]


def test_collaboration_edges_are_unordered_pairs_counted_per_shared_project_quarter():
    buckets = [
        _bucket("DM:2", "DM", "2018-Q1"),
        _bucket("DM:1", "DM", "2018-Q1"),
        _bucket("DM:1", "DM", "2018-Q1", chunk="1"),   # two chunks, still one co-presence
        _bucket("DM:2", "DM", "2018-Q2"),
        _bucket("DM:1", "DM", "2018-Q2"),
        _bucket("DM:3", "DM", "2019-Q1"),              # no one else that quarter
        _bucket("EVG:1", "EVG", "2018-Q1"),            # different project, same quarter
    ]

    rows = stage5.build_collaborations(buckets)

    assert rows == [
        {
            "person_id": "DM:1",
            "other_person_id": "DM:2",
            "periods_count": 2,
            "basis": "co_presence_same_project_period",
        }
    ]


def test_a_person_alone_in_every_period_gets_no_collaboration_edge():
    assert stage5.build_collaborations([_bucket("DM:1", "DM", "2018-Q1")]) == []


# ---------- guards ----------

def test_ticket_payload_in_a_row_is_refused():
    with pytest.raises(ValueError, match="raw ticket payload"):
        stage5.assert_no_ticket_payload([{"id": "c1", "tickets": [{"key": "DM-1"}]}])
    with pytest.raises(ValueError, match="raw ticket payload"):
        stage5.assert_no_ticket_payload([{"id": "c1", "description": "ticket body"}])


def test_every_built_row_passes_the_ticket_payload_guard(monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)
    contrib = _contribution()
    buckets = [_bucket("DM:1", "DM", "2018-Q1"), _bucket("DM:2", "DM", "2018-Q1")]
    terms = [CanonicalTerm(canonical="Kafka", aliases=[], kind="skill")]
    vectors = {contrib.contribution_id: np.float32([1, 2, 3])}

    for rows in (
        stage5.build_people(buckets),
        stage5.build_projects(["DM"]),
        stage5.build_contributions([contrib], vectors),
        stage5.build_terms(terms, "skill"),
        stage5.build_made([contrib]),
        stage5.build_on([contrib]),
        stage5.build_demonstrates([contrib], "skill"),
        stage5.build_collaborations(buckets),
    ):
        stage5.assert_no_ticket_payload(rows)


def test_every_write_statement_merges_on_a_key_instead_of_creating():
    statements = [
        stage5.MERGE_PERSON,
        stage5.MERGE_PROJECT,
        stage5.MERGE_CONTRIBUTION,
        stage5.MERGE_TERM.format(label="Skill"),
        stage5.MERGE_MADE,
        stage5.MERGE_ON,
        stage5.MERGE_DEMONSTRATES.format(label="Specialization"),
        stage5.MERGE_CAPABILITY.format(label="Skill", rel_type="HAS_SKILL"),
        stage5.MERGE_COLLABORATED_WITH,
    ]

    for statement in statements:
        assert "MERGE" in statement
        assert "CREATE " not in statement
        assert "{}" not in statement          # no unformatted label placeholder left


def test_schema_declares_the_configured_vector_dimensions():
    cypher = stage5.SCHEMA_PATH.read_text(encoding="utf-8")

    drifted = cypher.replace(f"`vector.dimensions`: {DIMS}", "`vector.dimensions`: 7")

    assert stage5.check_vector_index_dims(cypher) == DIMS
    with pytest.raises(ValueError, match="vector.dimensions"):
        stage5.check_vector_index_dims(drifted)


def test_schema_statements_drop_comments_and_keep_every_statement():
    statements = stage5.schema_statements(stage5.SCHEMA_PATH.read_text(encoding="utf-8"))

    assert len(statements) == 6                      # five constraints + the vector index
    assert not any(statement.startswith("//") for statement in statements)
    assert statements[-1].startswith("CREATE VECTOR INDEX contribution_embedding")


# ---------- batching ----------

def test_batches_cover_every_row_exactly_once():
    rows = list(range(1050))

    batches = list(stage5.batched(rows, 500))

    assert [len(batch) for batch in batches] == [500, 500, 50]
    assert [row for batch in batches for row in batch] == rows
    assert list(stage5.batched([], 500)) == []


def test_a_non_positive_batch_size_is_refused():
    with pytest.raises(ValueError, match="batch size"):
        list(stage5.batched([1, 2, 3], 0))


def test_run_batches_sends_one_transaction_per_batch_with_the_same_statement(monkeypatch):
    monkeypatch.setattr(stage5, "BATCH_SIZE", 2)
    driver = FakeDriver()
    rows = [{"id": index} for index in range(5)]

    written = stage5.run_batches(driver, stage5.MERGE_PERSON, rows)

    assert written == 5
    assert [len(call.rows) for call in driver.calls] == [2, 2, 1]
    assert {call.statement for call in driver.calls} == {stage5.MERGE_PERSON}
    assert [row for call in driver.calls for row in call.rows] == rows


def test_run_batches_refuses_ticket_payload_before_touching_the_database():
    driver = FakeDriver()

    with pytest.raises(ValueError, match="raw ticket payload"):
        stage5.run_batches(driver, stage5.MERGE_CONTRIBUTION, [{"id": "c1", "tickets": []}])

    assert driver.calls == []


def test_counts_are_reported_per_label_and_relationship_type():
    driver = FakeDriver(
        results={
            f"MATCH (n:{label}) RETURN count(n) AS total": {"total": index}
            for index, label in enumerate(stage5.NODE_LABELS)
        }
        | {
            f"MATCH ()-[r:{rel}]->() RETURN count(r) AS total": {"total": 100 + index}
            for index, rel in enumerate(stage5.RELATIONSHIP_TYPES)
        }
    )

    counts = stage5.graph_counts(driver)

    assert counts["Person"] == 0 and counts["Contribution"] == 2
    assert counts["MADE"] == 100 and counts["COLLABORATED_WITH"] == 105
    assert "| Person | node | 0 |" in stage5.format_counts(counts)
    assert "| COLLABORATED_WITH | relationship | 105 |" in stage5.format_counts(counts)


# ---------- embedding cache ----------

def test_embeddings_are_cached_and_reused_across_runs(tmp_path, monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)
    path = tmp_path / "contribution_embeddings.npz"
    contribs = [_contribution(period="2018-Q1"), _contribution(period="2018-Q2")]
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return np.asarray([[float(index), 0.0, 1.0] for index, _ in enumerate(texts)], np.float32)

    first, recomputed = stage5.embed_contributions(
        contribs, path=path, embed_fn=fake_embed
    )
    second, reused = stage5.embed_contributions(
        contribs, path=path, embed_fn=fake_embed
    )

    assert recomputed and not reused
    assert len(calls) == 1                                   # the second run never embedded
    assert sorted(first) == [c.contribution_id for c in contribs]
    assert all(np.array_equal(first[key], second[key]) for key in first)


def test_force_recomputes_and_new_contributions_invalidate_the_cache(tmp_path, monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)
    path = tmp_path / "contribution_embeddings.npz"
    contribs = [_contribution(period="2018-Q1")]
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], np.float32)

    stage5.embed_contributions(contribs, path=path, embed_fn=fake_embed)
    _, forced = stage5.embed_contributions(contribs, path=path, force=True, embed_fn=fake_embed)
    grown = [*contribs, _contribution(period="2018-Q2")]
    vectors, recomputed = stage5.embed_contributions(grown, path=path, embed_fn=fake_embed)

    assert forced and recomputed
    assert len(calls) == 3
    assert sorted(vectors) == sorted(c.contribution_id for c in grown)


def test_a_cache_from_another_embedding_model_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)
    path = tmp_path / "contribution_embeddings.npz"
    contribs = [_contribution()]

    def fake_embed(texts):
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], np.float32)

    stage5.embed_contributions(contribs, path=path, embed_fn=fake_embed)
    monkeypatch.setitem(settings._cfg["embedding"], "model", "some/other-model")
    _, recomputed = stage5.embed_contributions(contribs, path=path, embed_fn=fake_embed)

    assert recomputed


def test_an_embedding_matrix_of_the_wrong_shape_is_refused(tmp_path, monkeypatch):
    monkeypatch.setitem(settings._cfg["embedding"], "dims", 3)

    def wrong_dims(texts):
        return np.asarray([[1.0, 0.0] for _ in texts], np.float32)

    with pytest.raises(ValueError, match="embedding matrix has shape"):
        stage5.embed_contributions(
            [_contribution()], path=tmp_path / "cache.npz", embed_fn=wrong_dims
        )
