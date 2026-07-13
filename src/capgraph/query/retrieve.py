"""Query steps 2-3: candidate generation (vector ∪ structured) + subgraph expansion.

Contract:
  generate_candidates(role, brief_text, driver) -> list[CandidateProfile]
    - vector arm: embed role+brief, query Neo4j vector index `contribution_embedding`
      (top retrieval.vector_top_k contributions) -> owning Persons
    - structured arm: Cypher matching role.specializations/skills against
      HAS_SPECIALIZATION / HAS_SKILL edges (alias-aware: also match Skill.aliases),
      ordered by evidence_count * decay_score, top structured_top_k
    - UNION by person_id, tag retrieval_sources ["vector"], ["structured"], or both
  expand(candidates, driver) -> fills each CandidateProfile with its capabilities and
    the relevant Contributions (those retrieved by vector arm + top-N by evidence).

TODO(claude-code): implement per implementation plan Task 6. Keep Cypher in this module,
parameterized, no string interpolation of user input.
"""
from __future__ import annotations

from ..models import CandidateProfile, RoleSpec


def generate_candidates(role: RoleSpec, brief_text: str, driver) -> list[CandidateProfile]:
    raise NotImplementedError


def expand(candidates: list[CandidateProfile], driver) -> list[CandidateProfile]:
    raise NotImplementedError


def known_specializations(driver) -> list[str]:
    with driver.session() as session:
        result = session.run("MATCH (s:Specialization) RETURN s.name AS name")
        return [r["name"] for r in result]
