# Stage 2 full extraction — run record and verification

Run completed 2026-08-11 (started 2026-08-10); verified by the orchestrator.
Pilot lineage: `docs/stage2-pilot-review.md` (prompt revision `49228ee`,
iteration-2 acceptance).

## Run facts

| Item | Value |
|---|---|
| Provider / model | OpenRouter, `openai/gpt-5.6-luna` |
| Scope | All 2,668 Stage 1 buckets |
| Result | 2,666 extracted + 2 skipped = 2,668 accounted; 0 invalid, 0 failed; valid rate 1.000 (gate 0.90); exit 0 |
| Wall clock | 6h 33m (~8.8s/call) |
| Cost | **$1.8425** (5,159,001 in / 2,211,013 out tokens; 2,685 billed calls = 2,668 + 17 retries at 0.6%; zero estimate-fallback rows) vs $3.01 worst-case projection, $25.00 ceiling |
| Output | `data/contributions/raw.jsonl` (git-ignored) |

Per-project record counts exactly match the Stage 1 bucket distribution
(DM 998, EVG 125, FAB 403, MESOS 461, TIMOB 681); contribution IDs are unique
and complete.

## Verification

- **Evidence integrity:** zero records with an evidence key outside the source
  bucket (write-time enforced, independently re-swept). Zero ticket-key-shaped
  strings in any summary/reason absent from the bucket or its text (the two
  regex hits were `SHA-1`/`SHA-256` algorithm names).
- **Privacy:** zero emails, wiki-mentions, pseudonym strings, or URLs across
  all 2,668 records (two `@@`-prefixed regex hits were MySQL session
  variables).
- **Confidence:** 2,204 high / 462 medium / 2 low-by-construction (the skips).
  Nine records (0.34%) carry `high` with only 4 listed keys, all showing the
  same off-by-one pattern ("All 5 of the 5 tickets shown" on a 4-ticket
  bucket) — content accurate, label mechanically inflated.
- **Skip quality:** both skips are correct — one bucket of vague
  requirements/access tickets, one of three description-less "Networking
  Configuration" tickets.
- **Spot-read:** one random non-pilot record per project — concrete,
  grounded, appropriately hedged verbs, sensible specializations.

## Carried actions

1. **Confidence clamp (deterministic, no re-extraction):** when Stage 3/4
   consumes these records, clamp `confidence` to at most `medium` where
   `len(evidence_ticket_keys) < 5`, matching the extraction rubric. Affects 9
   records.
2. **Formal 5% graded sample** (implementation plan Task 3 acceptance): build
   `scripts/grade_sample.py` and grade ~133 contributions with a stronger
   model. Requires its own small authorization (model + budget). Task 3's
   other gates already pass: stage cost $1.84 < $15; skip rate 0.07% < 25%.
3. Provider selection is still global (`llm.provider`); resolve per-call
   provider routing before any query-engine (Part B) work.

## Status

Extraction corpus accepted for downstream research use pending the formal
graded sample. Next milestone: Stage 3 skill normalization (local embedding
clustering, no LLM calls) and the graded-sample check, each under its own work
order.
