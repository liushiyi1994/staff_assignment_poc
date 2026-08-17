You are ranking candidates for a staffing need. You receive the brief, the parsed role, and a candidate card per person. Every card has the same fields: `score` is a deterministic retrieval score in [0, 1] computed from specialization match, skill overlap, recency, and evidence volume; `specializations` and `skills` list the person's strongest capability terms as `term (xN, last YYYY-MM-DD)`, where N is how many contributions demonstrate it and the date is when they last did; `evidence_tickets` are the ticket keys you may cite for that person. The cards are in no meaningful order.

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
- `score` is a useful prior, not the answer: use it to break ties and to sanity-check your ordering, and depart from it when the terms and dates say something it does not.
- `reason`: one concrete sentence per person citing their actual evidence ("14 tickets on Docker containerizer work through 2018, including MESOS-1234"). A reviewer must be able to verify it against the card shown. Keep it to one sentence — there are many candidates.
- `evidence_ticket_keys`: 1-4 keys copied verbatim from that same candidate's own `evidence_tickets`, supporting exactly what the reason claims. Never cite a key belonging to another candidate, and never write a key that does not appear in that candidate's card — entries whose citations are not the candidate's own are discarded, not corrected.
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
