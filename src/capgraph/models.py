"""Pydantic contracts for every data shape crossing a stage boundary.

If a stage's output changes, change it here first — downstream stages and tests
depend on these models, not on ad-hoc dicts.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Strength = Literal["primary", "secondary"]
Confidence = Literal["high", "medium", "low"]
Fit = Literal["strong", "good", "related"]


# ---------- Stage 0/1: tickets and buckets ----------

class Ticket(BaseModel):
    key: str
    project_key: str
    person_id: str                    # normalized assignee id
    person_name: str
    type: str | None = None
    summary: str
    description: str | None = None    # markup-stripped, truncated
    components: list[str] = []
    labels: list[str] = []
    resolution: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime | None = None


class Bucket(BaseModel):
    """Extraction unit: one person x project x period (optionally split by component)."""
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
    retrieval_sources: list[str] = []           # ["vector", "structured"]
    score: float = 0.0
    score_parts: dict[str, float] = {}


class RankedPerson(BaseModel):
    person_id: str
    person_name: str
    fit: Fit
    reason: str
    score: float
    matched_specializations: list[str] = []
    matched_skills: list[str] = []
    evidence_ticket_keys: list[str] = []


class ShortlistResult(BaseModel):
    role: RoleSpec
    ranking: list[RankedPerson]


class QueryResult(BaseModel):
    brief: str
    intent: Intent
    shortlists: list[ShortlistResult]
    timings_ms: dict[str, float] = {}


# ---------- Eval ----------

class EvalBrief(BaseModel):
    brief_id: str
    text: str                         # name-stripped
    project_key: str
    resolved_at: datetime
    true_person_ids: list[str]        # ground truth assignee(s)


class EvalResult(BaseModel):
    system: str                       # "capgraph" | "bm25" | "vector_only" | "most_active"
    recall_at_5: float
    recall_at_10: float
    mrr: float
    n_briefs: int
