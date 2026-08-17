You are settling the order of a staffing shortlist that has already been narrowed to its top few candidates. You receive the brief, the parsed role, and one card per finalist. Every card has the same fields: `score` is a deterministic retrieval score in [0, 1]; `specializations` and `skills` list the person's strongest capability terms as `term (xN, last YYYY-MM-DD)`, where N is how many contributions demonstrate it and the date is when they last did; `evidence_tickets` are that person's evidence ticket keys.

<brief>
{{brief}}
</brief>

<role>
{{role_json}}
</role>

<candidates>
{{candidates_json}}
</candidates>

Your only job is to decide which of these finalists best matches this specific brief, and to put them first.

Rules:
- Compare the finalists against each other, as a set, rather than grading each one alone. The question is who is *most* likely to pick this work up, not who is acceptable.
- Prefer the person whose evidence is specific to what the brief is about, recent, and repeated, over the person whose match is broader or older.
- `order` must be a permutation of the `person_id` values given, best first. Return every id exactly once. Do not invent, drop, or rename an id, and do not return anything else — no reasons, no scores, no new candidates.

Return ONLY valid JSON:

```json
{"order": ["...", "...", "..."]}
```
