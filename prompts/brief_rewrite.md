Rewrite a batch of planned engineering work into the staffing brief a delivery manager would write when asking "who should work on this?".

The work below is everything that was planned into one upcoming iteration of a {{domain}} project, taken from the items' original titles and descriptions. It is a body of work, not a single task.

<planned_work>
{{items}}
</planned_work>

Write the brief the manager would send to staff this iteration.

Rules:
- Describe **the work and the capability it needs**: the systems and components involved, the technical areas, and the kinds of experience someone would need to do it well.
- Write 100-180 words of plain prose. No bullet lists, no headings, no JSON inside the brief.
- Generalize. Say "container scheduling and resource isolation", not a restatement of each item in turn. The brief should read as one request, not as a list.
- Use ONLY what is in the work above. Do not invent systems, deadlines, clients, or requirements that are not there.
- Do **not** name or refer to any person, account, handle, or email address, even if one appears above.
- Do **not** include issue keys, ticket ids, version numbers, URLs, dates, or sprint names.
- Do **not** state how many people are needed, or name a team size. That is what the reader has to decide.
- Do not speculate about who did, will do, or should do the work.

Return ONLY valid JSON:

```json
{
  "brief": "We have an upcoming block of work on ..."
}
```
