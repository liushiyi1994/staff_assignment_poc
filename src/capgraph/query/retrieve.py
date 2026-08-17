"""Query steps 2-3: candidate generation (vector ∪ structured ∪ lexical) + expansion.

Three retrievers, unioned — never intersected (non-negotiable #3). The vector arm
catches phrasing the emergent vocabulary missed; the structured arm catches people
whose contribution summaries read dry but whose capability edges are strong; the
lexical arm (benchmark v3) catches the person whose evidence uses the brief's own
words but whose extracted profile does not. Which arm found a candidate is kept on the
profile (``retrieval_sources``) and carried all the way to the printed shortlist, so a
result can always be explained.

The lexical arm is BM25 over the Stage 1 per-person evidence documents — the same
index :mod:`capgraph.lexical` builds for the benchmark's ``bm25`` baseline, queried
with the same brief text, so the arm is that baseline truncated to its top
``retrieval.bm25_top_k`` rather than a second lexical retriever that could drift from
it. Setting that width to 0 disables the arm and restores the v1/v2 two-arm union. It
is a **union** member, never a fusion: benchmark v2 measured vanilla RRF of the graph
ranking with BM25 dragging the stronger list down.

Term matching happens exactly once, in :func:`resolve_terms`: free-text intent terms
are resolved against canonical names *and* their Stage 3 aliases in one Cypher query,
and every downstream step (structured lookup, scoring, expansion ordering) consumes
the resolved canonical names. There is deliberately no second matcher implemented in
Python that could drift from the Cypher one.

All Cypher lives here and is parameterized: no user text is ever interpolated into a
query string. The two label-templated statements are formatted only from the fixed
``LABELS`` allowlist.

Two optional inputs exist for the temporal benchmark and are inert in normal use:

* ``roster`` restricts both arms to a frozen set of person ids — a Cypher parameter
  in the structured arm and in the vector arm's post-index filter. A benchmark case
  may only be answered from its own project's eligible roster.
* ``as_of`` recomputes every capability edge's recency from its stored ``last_used``
  at that date, through Stage 4's :func:`decay`. The graph stores decay frozen at the
  holdout cutoff, which is *earlier* than any benchmark query time, so the stored
  value is never used for scoring here. ``as_of`` defaults to the same cutoff
  snapshot, so the ordinary query path is unchanged (up to the stored value's 4-dp
  rounding) and there is exactly one decay implementation in the repository.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import improvements
from ..models import (
    CandidateProfile,
    Contribution,
    PersonCapability,
    RoleSpec,
    SkillRef,
    SpecializationRef,
)
from ..pipeline.stage4_project import decay, snapshot_date
from ..pipeline.stage5_graph import VECTOR_INDEX
from ..settings import settings

ARM_VECTOR = "vector"
ARM_STRUCTURED = "structured"
ARM_LEXICAL = "lexical"
ARMS = (ARM_VECTOR, ARM_STRUCTURED, ARM_LEXICAL)

SKILL = "skill"
SPECIALIZATION = "specialization"
LABELS = {SKILL: "Skill", SPECIALIZATION: "Specialization"}
CAPABILITY_RELATIONSHIPS = {SKILL: "HAS_SKILL", SPECIALIZATION: "HAS_SPECIALIZATION"}

# A one-character term ("R", "C") matches far too much even on a word boundary, so it
# is resolved by exact name/alias only. Lexical rule, not a tuning knob.
MIN_PATTERN_TERM_CHARS = 2

# Java-regex metacharacters. The pattern is passed as a *parameter*, never spliced
# into the statement, but it still has to be a literal match for the term itself.
_REGEX_META = re.compile(r"([\\.\[\]{}()*+?^$|/])")


# ---------- Cypher ----------

# Alias-aware term resolution. `=~` is a full-string match in Cypher, so the pattern
# carries its own `.*` padding; both sides are lower-cased before comparison.
RESOLVE_TERMS = """
UNWIND $terms AS term
MATCH (t:{label})
WHERE any(name IN [t.name] + coalesce(t.aliases, [])
          WHERE toLower(name) = term.exact
             OR (term.pattern IS NOT NULL AND toLower(name) =~ term.pattern))
RETURN term.exact AS term, collect(DISTINCT t.name) AS canonical
"""

# The index query itself cannot be pre-filtered, so a roster-restricted search asks
# for a pool wider than the corpus and filters inside the same statement; the caller
# then keeps the top `retrieval.vector_top_k` surviving rows. That is exact, not
# approximate, as long as the pool exceeds the number of indexed contributions.
VECTOR_CANDIDATES = """
CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score
MATCH (p:Person)-[:MADE]->(node)
WHERE $roster IS NULL OR p.id IN $roster
RETURN p.id AS person_id, p.pseudonym AS person_name,
       node.id AS contribution_id, score
ORDER BY score DESC, contribution_id ASC
"""

# One person may match several terms across both capability kinds. The edges are
# returned raw — evidence count and last-used date — and the decayed strength is
# summed in Python through Stage 4's decay(), so recency has one implementation and
# can be recomputed at a benchmark query time. Ties break on person_id.
STRUCTURED_CANDIDATES = """
CALL () {
    UNWIND $specialization_terms AS term
    MATCH (p:Person)-[h:HAS_SPECIALIZATION]->(:Specialization {name: term})
    WHERE $roster IS NULL OR p.id IN $roster
    RETURN p AS person, h.evidence_count AS evidence_count, h.last_used AS last_used,
           term AS matched
  UNION ALL
    UNWIND $skill_terms AS term
    MATCH (p:Person)-[h:HAS_SKILL]->(:Skill {name: term})
    WHERE $roster IS NULL OR p.id IN $roster
    RETURN p AS person, h.evidence_count AS evidence_count, h.last_used AS last_used,
           term AS matched
}
RETURN person.id AS person_id, person.pseudonym AS person_name,
       evidence_count, last_used, matched
ORDER BY person_id ASC, matched ASC
"""

# How much evidence actually stands behind the match: contributions counted once each,
# however many matched terms they demonstrate. Summing the edges' evidence_count would
# count one contribution as many times as it has matched terms.
COUNT_MATCHED_CONTRIBUTIONS = """
UNWIND $person_ids AS person_id
MATCH (:Person {id: person_id})-[:MADE]->(c:Contribution)-[:DEMONSTRATES]->(t)
WHERE t.name IN $matched_terms
RETURN person_id AS person_id, count(DISTINCT c) AS supporting
"""

# primary_evidence_count (backlog G6) is coalesced: a graph loaded before Stage 5 began
# writing it answers 0, which is what the flag-off score already assumes.
EXPAND_CAPABILITIES = """
UNWIND $person_ids AS person_id
MATCH (p:Person {id: person_id})-[h:HAS_SKILL|HAS_SPECIALIZATION]->(t)
RETURN person_id AS person_id, t.name AS term,
       CASE WHEN t:Specialization THEN 'specialization' ELSE 'skill' END AS kind,
       h.evidence_count AS evidence_count, h.last_used AS last_used,
       coalesce(h.primary_evidence_count, 0) AS primary_evidence_count
ORDER BY person_id ASC, kind ASC, evidence_count DESC, term ASC
"""

# Backlog G11a: when the last retained bucket of *any* kind ended, per person. Stage 5
# already stores it as Person.active_to, so activity currency needs no new projection —
# only this lookup, which runs solely when the flag is on.
PERSON_ACTIVITY = """
UNWIND $person_ids AS person_id
MATCH (p:Person {id: person_id})
WHERE p.active_to IS NOT NULL
RETURN p.id AS person_id, p.active_to AS active_to
"""

# Which of a person's contributions to carry into scoring and the re-rank context:
# the ones the vector arm surfaced first, then the ones demonstrating a matched term,
# then the most recent. Contribution ids break ties so re-runs are identical.
SELECT_CONTRIBUTIONS = """
UNWIND $rows AS row
MATCH (:Person {id: row.person_id})-[:MADE]->(c:Contribution)
OPTIONAL MATCH (c)-[:DEMONSTRATES]->(t)
WHERE t.name IN $matched_terms
WITH row, c, count(DISTINCT t) AS matched_hits,
     CASE WHEN c.id IN row.hit_ids THEN 1 ELSE 0 END AS vector_hit
ORDER BY vector_hit DESC, matched_hits DESC, c.period DESC, c.id ASC
RETURN row.person_id AS person_id, collect(c.id)[0..$per_person] AS contribution_ids
"""

FETCH_CONTRIBUTIONS = """
UNWIND $ids AS contribution_id
MATCH (p:Person)-[:MADE]->(c:Contribution {id: contribution_id})-[:ON]->(pr:Project)
OPTIONAL MATCH (c)-[d:DEMONSTRATES]->(t)
RETURN c.id AS id, p.id AS person_id, pr.key AS project_key, c.period AS period,
       c.summary AS summary, c.confidence AS confidence,
       c.evidence_ticket_keys AS evidence_ticket_keys,
       collect(DISTINCT {
         name: t.name,
         kind: CASE WHEN t:Specialization THEN 'specialization' ELSE 'skill' END,
         strength: d.strength
       }) AS terms
"""

KNOWN_SPECIALIZATIONS = "MATCH (s:Specialization) RETURN s.name AS name ORDER BY name"


# ---------- term resolution ----------

def normalize_term(term: str) -> str:
    """Lower-case, whitespace-collapsed comparison form for a free-text term."""
    return " ".join(str(term).split()).lower()


def term_pattern(term: str) -> str | None:
    """Word-boundary regex for one normalized term, or None when it is too short.

    ``\\b`` is dropped at an end that is not alphanumeric ("c++"), where it would
    otherwise demand a word character that cannot be there.
    """
    if len(term) < MIN_PATTERN_TERM_CHARS:
        return None
    escaped = _REGEX_META.sub(r"\\\1", term)
    prefix = r".*\b" if term[0].isalnum() or term[0] == "_" else ".*"
    suffix = r"\b.*" if term[-1].isalnum() or term[-1] == "_" else ".*"
    return f"{prefix}{escaped}{suffix}"


def term_params(terms: Iterable[str]) -> list[dict[str, str | None]]:
    """Deduplicated {exact, pattern} rows for RESOLVE_TERMS, in stable order."""
    params: dict[str, dict[str, str | None]] = {}
    for raw in terms:
        term = normalize_term(raw)
        if term and term not in params:
            params[term] = {"exact": term, "pattern": term_pattern(term)}
    return list(params.values())


@dataclass(frozen=True)
class TermResolution:
    """Requested term -> the canonical graph terms that satisfy it, per kind."""

    specializations: dict[str, list[str]] = field(default_factory=dict)
    skills: dict[str, list[str]] = field(default_factory=dict)

    def canonical(self, kind: str) -> list[str]:
        source = self.specializations if kind == SPECIALIZATION else self.skills
        return sorted({term for terms in source.values() for term in terms})

    def all_canonical(self) -> list[str]:
        return sorted(set(self.canonical(SPECIALIZATION)) | set(self.canonical(SKILL)))

    def is_empty(self) -> bool:
        return not (self.specializations or self.skills)


def _run(driver, statement: str, **params) -> list[dict]:
    with driver.session() as session:
        return [dict(record) for record in session.run(statement, **params)]


def _as_date(value) -> date:
    """Neo4j temporal values are not datetime.date subclasses; normalize them."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    to_native = getattr(value, "to_native", None)
    if to_native is not None:
        return _as_date(to_native())
    return date.fromisoformat(str(value))


def recency_as_of(as_of: date | datetime | None) -> date:
    """The date recency is measured from: the caller's, or the frozen graph snapshot."""
    return snapshot_date() if as_of is None else _as_date(as_of)


def _decay_at(last_used, as_of: date) -> float:
    """Stage 4's decay for one stored edge, recomputed at ``as_of``."""
    return decay(
        _as_date(last_used), int(settings["projections.recency_half_life_days"]), as_of=as_of
    )


def _roster_param(roster: Sequence[str] | None) -> list[str] | None:
    """A roster travels as a Cypher parameter, or as null when unrestricted."""
    return None if roster is None else sorted({str(person_id) for person_id in roster})


def resolve_terms(terms: Sequence[str], kind: str, driver) -> dict[str, list[str]]:
    """Map free-text terms onto canonical names, matching names and aliases alike."""
    if kind not in LABELS:
        raise ValueError(f"unknown term kind {kind!r}")
    params = term_params(terms)
    if not params:
        return {}
    rows = _run(driver, RESOLVE_TERMS.format(label=LABELS[kind]), terms=params)
    return {row["term"]: sorted(row["canonical"]) for row in rows if row["canonical"]}


def resolve_role_terms(role: RoleSpec, driver) -> TermResolution:
    return TermResolution(
        specializations=resolve_terms(role.specializations, SPECIALIZATION, driver),
        skills=resolve_terms(role.skills, SKILL, driver),
    )


# ---------- arms ----------

def query_text(role: RoleSpec, brief_text: str) -> str:
    """Vector-arm query text: the brief plus this role's terms (design §6 embeds
    "the brief (and each role)"), so a two-role brief does not retrieve twice the
    same way."""
    parts = [brief_text.strip()]
    if role.role.strip():
        parts.append(f"Role: {role.role.strip()}")
    if role.specializations:
        parts.append("Specializations: " + ", ".join(role.specializations))
    if role.skills:
        parts.append("Skills: " + ", ".join(role.skills))
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class VectorHit:
    person_id: str
    person_name: str
    contribution_ids: tuple[str, ...]
    best_score: float


def vector_candidates(
    role: RoleSpec,
    brief_text: str,
    driver,
    *,
    embed_fn=None,
    roster: Sequence[str] | None = None,
) -> list[VectorHit]:
    """Top contributions by cosine over the local embedding, mapped to their Persons.

    Restricted to ``roster`` when one is given: the index is asked for a pool wider
    than the corpus, the statement filters it, and the top ``retrieval.vector_top_k``
    surviving contributions are kept — the same count the unrestricted arm returns.
    """
    if embed_fn is None:
        from ..embeddings import embed as embed_fn
    vector = [float(value) for value in embed_fn([query_text(role, brief_text)])[0]]
    top_k = int(settings["retrieval.vector_top_k"])
    rows = _run(
        driver,
        VECTOR_CANDIDATES,
        index=VECTOR_INDEX,
        k=top_k if roster is None else int(settings["retrieval.roster_vector_pool_k"]),
        vec=vector,
        roster=_roster_param(roster),
    )[:top_k]

    hits: dict[str, dict] = {}
    for row in rows:
        hit = hits.setdefault(
            row["person_id"],
            {"person_name": row["person_name"], "ids": [], "best": float(row["score"])},
        )
        hit["ids"].append(row["contribution_id"])
        hit["best"] = max(hit["best"], float(row["score"]))
    return [
        VectorHit(
            person_id=person_id,
            person_name=hit["person_name"],
            contribution_ids=tuple(hit["ids"]),
            best_score=hit["best"],
        )
        for person_id, hit in sorted(hits.items(), key=lambda kv: (-kv[1]["best"], kv[0]))
    ]


def structured_candidates(
    resolution: TermResolution,
    driver,
    *,
    roster: Sequence[str] | None = None,
    as_of: date | datetime | None = None,
) -> list[dict]:
    """People whose capability edges carry the resolved terms, by decayed evidence.

    Strength sums ``evidence_count * decay(last_used)`` over every matched edge, with
    decay measured at ``as_of``. Top ``retrieval.structured_top_k`` by strength, ties
    on person_id.
    """
    specializations = resolution.canonical(SPECIALIZATION)
    skills = resolution.canonical(SKILL)
    if not (specializations or skills):
        return []
    rows = _run(
        driver,
        STRUCTURED_CANDIDATES,
        specialization_terms=specializations,
        skill_terms=skills,
        roster=_roster_param(roster),
    )
    snapshot = recency_as_of(as_of)

    matches: dict[str, dict] = {}
    for row in rows:
        match = matches.setdefault(
            row["person_id"],
            {"person_name": row["person_name"], "strength": 0.0, "matched_terms": set()},
        )
        match["strength"] += int(row["evidence_count"]) * _decay_at(row["last_used"], snapshot)
        match["matched_terms"].add(row["matched"])

    ordered = sorted(matches.items(), key=lambda item: (-item[1]["strength"], item[0]))
    return [
        {
            "person_id": person_id,
            "person_name": match["person_name"],
            "strength": match["strength"],
            "matched_terms": sorted(match["matched_terms"]),
        }
        for person_id, match in ordered[: int(settings["retrieval.structured_top_k"])]
    ]


def lexical_candidates(
    brief_text: str,
    index=None,
    *,
    roster: Sequence[str] | None = None,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """Top ``retrieval.bm25_top_k`` people by BM25 over their evidence documents.

    The query text is the brief alone, deliberately not the role-augmented text the
    vector arm embeds: this arm exists to reproduce the benchmark's ``bm25`` ranking
    inside the union, and that baseline is scored on the brief. Returns
    ``(person_id, score)`` pairs; an empty list when the arm is switched off, when no
    index was supplied, or when nothing scored.
    """
    width = int(settings["retrieval.bm25_top_k"]) if top_k is None else int(top_k)
    if width < 1:
        return []
    if index is None:
        from ..lexical import default_person_index

        index = default_person_index()
    return index.top_people(brief_text, k=width, roster=roster)


def union_candidates(
    vector: Sequence[VectorHit],
    structured: Sequence[Mapping],
    lexical: Sequence[tuple[str, float]] = (),
    *,
    names: Mapping[str, str] | None = None,
) -> list[CandidateProfile]:
    """Union the arms, keeping which arm(s) found each person. Never an intersection.

    ``names`` supplies a pseudonym for a person only the lexical arm found, since BM25
    knows person ids and not graph properties. A person with no name there keeps their
    id as the display name until expansion is asked for one.
    """
    profiles: dict[str, CandidateProfile] = {}
    for hit in vector:
        profiles[hit.person_id] = CandidateProfile(
            person_id=hit.person_id,
            person_name=hit.person_name,
            retrieval_sources=[ARM_VECTOR],
            vector_hit_contribution_ids=list(hit.contribution_ids),
            vector_score=round(hit.best_score, 4),
        )
    for row in structured:
        profile = profiles.get(row["person_id"])
        if profile is None:
            profile = CandidateProfile(
                person_id=row["person_id"], person_name=row["person_name"]
            )
            profiles[row["person_id"]] = profile
        sources = set(profile.retrieval_sources) | {ARM_STRUCTURED}
        profile.retrieval_sources = [arm for arm in ARMS if arm in sources]
        profile.structured_strength = round(float(row["strength"]), 4)
    for person_id, score in lexical:
        profile = profiles.get(person_id)
        if profile is None:
            profile = CandidateProfile(
                person_id=person_id,
                person_name=(names or {}).get(person_id, person_id),
            )
            profiles[person_id] = profile
        sources = set(profile.retrieval_sources) | {ARM_LEXICAL}
        profile.retrieval_sources = [arm for arm in ARMS if arm in sources]
        profile.lexical_score = round(float(score), 4)
    return [profiles[person_id] for person_id in sorted(profiles)]


PERSON_NAMES = """
UNWIND $person_ids AS person_id
MATCH (p:Person {id: person_id})
RETURN p.id AS person_id, p.pseudonym AS person_name
"""


def person_names(person_ids: Sequence[str], driver) -> dict[str, str]:
    """Pseudonyms for people the lexical arm found outside the other two arms."""
    if not person_ids:
        return {}
    return {
        row["person_id"]: row["person_name"]
        for row in _run(driver, PERSON_NAMES, person_ids=sorted(set(person_ids)))
    }


def generate_candidates(
    role: RoleSpec,
    brief_text: str,
    driver,
    *,
    resolution: TermResolution | None = None,
    embed_fn=None,
    roster: Sequence[str] | None = None,
    as_of: date | datetime | None = None,
    lexical_index=None,
) -> list[CandidateProfile]:
    """Vector ∪ structured ∪ lexical, with arm provenance on every profile."""
    if resolution is None:
        resolution = resolve_role_terms(role, driver)
    vector = vector_candidates(role, brief_text, driver, embed_fn=embed_fn, roster=roster)
    structured = structured_candidates(resolution, driver, roster=roster, as_of=as_of)
    lexical = lexical_candidates(brief_text, lexical_index, roster=roster)
    found = {hit.person_id for hit in vector} | {row["person_id"] for row in structured}
    names = person_names(
        [person_id for person_id, _ in lexical if person_id not in found], driver
    )
    return union_candidates(vector, structured, lexical, names=names)


# ---------- expansion ----------

def _capability(row: Mapping, as_of: date) -> PersonCapability:
    last_used = _as_date(row["last_used"])
    return PersonCapability(
        person_id=row["person_id"],
        term=row["term"],
        kind=row["kind"],
        evidence_count=int(row["evidence_count"]),
        # The projection's source contribution ids are not stored on the edge; the
        # evidence itself is reachable through MADE -> Contribution below.
        contribution_ids=[],
        last_used=last_used,
        # Never the edge's stored decay: that is frozen at the graph cutoff, which is
        # earlier than any benchmark query time.
        decay_score=round(_decay_at(last_used, as_of), 4),
        primary_evidence_count=int(row.get("primary_evidence_count") or 0),
    )


def _contribution(row: Mapping) -> Contribution:
    terms = [term for term in row["terms"] if term.get("name")]
    return Contribution(
        contribution_id=row["id"],
        person_id=row["person_id"],
        project_key=row["project_key"],
        period=row["period"],
        contribution_summary=row["summary"],
        specializations=[
            SpecializationRef(name=t["name"], strength=t.get("strength") or "primary")
            for t in sorted(terms, key=lambda t: t["name"])
            if t["kind"] == SPECIALIZATION
        ],
        skills=[
            SkillRef(name=t["name"])
            for t in sorted(terms, key=lambda t: t["name"])
            if t["kind"] == SKILL
        ],
        confidence=row["confidence"],
        # The extractor's free-text rationale is not loaded into the graph (it is not
        # evidence); the evidence pointers below are.
        reason="",
        evidence_ticket_keys=list(row["evidence_ticket_keys"]),
    )


def expand(
    candidates: Sequence[CandidateProfile],
    driver,
    *,
    resolution: TermResolution | None = None,
    as_of: date | datetime | None = None,
) -> list[CandidateProfile]:
    """Fill each candidate's capability edges and its most relevant contributions."""
    if not candidates:
        return []
    person_ids = [c.person_id for c in candidates]
    by_person = {c.person_id: c for c in candidates}
    snapshot = recency_as_of(as_of)

    for row in _run(driver, EXPAND_CAPABILITIES, person_ids=person_ids):
        capability = _capability(row, snapshot)
        profile = by_person[row["person_id"]]
        if capability.kind == SPECIALIZATION:
            profile.specializations.append(capability)
        else:
            profile.skills.append(capability)

    # Backlog G11a. The extra round trip runs only when the flag is on, so an expansion
    # with the flag off issues exactly the queries v1-v3 issued.
    if improvements.activity_currency_mode() != improvements.OFF:
        for row in _run(driver, PERSON_ACTIVITY, person_ids=person_ids):
            by_person[row["person_id"]].activity_currency = round(
                _decay_at(row["active_to"], snapshot), 4
            )

    matched_terms = [] if resolution is None else resolution.all_canonical()
    if matched_terms:
        for row in _run(
            driver,
            COUNT_MATCHED_CONTRIBUTIONS,
            person_ids=person_ids,
            matched_terms=matched_terms,
        ):
            by_person[row["person_id"]].matched_contribution_count = int(row["supporting"])

    selection = _run(
        driver,
        SELECT_CONTRIBUTIONS,
        rows=[
            {"person_id": c.person_id, "hit_ids": list(c.vector_hit_contribution_ids)}
            for c in candidates
        ],
        matched_terms=matched_terms,
        per_person=int(settings["retrieval.contributions_per_person"]),
    )
    selected = {row["person_id"]: list(row["contribution_ids"]) for row in selection}
    wanted = [cid for person_id in person_ids for cid in selected.get(person_id, [])]
    if wanted:
        fetched = {
            row["id"]: _contribution(row)
            for row in _run(driver, FETCH_CONTRIBUTIONS, ids=wanted)
        }
        for person_id, ids in selected.items():
            by_person[person_id].contributions = [
                fetched[cid] for cid in ids if cid in fetched
            ]
    return list(candidates)


def known_specializations(driver) -> list[str]:
    return [row["name"] for row in _run(driver, KNOWN_SPECIALIZATIONS)]
