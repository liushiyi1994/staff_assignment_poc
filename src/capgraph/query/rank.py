"""Query step 4: deterministic weighted score, then LLM re-rank of top-K with reasons.

The weighted score is implemented and unit-tested here (dataset-independent).
The LLM re-rank consumes prompts/rerank.md.
"""
from __future__ import annotations

import json

from ..llm import call_json
from ..models import CandidateProfile, RankedPerson, RoleSpec
from ..settings import load_prompt, settings


def _norm(term: str) -> str:
    return term.strip().lower()


def score_candidate(candidate: CandidateProfile, role: RoleSpec) -> CandidateProfile:
    """Transparent weighted score in [0,1]. Parts kept for explainability."""
    w = settings["scoring.weights"]
    want_specs = {_norm(s) for s in role.specializations}
    want_skills = {_norm(s) for s in role.skills}

    have_specs = {_norm(c.term): c for c in candidate.specializations}
    have_skills = {_norm(c.term): c for c in candidate.skills}

    spec_hits = want_specs & set(have_specs)
    skill_hits = want_skills & set(have_skills)

    spec_match = len(spec_hits) / len(want_specs) if want_specs else 0.0
    skill_overlap = len(skill_hits) / len(want_skills) if want_skills else 0.0

    matched_caps = [have_specs[s] for s in spec_hits] + [have_skills[s] for s in skill_hits]
    recency = max((c.decay_score for c in matched_caps), default=0.0)
    # log-ish saturation: 1 piece of evidence ≈ 0.33, 5 ≈ 0.78, 10+ ≈ 1.0
    total_evidence = sum(c.evidence_count for c in matched_caps)
    evidence_strength = min(1.0, (total_evidence ** 0.5) / 3.2) if total_evidence else 0.0

    parts = {
        "specialization_match": spec_match,
        "skill_overlap": skill_overlap,
        "recency": recency,
        "evidence_strength": evidence_strength,
    }
    candidate.score_parts = parts
    candidate.score = round(sum(w[k] * v for k, v in parts.items()), 4)
    return candidate


def rerank(brief: str, role: RoleSpec, candidates: list[CandidateProfile]) -> list[RankedPerson]:
    top_k = settings["retrieval.rerank_top_k"]
    ranked_input = sorted(candidates, key=lambda c: -c.score)[:top_k]

    def profile_view(c: CandidateProfile) -> dict:
        return {
            "person_id": c.person_id,
            "person_name": c.person_name,
            "score": c.score,
            "specializations": [
                {"term": s.term, "evidence_count": s.evidence_count, "last_used": str(s.last_used)}
                for s in c.specializations],
            "skills": [
                {"term": s.term, "evidence_count": s.evidence_count, "last_used": str(s.last_used)}
                for s in sorted(c.skills, key=lambda x: -x.evidence_count)[:15]],
            "contributions": [
                {"summary": k.contribution_summary, "period": k.period,
                 "evidence_tickets": k.evidence_ticket_keys}
                for k in c.contributions[:6]],
        }

    raw = call_json(
        load_prompt(
            "rerank",
            brief=brief,
            role_json=role.model_dump_json(),
            candidates_json=json.dumps([profile_view(c) for c in ranked_input], indent=1),
        ),
        model=settings["llm.rerank_model"], stage="query", max_tokens=3000,
    )
    by_id = {c.person_id: c for c in ranked_input}
    out: list[RankedPerson] = []
    for entry in raw.get("ranking", []):
        c = by_id.get(entry.get("person_id"))
        if c is None:
            continue  # model invented an id — drop it
        want_specs = {_norm(s) for s in role.specializations}
        want_skills = {_norm(s) for s in role.skills}
        out.append(RankedPerson(
            person_id=c.person_id, person_name=c.person_name,
            fit=entry.get("fit", "related"), reason=entry.get("reason", ""),
            score=c.score,
            matched_specializations=[s.term for s in c.specializations if _norm(s.term) in want_specs],
            matched_skills=[s.term for s in c.skills if _norm(s.term) in want_skills],
            evidence_ticket_keys=[k for contrib in c.contributions[:3] for k in contrib.evidence_ticket_keys][:8],
        ))
    return out
