You are analyzing a software engineer's Jira ticket history to extract an evidence-backed record of what they actually did and what capabilities that work demonstrates.

<person>{{person_name}}</person>
<project>{{project_name}} — {{project_domain}}</project>
<period>{{period}}</period>

<tickets>
{{tickets}}
</tickets>

Each ticket shows only the leakage-safe evidence view: stable key, creation-time
summary/description (description truncated). Mutable final type, resolution,
assignee, and unversioned component-name fields are deliberately absent.

Produce ONE contribution record for this person on this project in this period.

Rules:
- Ground every claim in the tickets. Never infer capabilities that the ticket text does not support.
- Describe activity faithfully: prefer verbs the tickets support ("worked on", "investigated", "fixed"); use "implemented" or "led" only when ticket text explicitly says so.
- `contribution_summary`: 2-4 sentences, specific and concrete — what they built/fixed/led, in which subsystem. Written for a staffing reviewer, not a performance review. No superlatives.
- `specializations`: 1-3 coarse capability areas (e.g. "Distributed systems backend", "Frontend web development", "Data pipeline engineering", "DevOps / build infrastructure"). `strength` is "primary" if most tickets support it, else "secondary".
- `skills`: 3-10 fine-grained, evidence-supported skills — technologies, subsystems, techniques (e.g. "Kafka", "memory leak debugging", "CI pipeline configuration"). Use the terms the tickets use.
- `confidence`: "high" if ≥5 of the keys you list in `evidence_ticket_keys` clearly support the summary; "medium" if signal is thinner or tickets are vague; "low" if you are mostly guessing (prefer returning fewer skills over guessing).
- `reason`: one sentence explaining the confidence, citing concrete text signals and accurate counts — count the keys you actually list; if you reference further supporting tickets beyond that list, say "N of the M tickets shown".
- `evidence_ticket_keys`: the 3-8 ticket keys that best support the summary (a capped selection; the ticket list above may contain more).
- If tickets are trivial or too vague to support any capability claim, return `"skip": true` with a one-line reason.

Return ONLY valid JSON matching this schema:

```json
{
  "skip": false,
  "contribution_summary": "...",
  "specializations": [{"name": "...", "strength": "primary|secondary"}],
  "skills": [{"name": "..."}],
  "confidence": "high|medium|low",
  "reason": "...",
  "evidence_ticket_keys": ["KEY-1", "KEY-2"]
}
```
