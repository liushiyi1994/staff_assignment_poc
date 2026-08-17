# Work order: manager-pitch v4 addendum

- Issued: 2026-08-16 by the orchestrator
- Status: accepted 2026-08-16 by the orchestrator (see Acceptance record)
- Phase: research-track wrap-up (base is `main` after the weights-round merge)
- Suggested working branch: `agent/pitch-v4-addendum`
- LLM authorization: **none.** $0 order — documentation only. No benchmark
  runs, no test-split access, no queries.

## Objective

Bring `docs/manager-pitch.md` up to date with everything since 2026-08-14,
as a **dated addendum section** appended to the document — the original body
stays as the research-track-at-2026-08-14 record, edited only where a claim
would now be false without a pointer to the addendum.

## What the addendum must cover (same honesty rules as the original)

1. **The new instrument, first.** Benchmark v4 measures a different, more
   product-shaped question (a sprint's body of work, everyone who worked it
   as truth) — one short plain-language paragraph, before any number, so no
   reader mistakes v4 rows for v1–v3 rows.
2. **The new headline.** On v4's 122-case test split the full system leads
   every baseline on all six metrics — the project's first statistically
   significant separation (vs BM25: Hit@1 0.508 vs 0.303, p = 0.000; MRR
   0.622 vs 0.459). Transcribe from `docs/eval-results.md`; keep the
   best-baseline-per-column table.
3. **The self-correction arc, told plainly — it is the pitch's trust story.**
   A cheap probe accused the LLM ranking stage of following presentation
   order; a better-instrumented study (identical candidate pools) cleared
   it and traced the false signal to retrieval randomness; the accusation
   and the correction are both in the record. The re-rank earns +0.250
   top-1 over the cheap arm on identical pools.
4. **The discipline dividend.** The instrument now has measured per-metric
   noise floors; four further improvement ideas were closed at a tenth of
   their authorized budget because free tiers gated the paid ones; the
   final weights round returned its entire $10 unspent after proving its
   improvement could not reach the output. "We spend to learn, and we stop
   when the arithmetic says stop" — with the numbers.
5. **Updated totals and status.** Research track concluded 2026-08-16
   (`docs/direction-decision.md`); total experimental spend ≈ $49.86 across
   all stages, reconciled from `data/llm_costs.jsonl` (state the exact sum
   you compute); configuration of record unchanged since v4; one test
   exposure held in reserve.
6. **What this changes for the MVP conversation** — three lines: the system
   now demonstrably beats free search on the product-shaped question; the
   cheap arm remains the cost story and has a recorded scoring lead for the
   pilot to measure; the evaluation methodology itself is the most
   transferable asset.
7. **Source map extended** for every new number; the ethics section is
   restated as unchanged and still non-negotiable, in one line.

## Constraints

- Every number transcribed from a repository artifact — no recomputation
  except the ledger sum, whose method is stated; no flattering rounding.
- v1–v3 pitch sections and the v1–v3 halves of `docs/eval-results.md` remain
  byte-identical; the original pitch body may gain at most brief pointers to
  the addendum where a 2026-08-14 claim is now superseded.
- Plain language throughout: every term of art explained on first use; the
  correction arc must be understandable by a reader with zero context.
- Suite untouched or green; no code changes expected.

## Deliverables

1. The addendum in `docs/manager-pitch.md`, plus updated source map.
2. Report back: what was included/omitted and why, the ledger sum and its
   method, verification that the original body is unedited except recorded
   pointers, and any deviations. Escalate rather than improvise.

## Acceptance criteria

1. Facts reconcile against artifacts (spot-checked by the orchestrator).
2. v4 presented unmistakably as a different instrument; no cross-instrument
   comparison presented as apples-to-apples.
3. The correction arc present, plainly told, with both the accusation and
   the exoneration.
4. Zero ledger delta; original pitch body byte-identical except recorded
   pointer edits.

## Acceptance record (2026-08-16, orchestrator)

Reviewed from the branch artifacts (the deliverable is self-verifying via its
source map). Merged with this record.

- **Original body byte-identical plus inserted pointers only:** the diff is
  408 pure insertions, zero deletions — stricter than the order's allowance.
- **Spot-checks reconcile:** the v4 headline table matches
  `docs/eval-results.md` (0.508/0.303, MRR 0.622/0.459, paired rows exact);
  the cheap-arm 0.311 top-1 I verified from the raw v4 test records; the
  closing total $49.8621 over 5,402 calls matches my own ledger sum at the
  conclusion point, correctly scoped in the text ("through 2026-08-14" for
  the body figure, method stated for the calculated total).
- **The order's content points are all present:** instrument-difference
  warning before any number; the correction arc told as accusation and
  withdrawal; the discipline dividend with the gate arithmetic; hardness
  caveats carried into §A2 (second commit); ethics restated; source map
  extended.
- **Orchestrator addition at acceptance:** the re-rank probes round closed
  the same afternoon, after this addendum was written; a dated postscript
  was appended to `docs/manager-pitch.md` at merge (probes outcome, updated
  ledger $58.8779 / 5,521 calls, the Part-A hypothesis) so the document's
  copied-from-artifacts claim stays literally true.
