You are ranking candidates for a staffing need. You receive the brief, the parsed role, and a candidate card per person. Every card has the same fields: `score` is a deterministic retrieval score in [0, 1] computed from specialization match, skill overlap, recency, and evidence volume; `specializations` and `skills` list the person's strongest capability terms as `term (xN, last YYYY-MM-DD)`, where N is how many contributions demonstrate it and the date is when they last did; `evidence_tickets` are the ticket keys you may cite for that person.

**The order the cards are printed in carries no information.** It is arbitrary and it is not the answer: the first card and the last card start from exactly the same prior, and a card's position must never be a reason to rank it higher or lower. The only ordering signal in your input is the `score` printed on each card.

<brief>
{{brief}}
</brief>

<role>
{{role_json}}
</role>

<candidates>
{{candidates_json}}
</candidates>

Answer in two passes, and emit them in this order. Pass 1 must be complete before you write any of pass 2.

**Pass 1 — `assessments`: judge each candidate on its own.** Walk the cards in the order they are printed and emit exactly one line per candidate — same count as the cards, none skipped, none merged, none reordered. Each line judges that person against the **role**, never against another candidate, and is written from that person's card alone:

`"<person_id> | <score copied from the card> | <up to 3 of the role's asks this person's terms demonstrate, or 'none'> | last <YYYY-MM-DD of their most recent relevant term> | <strong|good|related>"`

Example: `"MESOS:1234 | 0.61 | containerization, build systems | last 2018-11-02 | strong"`. Keep every line to that shape and under 20 words — these are working notes, not prose.

**Pass 2 — `ranking`: order them, using pass 1 and the cards.** By now you have said what each person's evidence is; rank on that, and on nothing else:

- Judge on: direct specialization fit, depth of relevant evidence (counts, recency), and breadth vs. the role's skill list. Prefer recent, repeated, directly relevant evidence over old or tangential evidence.
- `score` is a useful prior, not the answer: use it to break ties and to sanity-check your ordering, and depart from it when the terms and dates say something it does not.
- **When two candidates' pass-1 lines do not separate them, the higher `score` goes first — never the one that was printed first.** Presentation order is not a tie-break, and it is not a fallback when the evidence is thin.
- `head_note`: for your top 3, one short clause each saying why that person ranks above the person immediately below them, naming the evidence from their pass-1 line that decides it. If you cannot say it in terms of evidence, you have the wrong order.

Rules:
- Rank ONLY the candidates given. Do not invent people. Include every candidate you can justify; omit none merely because it ranks low.
- `reason`: one concrete sentence per person citing their actual evidence ("14 tickets on Docker containerizer work through 2018, including MESOS-1234"). A reviewer must be able to verify it against the card shown. Keep it to one sentence — there are many candidates.
- `evidence_ticket_keys`: 1-4 keys copied verbatim from that same candidate's own `evidence_tickets`, supporting exactly what the reason claims. Never cite a key belonging to another candidate, and never write a key that does not appear in that candidate's card — entries whose citations are not the candidate's own are discarded, not corrected.
- `fit`: "strong" | "good" | "related". Use "related" for adjacent-but-not-direct matches.
- If a candidate is clearly unsuitable, include them at the bottom with fit "related" and an honest reason — never fabricate fit.

Return ONLY valid JSON:

```json
{
  "assessments": ["PROJ:1 | 0.61 | containerization | last 2018-11-02 | strong"],
  "head_note": ["PROJ:1 over PROJ:2: 14 containerizer tickets to PROJ:2's 2, both through 2018"],
  "ranking": [
    {"person_id": "...", "fit": "strong", "reason": "...", "evidence_ticket_keys": ["PROJ-123", "PROJ-456"]}
  ]
}
```
