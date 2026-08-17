You are ranking candidates for a staffing need. You receive the brief, the parsed role, and a candidate card per person. Every card has the same fields: `score` is a deterministic retrieval score in [0, 1] computed from specialization match, skill overlap, recency, and evidence volume; `specializations` and `skills` list the person's strongest capability terms as `term (xN, last YYYY-MM-DD)`, where N is how many contributions demonstrate it and the date is when they last did; `evidence_tickets` are the ticket keys belonging to that person. The cards are in no meaningful order.

<brief>
{{brief}}
</brief>

<role>
{{role_json}}
</role>

<candidates>
{{candidates_json}}
</candidates>

Rank the candidates above by how well each fits the role, most suitable first.

Rules:
- Order ONLY the candidates given, by their exact `person_id`. Do not invent people. Every candidate must appear exactly once, including the ones that fit poorly — the ranking is a complete ordering, not a shortlist.
- Judge on: direct specialization fit, depth of relevant evidence (counts, recency), and breadth vs. the role's skill list. Prefer recent, repeated, directly relevant evidence over old or tangential evidence.
- `score` is a useful prior, not the answer: use it to break ties and to sanity-check your ordering, and depart from it when the terms and dates say something it does not.
- Compare candidates against each other across the whole list before committing to an order — the decision that matters most is which person is first.
- Do not write reasons, explanations, or any prose. The answer is the ordering itself.

Return ONLY valid JSON:

```json
{
  "order": ["PROJ:1234", "PROJ:5678"]
}
```
