"""Stage 3: raw.jsonl -> normalized.jsonl + terms.jsonl (canonical skill/spec vocabulary).

Emergent terms are deduped by embedding similarity (agglomerative clustering, average
linkage, cosine threshold from settings). Canonical name = most frequent term in cluster.

IMPORTANT: after first run, manually review data/contributions/terms.jsonl for bad merges
(e.g. "Java" + "JavaScript") and add overrides to data/contributions/term_overrides.yaml:
  never_merge: [["java", "javascript"]]
  force_alias: {"k8s": "Kubernetes"}
Re-running applies overrides. Budget one careful human hour here — it pays for itself.
"""
from __future__ import annotations

from collections import Counter

import yaml
from sklearn.cluster import AgglomerativeClustering

from ..embeddings import embed
from ..models import CanonicalTerm, Contribution
from ..settings import DATA_DIR, settings

RAW_PATH = DATA_DIR / "contributions" / "raw.jsonl"
NORM_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
TERMS_PATH = DATA_DIR / "contributions" / "terms.jsonl"
OVERRIDES_PATH = DATA_DIR / "contributions" / "term_overrides.yaml"


def load_overrides() -> dict:
    if OVERRIDES_PATH.exists():
        return yaml.safe_load(OVERRIDES_PATH.read_text()) or {}
    return {}


def cluster_terms(terms: list[str], threshold: float, kind: str, overrides: dict) -> dict[str, str]:
    """Map every raw term -> canonical name."""
    counts = Counter(t.strip() for t in terms if t and t.strip())
    unique = list(counts)
    if len(unique) < 2:
        return {t: t for t in unique}
    vecs = embed(unique)
    labels = AgglomerativeClustering(
        n_clusters=None, metric="cosine", linkage="average",
        distance_threshold=1 - threshold,
    ).fit_predict(vecs)

    never_merge = [set(map(str.lower, pair)) for pair in overrides.get("never_merge", [])]
    force_alias = {k.lower(): v for k, v in overrides.get("force_alias", {}).items()}

    mapping: dict[str, str] = {}
    for label in set(labels):
        cluster = [unique[i] for i in range(len(unique)) if labels[i] == label]
        # split clusters that violate never_merge pairs
        groups: list[list[str]] = []
        for term in sorted(cluster, key=lambda t: -counts[t]):
            placed = False
            for g in groups:
                if not any({term.lower(), m.lower()} in never_merge for m in g):
                    g.append(term)
                    placed = True
                    break
            if not placed:
                groups.append([term])
        for g in groups:
            canonical = max(g, key=lambda t: counts[t])
            for term in g:
                mapping[term] = canonical
    for raw, canon in force_alias.items():
        for term in list(mapping):
            if term.lower() == raw:
                mapping[term] = canon
    return mapping


def main() -> None:
    contribs = [Contribution.model_validate_json(line) for line in open(RAW_PATH)]
    contribs = [c for c in contribs if not c.skip]
    overrides = load_overrides()

    skill_map = cluster_terms(
        [s.name for c in contribs for s in c.skills],
        settings["normalization.skill_merge_threshold"], "skill", overrides)
    spec_map = cluster_terms(
        [s.name for c in contribs for s in c.specializations],
        settings["normalization.specialization_merge_threshold"], "specialization", overrides)

    with open(NORM_PATH, "w") as f:
        for c in contribs:
            for s in c.skills:
                s.name = skill_map.get(s.name, s.name)
            for s in c.specializations:
                s.name = spec_map.get(s.name, s.name)
            # dedup within a contribution after mapping
            c.skills = list({s.name: s for s in c.skills}.values())
            c.specializations = list({s.name: s for s in c.specializations}.values())
            f.write(c.model_dump_json() + "\n")

    with open(TERMS_PATH, "w") as f:
        for mapping, kind in [(skill_map, "skill"), (spec_map, "specialization")]:
            canon_to_aliases: dict[str, list[str]] = {}
            for raw, canon in mapping.items():
                canon_to_aliases.setdefault(canon, [])
                if raw != canon:
                    canon_to_aliases[canon].append(raw)
            for canon, aliases in canon_to_aliases.items():
                f.write(CanonicalTerm(canonical=canon, aliases=aliases, kind=kind).model_dump_json() + "\n")

    n_skills = len({v for v in skill_map.values()})
    print(f"{len(contribs)} contributions normalized; {n_skills} canonical skills, "
          f"{len({v for v in spec_map.values()})} specializations -> review {TERMS_PATH}")


if __name__ == "__main__":
    main()
