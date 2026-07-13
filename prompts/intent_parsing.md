Parse a project staffing brief into structured search intent for a capability-matching system.

<brief>
{{brief}}
</brief>

<known_specializations>
{{specializations}}
</known_specializations>

Rules:
- Extract one entry per distinct role the brief needs. If the brief is a single body of work (e.g. an epic description), infer the 1-2 roles it implies.
- Map to the closest `known_specializations` where possible (use exact names); also keep free-text skills as written in the brief.
- `count`: number of people needed for that role (default 1).
- `domain`: the business/technical domain of the work in a few words.
- `recency_years`: only if the brief implies freshness requirements (default null).
- Do NOT invent requirements that are not in the brief.

Return ONLY valid JSON:

```json
{
  "roles": [
    {
      "role": "backend engineer",
      "specializations": ["Distributed systems backend"],
      "skills": ["Kafka", "stream processing"],
      "count": 2
    }
  ],
  "domain": "real-time data platform",
  "recency_years": null
}
```
