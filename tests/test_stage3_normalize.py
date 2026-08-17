"""Fixture tests for Stage 3 normalization. Offline: the embedder is stubbed.

The stub places every term on a family axis with a small per-term perturbation, so
same-family terms sit at a known cosine similarity and different families are
orthogonal. That makes threshold behaviour assertable without a downloaded model.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from capgraph.models import Contribution, SkillRef, SpecializationRef
from capgraph.pipeline import stage3_normalize as stage3
from capgraph.pipeline.stage3_normalize import Overrides, cluster_terms, parse_overrides
from capgraph.settings import settings

# term -> (family, cosine similarity to the rest of its family)
VOCAB = {
    "Kubernetes": ("k8s", 0.99),
    "kubernetes": ("k8s", 0.99),
    "K8s": ("k8s", 0.99),
    "Docker": ("containers", 0.99),
    "docker containers": ("containers", 0.99),
    "Java": ("jvm-ish", 0.90),
    "JavaScript": ("jvm-ish", 0.90),
    "Kafka": ("kafka", 0.99),
}
FAMILIES = sorted({family for family, _ in VOCAB.values()})


def fake_embed(terms: list[str]) -> np.ndarray:
    """Deterministic unit vectors: family axis + a per-term axis sized by similarity."""
    dims = len(FAMILIES) + len(VOCAB)
    term_axis = {term: len(FAMILIES) + index for index, term in enumerate(sorted(VOCAB))}
    vectors = np.zeros((len(terms), dims), dtype=np.float32)
    for row, term in enumerate(terms):
        family, similarity = VOCAB[term]
        # cos(v_i, v_j) == similarity for two terms of the same family
        vectors[row, FAMILIES.index(family)] = math.sqrt(similarity)
        vectors[row, term_axis[term]] = math.sqrt(1 - similarity)
    return vectors


@pytest.fixture(autouse=True)
def stub_embedder(monkeypatch):
    monkeypatch.setattr(stage3, "embed", fake_embed)


def _contribution(contribution_id: str, skills, specializations, **kwargs) -> Contribution:
    payload = {
        "contribution_id": contribution_id,
        "person_id": contribution_id.split("|")[0],
        "project_key": "PROJ",
        "period": "2018-Q1",
        "contribution_summary": "Kept the broker retry path from melting down.",
        "skills": [SkillRef(name=name) for name in skills],
        "specializations": [
            SpecializationRef(name=name, strength=strength) for name, strength in specializations
        ],
        "confidence": "high",
        "reason": "All 5 of the 5 tickets shown describe the same work.",
        "evidence_ticket_keys": [f"PROJ-{index}" for index in range(5)],
    }
    payload.update(kwargs)
    return Contribution(**payload)


def _write_raw(path, contribs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for contribution in contribs:
            handle.write(contribution.model_dump_json() + "\n")
    return path


@pytest.fixture
def stage3_paths(tmp_path, monkeypatch):
    """Redirect every Stage 3 path into tmp_path; returns the path namespace."""
    paths = {
        "RAW_PATH": tmp_path / "contributions" / "raw.jsonl",
        "NORM_PATH": tmp_path / "contributions" / "normalized.jsonl",
        "TERMS_PATH": tmp_path / "contributions" / "terms.jsonl",
        "REPORT_PATH": tmp_path / "contributions" / "terms_report.md",
        "OVERRIDES_PATH": tmp_path / "config" / "term_overrides.yaml",
    }
    for name, path in paths.items():
        monkeypatch.setattr(stage3, name, path)
    paths["RAW_PATH"].parent.mkdir(parents=True, exist_ok=True)
    return paths


# ---------- clustering ----------

def test_clustering_merges_near_duplicates_onto_the_most_frequent_term():
    terms = ["Kubernetes", "Kubernetes", "kubernetes", "K8s", "Kafka"]

    vocabulary = cluster_terms(terms, 0.85, "skill", Overrides())

    assert vocabulary.mapping == {
        "Kubernetes": "Kubernetes",
        "kubernetes": "Kubernetes",
        "K8s": "Kubernetes",
        "Kafka": "Kafka",
    }
    assert vocabulary.members()["Kubernetes"] == ["Kubernetes", "K8s", "kubernetes"]


def test_threshold_decides_whether_a_loose_pair_merges():
    terms = ["Java", "Java", "JavaScript"]   # stub similarity 0.90

    merged = cluster_terms(terms, 0.85, "skill", Overrides())
    split = cluster_terms(terms, 0.95, "skill", Overrides())

    assert merged.mapping == {"Java": "Java", "JavaScript": "Java"}
    assert split.mapping == {"Java": "Java", "JavaScript": "JavaScript"}


def test_never_merge_splits_a_cluster_the_embedding_would_join():
    terms = ["Java", "Java", "JavaScript"]
    overrides = parse_overrides({"never_merge": [["java", "javascript"]]})

    vocabulary = cluster_terms(terms, 0.85, "skill", overrides)

    assert vocabulary.mapping == {"Java": "Java", "JavaScript": "JavaScript"}


def test_force_alias_overrides_the_clustered_canonical():
    terms = ["Kubernetes", "Kubernetes", "K8s", "Docker"]
    overrides = parse_overrides({"force_alias": {"docker": "Kubernetes"}})

    vocabulary = cluster_terms(terms, 0.85, "skill", overrides)

    assert vocabulary.mapping["Docker"] == "Kubernetes"
    assert vocabulary.mapping["K8s"] == "Kubernetes"
    assert vocabulary.canonicals == ["Kubernetes"]


def test_canonical_is_the_most_frequent_term_with_lexicographic_tie_break():
    balanced = cluster_terms(["Kubernetes", "kubernetes"], 0.85, "skill", Overrides())
    weighted = cluster_terms(["Kubernetes", "kubernetes", "kubernetes"], 0.85, "skill", Overrides())

    assert set(balanced.mapping.values()) == {"Kubernetes"}      # "K" < "k"
    assert set(weighted.mapping.values()) == {"kubernetes"}      # frequency beats order


def test_single_term_vocabulary_still_honours_force_alias():
    overrides = parse_overrides({"force_alias": {"k8s": "Kubernetes"}})

    assert cluster_terms(["K8s"], 0.85, "skill", overrides).mapping == {"K8s": "Kubernetes"}
    assert cluster_terms([], 0.85, "skill", Overrides()).mapping == {}


def test_oversized_vocabulary_escalates_instead_of_clustering(monkeypatch):
    monkeypatch.setitem(settings._cfg["normalization"], "max_pairwise_matrix_gb", 1e-9)

    with pytest.raises(MemoryError, match="condensed pairwise"):
        cluster_terms(["Kubernetes", "Kafka"], 0.85, "skill", Overrides())


def test_malformed_overrides_are_rejected():
    with pytest.raises(ValueError, match="never_merge"):
        parse_overrides({"never_merge": [["Java"]]})
    with pytest.raises(ValueError, match="force_alias"):
        parse_overrides({"force_alias": {"k8s": "  "}})
    with pytest.raises(ValueError, match="unknown term override keys"):
        parse_overrides({"never_merged": []})


def test_overrides_load_from_the_tracked_config_path(tmp_path):
    path = tmp_path / "term_overrides.yaml"

    assert stage3.load_overrides(path) == Overrides()        # missing file
    path.write_text("# only comments\n", encoding="utf-8")
    assert stage3.load_overrides(path) == Overrides()        # empty file
    path.write_text('force_alias:\n  k8s: "Kubernetes"\n', encoding="utf-8")
    assert stage3.load_overrides(path).force_alias == {"k8s": "Kubernetes"}


# ---------- contributions ----------

def test_confidence_is_clamped_only_for_short_evidence_high_records():
    contribs = [
        _contribution("a", ["Kafka"], [], evidence_ticket_keys=["PROJ-1"] * 4),
        _contribution("b", ["Kafka"], [], evidence_ticket_keys=["PROJ-1"] * 5),
        _contribution("c", ["Kafka"], [], confidence="medium", evidence_ticket_keys=["PROJ-1"]),
        _contribution("d", ["Kafka"], [], confidence="low", evidence_ticket_keys=[]),
    ]

    assert stage3.clamp_confidence(contribs) == 1
    assert [c.confidence for c in contribs] == ["medium", "high", "medium", "low"]


def test_terms_are_deduped_within_a_contribution_after_mapping():
    contribs = [
        _contribution(
            "a",
            ["Kubernetes", "K8s", "kubernetes", "Kafka"],
            [("Docker", "secondary"), ("docker containers", "primary")],
        )
    ]
    skills = cluster_terms(["Kubernetes", "Kubernetes", "K8s", "kubernetes", "Kafka"],
                           0.85, "skill", Overrides())
    specializations = cluster_terms(["Docker", "Docker", "docker containers"],
                                    0.80, "specialization", Overrides())

    stage3.apply_mappings(contribs, skills, specializations)

    assert [ref.name for ref in contribs[0].skills] == ["Kubernetes", "Kafka"]
    assert [(ref.name, ref.strength) for ref in contribs[0].specializations] == [
        ("Docker", "primary")   # the stronger of the two merged mentions wins
    ]


def test_skipped_contributions_are_excluded_from_the_corpus(stage3_paths):
    _write_raw(
        stage3_paths["RAW_PATH"],
        [
            _contribution("a", ["Kafka"], []),
            _contribution("b", [], [], skip=True, skip_reason="vague tickets", confidence="low"),
        ],
    )

    contribs, n_skipped = stage3.load_contributions(stage3_paths["RAW_PATH"])

    assert [c.contribution_id for c in contribs] == ["a"]
    assert n_skipped == 1


# ---------- end to end ----------

def _corpus() -> list[Contribution]:
    return [
        _contribution("p1|PROJ|2018-Q1|0", ["Kubernetes", "K8s"], [("Docker", "primary")]),
        _contribution("p2|PROJ|2018-Q1|0", ["kubernetes", "Kafka"],
                      [("docker containers", "secondary")]),
        _contribution("p3|PROJ|2018-Q1|0", ["Java", "JavaScript"], [("Kafka", "primary")],
                      evidence_ticket_keys=["PROJ-1", "PROJ-2"]),
        _contribution("p4|PROJ|2018-Q1|0", [], [], skip=True, skip_reason="no signal",
                      confidence="low", evidence_ticket_keys=[]),
    ]


def test_main_writes_byte_identical_outputs_on_re_run(stage3_paths):
    _write_raw(stage3_paths["RAW_PATH"], _corpus())

    stage3.main()
    first = {name: stage3_paths[name].read_bytes() for name in ("NORM_PATH", "TERMS_PATH")}
    stage3.main()

    assert {name: stage3_paths[name].read_bytes() for name in first} == first


def test_output_does_not_depend_on_raw_line_order(stage3_paths):
    corpus = _corpus()
    _write_raw(stage3_paths["RAW_PATH"], corpus)
    stage3.main()
    expected = {name: stage3_paths[name].read_bytes() for name in ("NORM_PATH", "TERMS_PATH")}

    _write_raw(stage3_paths["RAW_PATH"], list(reversed(corpus)))
    stage3.main()

    assert {name: stage3_paths[name].read_bytes() for name in expected} == expected


def test_normalized_output_is_complete_and_covered_by_the_term_vocabulary(stage3_paths):
    _write_raw(stage3_paths["RAW_PATH"], _corpus())

    stage3.main()

    normalized = [
        Contribution.model_validate_json(line)
        for line in stage3_paths["NORM_PATH"].read_text(encoding="utf-8").splitlines()
    ]
    canonicals = {
        line.split('"canonical":"')[1].split('"')[0]
        for line in stage3_paths["TERMS_PATH"].read_text(encoding="utf-8").splitlines()
    }
    assert [c.contribution_id for c in normalized] == [
        "p1|PROJ|2018-Q1|0", "p2|PROJ|2018-Q1|0", "p3|PROJ|2018-Q1|0"
    ]
    assert not canonicals.isdisjoint({"Kubernetes", "Kafka"})
    assert all(
        ref.name in canonicals
        for contribution in normalized
        for ref in [*contribution.skills, *contribution.specializations]
    )
    # the short-evidence record was clamped in the output, never in raw.jsonl
    assert [c.confidence for c in normalized] == ["high", "high", "medium"]
    assert '"confidence":"high"' in stage3_paths["RAW_PATH"].read_text(encoding="utf-8")
    assert "top" in stage3_paths["REPORT_PATH"].read_text(encoding="utf-8")
