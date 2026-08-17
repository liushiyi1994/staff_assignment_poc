"""Pydantic contracts for every data shape crossing a stage boundary.

If a stage's output changes, change it here first — downstream stages and tests
depend on these models, not on ad-hoc dicts.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Strength = Literal["primary", "secondary"]
Confidence = Literal["high", "medium", "low"]
Fit = Literal["strong", "good", "related"]


# ---------- Stage 0/1: tickets and buckets ----------

class Ticket(BaseModel):
    source_issue_id: str              # stable TAWOS Issue.ID; keys/projects can move
    key: str
    project_key: str
    # ``person_*`` is the final-snapshot assignee kept for source audit only. It
    # is not profile evidence or benchmark truth because it may reflect a later
    # reassignment. Unassigned issues remain in the audit-complete Stage 0 export.
    person_id: str | None = None
    person_name: str | None = None
    # Profile ownership and benchmark truth are reconstructed independently from
    # the assignee change log at resolution time. Stage 1/benchmark use these.
    evidence_person_id: str | None = None
    evidence_person_name: str | None = None
    type: str | None = None
    summary: str
    summary_provenance: str = "snapshot_no_recorded_change"
    description: str | None = None    # markup-stripped, truncated
    description_provenance: str = "snapshot_no_recorded_change"
    components: list[str] = []
    components_provenance: str = "snapshot_no_recorded_change"
    labels: list[str] = []
    resolution: str | None = None
    snapshot_resolved_at: datetime | None = None  # final dump value, audit-only
    resolved_at: datetime | None = None
    resolved_at_provenance: str = "snapshot_no_recorded_resolution_change"
    created_at: datetime | None = None
    query_time_source: Literal["created_at"] = "created_at"
    temporal_exclusion_reason: str | None = None
    assigned_at: datetime | None = None
    assignee_provenance: str = "final_snapshot_no_recorded_change"
    # Benchmark v4 grouping key. ``Issue.Sprint_ID`` is the dump's *final* sprint
    # pointer with no recorded timing, so it is audit data on the same footing as
    # ``person_id``: it is redacted from the Stage 1 evidence view, and the v4
    # manifest reconstructs dated membership from the sprint change log instead
    # (data/parquet/sprint_membership.parquet).
    sprint_id: str | None = None
    sprint_provenance: str = "final_snapshot_unversioned"
    # Effort columns carried verbatim from TAWOS Issue, in the units the dump
    # records them in (unvalidated — nothing in this repo consumes them yet). They
    # are the only capacity signal the dataset offers; see backlog G10.
    story_point: float | None = None
    timespent: float | None = None
    in_progress_minutes: float | None = None
    total_effort_minutes: float | None = None


class Bucket(BaseModel):
    """Extraction unit: one person x project x period, stably size-chunked."""
    bucket_id: str                    # f"{person_id}|{project_key}|{period}|{split}"
    person_id: str
    person_name: str
    project_key: str
    project_domain: str = ""
    period: str                       # e.g. "2018-Q3"
    tickets: list[Ticket]


# ---------- Stage 2: LLM extraction output ----------

class SpecializationRef(BaseModel):
    name: str
    strength: Strength = "primary"


class SkillRef(BaseModel):
    name: str


class Contribution(BaseModel):
    """The core memory object. One per bucket (unless skipped)."""
    contribution_id: str              # = bucket_id
    person_id: str
    project_key: str
    period: str
    contribution_summary: str
    specializations: list[SpecializationRef]
    skills: list[SkillRef]
    confidence: Confidence
    reason: str
    evidence_ticket_keys: list[str]
    skip: bool = False
    skip_reason: str | None = None


# ---------- Stage 3: normalization ----------

class CanonicalTerm(BaseModel):
    canonical: str
    aliases: list[str] = []
    kind: Literal["skill", "specialization"]


# ---------- Stage 4: projections ----------

class PersonCapability(BaseModel):
    """Derived HAS_SKILL / HAS_SPECIALIZATION edge payload."""
    person_id: str
    term: str                         # canonical name
    kind: Literal["skill", "specialization"]
    evidence_count: int
    contribution_ids: list[str]
    last_used: date
    decay_score: float                # exp(-ln2 * days_since / half_life)
    # Backlog G6: how many of the supporting contributions called this specialization
    # `primary` rather than `secondary`. Always 0 for skills, which carry no strength.
    # Unrelated to CandidateProfile.structured_strength, which is a decayed evidence sum
    # computed in Python at query time — the collision is in the English word only.
    primary_evidence_count: int = 0

    @property
    def primary_share(self) -> float:
        """Share of this edge's evidence that called the term primary, in [0, 1]."""
        if self.kind != "specialization" or self.evidence_count <= 0:
            return 0.0
        return min(1.0, self.primary_evidence_count / self.evidence_count)


# ---------- Query engine ----------

class RoleSpec(BaseModel):
    role: str
    specializations: list[str] = []
    skills: list[str] = []
    count: int = 1


class Intent(BaseModel):
    roles: list[RoleSpec]
    domain: str = ""
    recency_years: float | None = None


class CandidateProfile(BaseModel):
    person_id: str
    person_name: str
    specializations: list[PersonCapability] = []
    skills: list[PersonCapability] = []
    contributions: list[Contribution] = []      # relevant subset only
    retrieval_sources: list[str] = []           # ["vector", "structured", "lexical"]
    # Arm provenance, kept so a shortlist entry can always be explained: which
    # contributions the vector arm surfaced and its best cosine, the summed decayed
    # evidence behind the structured arm's match, and the BM25 score of the lexical
    # arm's per-person evidence document.
    vector_hit_contribution_ids: list[str] = []
    vector_score: float = 0.0
    structured_strength: float = 0.0
    lexical_score: float = 0.0
    # Distinct contributions demonstrating any term the role asked for, counted once
    # each during expansion. Zero when expansion had no resolved terms to count.
    matched_contribution_count: int = 0
    # Backlog G11a: decayed recency of this person's last activity of *any* kind, at the
    # same as-of date and half-life as the `recency` component. Filled by expansion only
    # when improvements.activity_currency is on, and read by nothing otherwise.
    activity_currency: float = 0.0
    # Canonical terms of this person that satisfy the role's asks (set by scoring).
    matched_specializations: list[str] = []
    matched_skills: list[str] = []
    score: float = 0.0
    score_parts: dict[str, float] = {}


class RankedPerson(BaseModel):
    person_id: str
    person_name: str
    fit: Fit
    reason: str
    score: float
    found_by: list[str] = []                    # retrieval arms, carried to output
    matched_specializations: list[str] = []
    matched_skills: list[str] = []
    evidence_ticket_keys: list[str] = []


class ShortlistResult(BaseModel):
    role: RoleSpec
    ranking: list[RankedPerson]
    # Re-rank entries dropped by validation (invented person, unusable evidence).
    rejected: list[str] = []
    candidate_counts: dict[str, int] = {}       # per retrieval arm, union, re-ranked
    resolved_terms: dict[str, list[str]] = {}   # requested term -> canonical terms
    # The two orderings behind `ranking`, kept so a result can be audited and so the
    # benchmark can score the deterministic stage on its own: the whole union pool
    # (candidate recall) and that pool ordered by weighted score alone (no LLM).
    candidate_person_ids: list[str] = []
    scored_person_ids: list[str] = []
    # Backlog G8: RoleSpec.count is parsed by the intent prompt and was read by nothing.
    # The first `count` entries of `ranking` are the proposed set for the role and the
    # rest are alternates. Presentation only — no score, ordering, or retrieval changes,
    # and team composition (preferring complementary coverage over `count` near
    # duplicates) is deliberately out of scope until there is multi-person ground truth.
    proposed_person_ids: list[str] = []
    alternate_person_ids: list[str] = []


class QueryResult(BaseModel):
    brief: str
    intent: Intent
    shortlists: list[ShortlistResult]
    timings_ms: dict[str, float] = {}


# ---------- Eval ----------

class EvalBrief(BaseModel):
    """Legacy selected-case view; the versioned benchmark manifest is authoritative."""

    brief_id: str
    text: str                         # name-stripped
    project_key: str
    as_of_time: datetime | None = None  # query time; normally issue creation
    resolved_at: datetime | None = None  # outcome metadata, never query time
    eligible_roster: list[str] = []
    true_person_ids: list[str]        # ground truth assignee(s)


class EvalResult(BaseModel):
    system: str                       # "capgraph" | "bm25" | "vector_only" | "most_active"
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    candidate_recall: float | None = None
    n_briefs: int
    latency_ms_mean: float = 0.0
    latency_ms_median: float = 0.0
    latency_ms_p95: float = 0.0
    cost_usd_total: float = 0.0
