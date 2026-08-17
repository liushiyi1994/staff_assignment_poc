"""Stage 3: raw.jsonl -> normalized.jsonl + terms.jsonl (canonical skill/spec vocabulary).

Emergent terms are deduped by embedding similarity (agglomerative clustering, average
linkage, cosine threshold from settings). Canonical name = the most frequent term in a
cluster, ties broken lexicographically.

Everything here is deterministic: the clustering input is sorted, cluster members,
aliases and output rows are sorted, and no randomness is involved. Re-running with the
same raw.jsonl, settings and overrides rewrites byte-identical outputs.

Confidence is clamped to at most "medium" for contributions listing fewer evidence keys
than the extraction rubric requires for "high" (prompts/extraction.md). The clamp lives
in the normalized output only — raw.jsonl is the immutable extraction record.

IMPORTANT: after a run, review data/contributions/terms_report.md (canonical terms by
frequency, with aliases) for bad merges (e.g. "Java" + "JavaScript") and record the
judgments in the tracked config/term_overrides.yaml:
  never_merge: [["Java", "JavaScript"]]
  force_alias: {"k8s": "Kubernetes"}
Re-running applies them. Budget one careful human hour here — it pays for itself.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from sklearn.cluster import AgglomerativeClustering

from .. import improvements
from ..embeddings import embed
from ..models import CanonicalTerm, Contribution, SkillRef, SpecializationRef
from ..settings import DATA_DIR, REPO_ROOT, settings

RAW_PATH = DATA_DIR / "contributions" / "raw.jsonl"
NORM_PATH = DATA_DIR / "contributions" / "normalized.jsonl"
TERMS_PATH = DATA_DIR / "contributions" / "terms.jsonl"
REPORT_PATH = DATA_DIR / "contributions" / "terms_report.md"
OVERRIDES_PATH = REPO_ROOT / "config" / "term_overrides.yaml"

BYTES_PER_GIB = 1024**3
STRENGTH_RANK = {"primary": 0, "secondary": 1}


@dataclass(frozen=True)
class Overrides:
    """Validated term-review judgments; both sides are lowercased for lookup."""

    never_merge: tuple[frozenset[str], ...] = ()
    force_alias: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Vocabulary:
    """One kind's clustering result plus the numbers the term review needs."""

    kind: str
    mapping: dict[str, str]           # raw term -> canonical name
    counts: Counter[str]              # raw term -> mentions across contributions
    matrix_gib: float                 # condensed pairwise matrix the clustering needed
    # Backlog G3a. The floor that was applied (0 = the gate was off) and how many
    # clusters it demoted from canonical to alias, so the report states both.
    min_document_frequency: int = 0
    gated_canonicals: int = 0

    @property
    def canonicals(self) -> list[str]:
        return sorted(set(self.mapping.values()))

    def members(self) -> dict[str, list[str]]:
        """canonical -> its raw terms, sorted by frequency then name."""
        grouped: dict[str, list[str]] = {canonical: [] for canonical in self.mapping.values()}
        for raw, canonical in self.mapping.items():
            grouped[canonical].append(raw)
        return {
            canonical: sorted(terms, key=lambda t: (-self.counts[t], t))
            for canonical, terms in sorted(grouped.items())
        }

    def frequencies(self) -> dict[str, int]:
        """canonical -> mentions summed over its cluster."""
        totals: Counter[str] = Counter()
        for raw, canonical in self.mapping.items():
            totals[canonical] += self.counts[raw]
        return dict(totals)


# ---------- overrides ----------

def parse_overrides(raw: dict | None) -> Overrides:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TypeError(f"{OVERRIDES_PATH.name} must contain a mapping, got {type(raw).__name__}")
    unknown = set(raw) - {"never_merge", "force_alias"}
    if unknown:
        raise ValueError(f"unknown term override keys: {sorted(unknown)}")

    pairs: list[frozenset[str]] = []
    for pair in raw.get("never_merge") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"never_merge entries must be two-term lists, got {pair!r}")
        terms = [str(term).strip() for term in pair]
        if not all(terms) or terms[0].lower() == terms[1].lower():
            raise ValueError(f"never_merge entries must be two distinct terms, got {pair!r}")
        pairs.append(frozenset(term.lower() for term in terms))

    aliases: dict[str, str] = {}
    for key, value in (raw.get("force_alias") or {}).items():
        term, canonical = str(key).strip(), str(value).strip()
        if not term or not canonical:
            raise ValueError(f"force_alias entries must be non-empty, got {key!r}: {value!r}")
        aliases[term.lower()] = canonical

    return Overrides(
        never_merge=tuple(sorted(pairs, key=sorted)),
        force_alias=dict(sorted(aliases.items())),
    )


def load_overrides(path: Path | None = None) -> Overrides:
    path = OVERRIDES_PATH if path is None else path
    if not path.exists():
        return Overrides()
    return parse_overrides(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---------- clustering ----------

def pairwise_matrix_gib(n_terms: int) -> float:
    """Condensed float64 distance matrix scipy materializes for n terms, in GiB."""
    return n_terms * (n_terms - 1) // 2 * 8 / BYTES_PER_GIB


def check_pairwise_budget(n_terms: int, kind: str) -> float:
    """Refuse to cluster a vocabulary whose O(n^2) distance matrix blows the budget."""
    needed = pairwise_matrix_gib(n_terms)
    budget = float(settings["normalization.max_pairwise_matrix_gb"])
    if needed > budget:
        raise MemoryError(
            f"{kind}: {n_terms} unique terms need a {needed:.2f} GiB condensed pairwise "
            f"distance matrix, above the {budget:.2f} GiB budget "
            "(normalization.max_pairwise_matrix_gb). Escalate with these numbers instead "
            "of switching clustering algorithm."
        )
    return needed


def _split_never_merge(
    cluster: list[str], counts: Counter[str], never_merge: tuple[frozenset[str], ...]
) -> list[list[str]]:
    """Greedily split one cluster so no group holds a forbidden pair.

    Members are visited most-frequent first (name as tie-break) so the largest group
    keeps the term that would win the canonical vote.
    """
    groups: list[list[str]] = []
    for term in sorted(cluster, key=lambda t: (-counts[t], t)):
        for group in groups:
            if not any({term.lower(), member.lower()} in never_merge for member in group):
                group.append(term)
                break
        else:
            groups.append([term])
    return groups


def canonical_document_frequency(
    mapping: Mapping[str, str],
    counts: Counter[str],
    documents: Sequence[Sequence[str]] | None = None,
) -> Counter[str]:
    """Distinct contributions supporting each canonical name.

    With ``documents`` — one term list per contribution — this is exact: a contribution
    naming two raw terms that merged onto the same canonical supports it once. Without
    them the per-term mention counts are summed, which is the same number except where a
    single contribution mentioned two members of one cluster.
    """
    totals: Counter[str] = Counter()
    if documents is None:
        for term, canonical in mapping.items():
            totals[canonical] += counts[term]
        return totals
    for terms in documents:
        supported = set()
        for term in terms:
            canonical = mapping.get(str(term).strip())
            if canonical is not None:
                supported.add(canonical)
        for canonical in supported:
            totals[canonical] += 1
    return totals


def _unit_rows(vectors) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def apply_frequency_gate(
    mapping: dict[str, str],
    *,
    unique: Sequence[str],
    vectors,
    frequencies: Counter[str],
    floor: int,
    protected: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], int]:
    """Backlog G3a: demote thin canonicals to aliases of their nearest surviving one.

    A canonical supported by fewer than ``floor`` distinct contributions cannot be
    matched by a query that phrases it any other way, so it contributes nothing to the
    structured arm while enlarging the vocabulary the intent parser has to map onto.
    Rather than delete it, its whole cluster attaches to the nearest surviving canonical
    by cosine over the same embeddings the clustering used, so every raw term still
    resolves to something and no evidence is lost.

    A floor of 0 or 1 is a no-op by construction: every canonical has at least one
    supporting contribution. Terms an operator forced in ``config/term_overrides.yaml``
    are ``protected`` — a human judgment outranks a frequency threshold.

    Returns the mapping and how many canonicals were demoted.
    """
    if floor <= 1:
        return mapping, 0
    canonicals = set(mapping.values())
    survivors = sorted(
        canonical
        for canonical in canonicals
        if frequencies[canonical] >= floor or canonical in protected
    )
    if not survivors:
        raise ValueError(
            f"no canonical term reaches a document-frequency floor of {floor} "
            f"(highest is {max(frequencies.values(), default=0)}); lower "
            "improvements.vocabulary.min_document_frequency instead of emptying the "
            "vocabulary"
        )
    index = {term: position for position, term in enumerate(unique)}
    normed = _unit_rows(vectors)
    # A canonical that never appeared as a raw term (an operator invented the name in
    # the overrides file) has no vector, so it can neither be scored against nor
    # demoted; it stays canonical.
    survivor_names = [name for name in survivors if name in index]
    if not survivor_names:
        return mapping, 0
    survivor_rows = normed[[index[name] for name in survivor_names]]

    demoted: dict[str, str] = {}
    for canonical in sorted(canonicals - set(survivors)):
        if canonical not in index:
            continue
        similarity = survivor_rows @ normed[index[canonical]]
        # argmax takes the first maximum and survivor_names is sorted, so a tie
        # resolves the same way on every run.
        demoted[canonical] = survivor_names[int(np.argmax(similarity))]
    if not demoted:
        return mapping, 0
    return (
        {term: demoted.get(canonical, canonical) for term, canonical in mapping.items()},
        len(demoted),
    )


def cluster_terms(
    terms: list[str],
    threshold: float,
    kind: str,
    overrides: Overrides,
    *,
    documents: Sequence[Sequence[str]] | None = None,
) -> Vocabulary:
    """Map every raw term of one kind to a canonical name."""
    counts = Counter(term.strip() for term in terms if term and term.strip())
    unique = sorted(counts)                     # deterministic clustering input order
    floor = improvements.vocabulary_min_document_frequency()
    if len(unique) < 2:
        mapping = {term: overrides.force_alias.get(term.lower(), term) for term in unique}
        return Vocabulary(
            kind=kind, mapping=mapping, counts=counts, matrix_gib=0.0,
            min_document_frequency=floor,
        )

    matrix_gib = check_pairwise_budget(len(unique), kind)
    vectors = embed(unique)
    labels = AgglomerativeClustering(
        n_clusters=None, metric="cosine", linkage="average",
        distance_threshold=1 - threshold,
    ).fit_predict(vectors)

    clusters: dict[int, list[str]] = {}
    for term, label in zip(unique, labels, strict=True):
        clusters.setdefault(int(label), []).append(term)

    mapping: dict[str, str] = {}
    for label in sorted(clusters):
        for group in _split_never_merge(clusters[label], counts, overrides.never_merge):
            canonical = min(group, key=lambda t: (-counts[t], t))
            for term in group:
                mapping[term] = canonical
    for term in sorted(mapping):
        forced = overrides.force_alias.get(term.lower())
        if forced is not None:
            mapping[term] = forced

    mapping, gated = apply_frequency_gate(
        mapping,
        unique=unique,
        vectors=vectors,
        frequencies=canonical_document_frequency(mapping, counts, documents),
        floor=floor,
        protected=frozenset(overrides.force_alias.values()),
    )

    return Vocabulary(
        kind=kind, mapping=dict(sorted(mapping.items())), counts=counts,
        matrix_gib=matrix_gib, min_document_frequency=floor, gated_canonicals=gated,
    )


# ---------- contributions ----------

def load_contributions(path: Path | None = None) -> tuple[list[Contribution], int]:
    """Return the non-skipped contributions and how many skips were dropped."""
    path = RAW_PATH if path is None else path
    with path.open(encoding="utf-8") as handle:
        contribs = [Contribution.model_validate_json(line) for line in handle if line.strip()]
    kept = [c for c in contribs if not c.skip]
    return kept, len(contribs) - len(kept)


def clamp_confidence(contribs: list[Contribution]) -> int:
    """Clamp "high" down to "medium" where the evidence list is short. Returns the count."""
    minimum = int(settings["normalization.min_evidence_keys_for_high_confidence"])
    clamped = 0
    for contribution in contribs:
        if contribution.confidence == "high" and len(contribution.evidence_ticket_keys) < minimum:
            contribution.confidence = "medium"
            clamped += 1
    return clamped


def apply_mappings(
    contribs: list[Contribution], skills: Vocabulary, specializations: Vocabulary
) -> None:
    """Rewrite term names in place, then dedup within each contribution.

    Distinct raw terms can collapse onto one canonical name, so dedup keeps the first
    mention's position; for specializations a "primary" mention outranks a "secondary"
    one rather than the merge silently downgrading the stronger signal.
    """
    for contribution in contribs:
        merged_skills: dict[str, SkillRef] = {}
        for ref in contribution.skills:
            name = skills.mapping.get(ref.name.strip(), ref.name.strip())
            if name:
                merged_skills.setdefault(name, SkillRef(name=name))
        contribution.skills = list(merged_skills.values())

        merged_specs: dict[str, SpecializationRef] = {}
        for ref in contribution.specializations:
            name = specializations.mapping.get(ref.name.strip(), ref.name.strip())
            if not name:
                continue
            current = merged_specs.get(name)
            if current is None or STRENGTH_RANK[ref.strength] < STRENGTH_RANK[current.strength]:
                merged_specs[name] = SpecializationRef(name=name, strength=ref.strength)
        contribution.specializations = list(merged_specs.values())


# ---------- outputs ----------

def build_terms(vocabulary: Vocabulary) -> list[CanonicalTerm]:
    return [
        CanonicalTerm(
            canonical=canonical,
            aliases=sorted(term for term in members if term != canonical),
            kind=vocabulary.kind,
        )
        for canonical, members in vocabulary.members().items()
    ]


def write_normalized(contribs: list[Contribution], path: Path) -> None:
    """Sorted by contribution_id so the output cannot depend on raw.jsonl line order."""
    rows = sorted(contribs, key=lambda c: c.contribution_id)
    with path.open("w", encoding="utf-8") as handle:
        for contribution in rows:
            handle.write(contribution.model_dump_json() + "\n")


def write_terms(vocabularies: list[Vocabulary], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for vocabulary in vocabularies:
            for term in build_terms(vocabulary):
                handle.write(term.model_dump_json() + "\n")


def cluster_size_distribution(vocabulary: Vocabulary) -> list[tuple[int, int]]:
    """[(cluster size, number of clusters)] ascending by size."""
    sizes = Counter(len(members) for members in vocabulary.members().values())
    return sorted(sizes.items())


def format_report(
    vocabularies: list[Vocabulary],
    *,
    n_contributions: int,
    n_skipped: int,
    n_clamped: int,
    overrides: Overrides,
    top_n: int,
) -> str:
    lines = [
        "# Stage 3 term review",
        "",
        f"{n_contributions} contributions normalized ({n_skipped} skipped, "
        f"{n_clamped} confidence-clamped to medium).",
        f"Overrides applied from {OVERRIDES_PATH.name}: "
        f"{len(overrides.never_merge)} never_merge pairs, "
        f"{len(overrides.force_alias)} force_alias entries.",
        "",
        "| vocabulary | mentions | unique terms | canonical terms | largest cluster | "
        "df floor | gated to alias |",
        "|---|---|---|---|---|---|---|",
    ]
    for vocabulary in vocabularies:
        members = vocabulary.members()
        largest = max((len(m) for m in members.values()), default=0)
        lines.append(
            f"| {vocabulary.kind} | {sum(vocabulary.counts.values())} | "
            f"{len(vocabulary.counts)} | {len(members)} | {largest} | "
            f"{vocabulary.min_document_frequency or '—'} | {vocabulary.gated_canonicals} |"
        )

    for vocabulary in vocabularies:
        frequencies = vocabulary.frequencies()
        members = vocabulary.members()
        lines += [
            "",
            f"## {vocabulary.kind}: cluster size distribution",
            "",
            "| terms in cluster | clusters |",
            "|---|---|",
        ]
        lines += [f"| {size} | {count} |" for size, count in cluster_size_distribution(vocabulary)]
        ranked = sorted(members, key=lambda c: (-frequencies[c], c))[:top_n]
        lines += [
            "",
            f"## {vocabulary.kind}: top {len(ranked)} canonical terms by frequency",
            "",
            "| # | canonical | mentions | terms | aliases |",
            "|---|---|---|---|---|",
        ]
        for rank, canonical in enumerate(ranked, start=1):
            aliases = [term for term in members[canonical] if term != canonical]
            lines.append(
                f"| {rank} | {canonical} | {frequencies[canonical]} | {len(members[canonical])} | "
                f"{'; '.join(aliases) if aliases else '—'} |"
            )
    return "\n".join(lines) + "\n"


def run(
    *,
    raw_path: Path | None = None,
    norm_path: Path | None = None,
    terms_path: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, int]:
    """Normalize one raw extraction into one vocabulary namespace.

    The paths default to the production artifacts, so ``main()`` and every existing
    caller behave exactly as before. They are parameters because a study that has to
    build a *second* vocabulary — the G3a document-frequency sweep of
    ``docs/work-orders/deterministic-sweeps.md`` — must write it somewhere other than
    ``data/contributions/``, and rebuilding it by copying this function is how two
    normalizations drift apart.
    """
    norm_path = NORM_PATH if norm_path is None else norm_path
    terms_path = TERMS_PATH if terms_path is None else terms_path
    report_path = REPORT_PATH if report_path is None else report_path
    contribs, n_skipped = load_contributions(raw_path)
    n_clamped = clamp_confidence(contribs)
    overrides = load_overrides()
    print(
        f"loaded {len(contribs)} contributions ({n_skipped} skipped, "
        f"{n_clamped} confidence-clamped to medium); overrides: "
        f"{len(overrides.never_merge)} never_merge, {len(overrides.force_alias)} force_alias"
    )

    vocabularies: list[Vocabulary] = []
    for kind, documents, threshold in [
        ("skill", [[s.name for s in c.skills] for c in contribs],
         settings["normalization.skill_merge_threshold"]),
        ("specialization", [[s.name for s in c.specializations] for c in contribs],
         settings["normalization.specialization_merge_threshold"]),
    ]:
        # Per-contribution term lists, so backlog G3a's document frequency counts
        # contributions rather than mentions. Flattened for the clustering input, which
        # is frequency-weighted and unchanged.
        refs = [name for document in documents for name in document]
        unique = len({term.strip() for term in refs if term and term.strip()})
        print(
            f"{kind}: {len(refs)} mentions, {unique} unique terms -> "
            f"{pairwise_matrix_gib(unique):.2f} GiB condensed pairwise matrix "
            f"(budget {float(settings['normalization.max_pairwise_matrix_gb']):.2f} GiB), "
            f"merge threshold {threshold}, document-frequency floor "
            f"{improvements.vocabulary_min_document_frequency()}"
        )
        vocabularies.append(
            cluster_terms(refs, threshold, kind, overrides, documents=documents)
        )

    skills, specializations = vocabularies
    apply_mappings(contribs, skills, specializations)
    for path in (norm_path, terms_path, report_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_normalized(contribs, norm_path)
    write_terms(vocabularies, terms_path)
    report_path.write_text(
        format_report(
            vocabularies,
            n_contributions=len(contribs),
            n_skipped=n_skipped,
            n_clamped=n_clamped,
            overrides=overrides,
            top_n=int(settings["normalization.report_top_n"]),
        ),
        encoding="utf-8",
    )
    print(
        f"{len(contribs)} contributions normalized; {len(skills.canonicals)} canonical skills, "
        f"{len(specializations.canonicals)} specializations -> review {report_path}"
    )
    return {
        "contributions": len(contribs),
        "skipped": n_skipped,
        "clamped": n_clamped,
        "skill_canonicals": len(skills.canonicals),
        "specialization_canonicals": len(specializations.canonicals),
        "skill_gated": skills.gated_canonicals,
        "specialization_gated": specializations.gated_canonicals,
        "min_document_frequency": improvements.vocabulary_min_document_frequency(),
    }


def main() -> None:
    run()


if __name__ == "__main__":
    main()
