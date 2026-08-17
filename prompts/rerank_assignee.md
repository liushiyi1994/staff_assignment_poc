<!--
BENCHMARK-V2 EXPERIMENT — MEASURED AND NOT ADOPTED. Nothing loads this file unless
llm.rerank_prompt names it, and it does not. On the 30 validation cases, holding
everything else fixed, this prompt scored below prompts/rerank.md on all four metrics
(Hit@1 0.433 -> 0.367, Hit@5 0.767 -> 0.700, Hit@10 0.833 -> 0.800, MRR 0.550 -> 0.489).
Kept as the record of the experiment. See docs/benchmark-v2-config.md.
-->

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

Rank by **who the evidence shows is currently working in the specific area this brief is about** — the person whose recent, repeated work already covers this subsystem, component, or failure mode. That is a claim about their demonstrated ownership of the area, not a claim that they are the only or best-qualified person.

Rules:
- Rank ONLY the candidates given. Do not invent people. Include every candidate you can justify; omit none merely because it ranks low.
- Judge on, in this order of importance:
  1. **Specific overlap.** Does their evidence name the same subsystem, component, or class of problem as the brief? A candidate whose contributions describe this exact area outranks one whose profile merely lists a matching skill term.
  2. **Currency.** How recent is that specific evidence? Compare `last_used` dates and contribution periods. Evidence from the most recent periods outweighs older evidence of the same depth; a strong match that stopped years before outranks nothing but loses to a live one.
  3. **Repetition in that same narrow area.** Several contributions covering the same component is stronger than the same number spread across unrelated ones.
- Do NOT reward breadth for its own sake. A wide skill list, a high total evidence count, or general activity across the project is not evidence for this brief; a specialist with three recent contributions in exactly this area outranks a generalist with thirty across everything else.
- `reason`: one concrete sentence per person citing their actual evidence, and saying what area it covers and when ("14 tickets on Docker containerizer work across 2017-2018, including MESOS-1234"). A reviewer must be able to verify it against the profile shown.
- `evidence_ticket_keys`: 1-4 keys copied verbatim from that same candidate's own `evidence_tickets`, supporting exactly what the reason claims. Never cite a key belonging to another candidate, and never write a key that does not appear in that candidate's profile — entries whose citations are not the candidate's own are discarded, not corrected.
- `fit`: "strong" | "good" | "related". Use "strong" only for recent, specific, repeated evidence in this area; "related" for adjacent-but-not-direct matches.
- If a candidate is clearly unsuitable, include them at the bottom with fit "related" and an honest reason — never fabricate fit.

Return ONLY valid JSON:

```json
{
  "ranking": [
    {"person_id": "...", "fit": "strong", "reason": "...", "evidence_ticket_keys": ["PROJ-123", "PROJ-456"]}
  ]
}
```
