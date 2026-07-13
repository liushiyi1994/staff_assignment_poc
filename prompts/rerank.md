You are ranking candidates for a staffing need. You receive the brief, the parsed role, and the top candidates from retrieval, each with their capability profile (contributions, skills with evidence counts and last-used dates).

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
- Rank ONLY the candidates given. Do not invent people.
- Judge on: direct specialization fit, depth of relevant evidence (counts, recency), and breadth vs. the role's skill list. Prefer recent, repeated, directly relevant evidence over old or tangential evidence.
- `reason`: one concrete sentence per person citing their actual evidence ("14 resolved tickets on X in 2018-2019, led Y"). A reviewer must be able to verify it against the profile shown.
- `fit`: "strong" | "good" | "related". Use "related" for adjacent-but-not-direct matches.
- If a candidate is clearly unsuitable, include them at the bottom with fit "related" and an honest reason — never fabricate fit.

Return ONLY valid JSON:

```json
{
  "ranking": [
    {"person_id": "...", "fit": "strong", "reason": "..."}
  ]
}
```
