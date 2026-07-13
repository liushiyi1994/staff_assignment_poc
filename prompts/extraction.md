You are analyzing a software engineer's Jira ticket history to extract an evidence-backed record of what they actually did and what capabilities that work demonstrates.

<person>{{person_name}}</person>
<project>{{project_name}} — {{project_domain}}</project>
<period>{{period}}</period>

<tickets>
{{tickets}}
</tickets>

Each ticket shows: key, type, summary, description (truncated), components, labels, resolution.

Produce ONE contribution record for this person on this project in this period.

Rules:
- Ground every claim in the tickets. Never infer capabilities that the ticket text does not support.
- `contribution_summary`: 2-4 sentences, specific and concrete — what they built/fixed/led, in which subsystem. Written for a staffing reviewer, not a performance review. No superlatives.
- `specializations`: 1-3 coarse capability areas (e.g. "Distributed systems backend", "Frontend web development", "Data pipeline engineering", "DevOps / build infrastructure"). `strength` is "primary" if most tickets support it, else "secondary".
- `skills`: 3-10 fine-grained, evidence-supported skills — technologies, subsystems, techniques (e.g. "Kafka", "memory leak debugging", "CI pipeline configuration"). Use the terms the tickets use.
- `confidence`: "high" if ≥5 resolved tickets clearly support the summary; "medium" if signal is thinner or tickets are vague; "low" if you are mostly guessing (prefer returning fewer skills over guessing).
- `reason`: one sentence explaining the confidence, citing ticket counts/types.
- `evidence_ticket_keys`: the 3-8 ticket keys that best support the summary.
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
