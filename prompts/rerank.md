You are ranking candidates for a staffing need. You receive the brief, the parsed role, and the top candidates from retrieval, each with their capability profile (contributions, skills with evidence counts and last-used dates, and the evidence ticket keys behind each contribution).

<brief>
{{brief}}
</brief>

<role>
{{role_json}}
</role>

<candidates>
{{candidates_json}}
</candidates>

Rules:
- Rank ONLY the candidates given. Do not invent people. Include every candidate you can justify; omit none merely because it ranks low.
- Judge on: direct specialization fit, depth of relevant evidence (counts, recency), and breadth vs. the role's skill list. Prefer recent, repeated, directly relevant evidence over old or tangential evidence.
- `reason`: one concrete sentence per person citing their actual evidence ("14 tickets on Docker containerizer work across 2017-2018, including MESOS-1234"). A reviewer must be able to verify it against the profile shown.
- `evidence_ticket_keys`: 1-4 keys copied verbatim from that same candidate's own `evidence_tickets`, supporting exactly what the reason claims. Never cite a key belonging to another candidate, and never write a key that does not appear in that candidate's profile — entries whose citations are not the candidate's own are discarded, not corrected.
- `fit`: "strong" | "good" | "related". Use "related" for adjacent-but-not-direct matches.
- If a candidate is clearly unsuitable, include them at the bottom with fit "related" and an honest reason — never fabricate fit.

Return ONLY valid JSON:

```json
{
  "ranking": [
    {"person_id": "...", "fit": "strong", "reason": "...", "evidence_ticket_keys": ["PROJ-123", "PROJ-456"]}
  ]
}
```
