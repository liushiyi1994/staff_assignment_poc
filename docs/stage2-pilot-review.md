# Stage 2 extraction pilot — run record and qualitative review

Reviewed 2026-08-10 by the orchestrator. Covers the first authorized LLM run of
this repository.

## Run facts

| Item | Value |
|---|---|
| Provider / model | OpenRouter, `openai/gpt-5.6-luna` |
| Manifest | `stage2-pilot-v1`, seed 20260810, SHA-256 `3938174d…d32f699` |
| Buckets | 30 (6 per project: DM, EVG, FAB, MESOS, TIMOB) |
| Result | 30 extracted, 0 skipped, 0 invalid, 0 failed; valid rate 1.000 (gate 0.90); exit 0 |
| Wall clock | ~3m46s (~7.6s/call) |
| Cost | **$0.0184** actual (44,978 in / 23,216 out tokens; all 30 usage rows provider-reported, zero estimate-fallback rows) vs $0.0324 projected worst case, $1.00 ceiling |
| Output | `data/contributions/pilot_raw.jsonl` (git-ignored) |

## Review criteria and findings

- **Grounding — pass.** Two buckets read in full against their outputs
  (`TIMOB:167543` 2012-Q4, `FAB:145069` 2018-Q3): every summary claim maps to a
  specific ticket; no invented systems or activities. The one ticket-key-shaped
  pair appearing in prose outside an evidence list (`LSE-68`/`LSE-75` in
  `DM:145785`) is verified present in the bucket's own ticket text (LSST
  document IDs, correctly used). A 30/30 automated sweep found no ticket key in
  any summary/reason that is absent from its source bucket.
- **Specificity — pass.** Summaries name concrete subsystems and activities
  (Qserv secondary-index loading, VSCC parallelization, reservation
  refinement, Lobster search behavior) rather than generic filler.
- **Sensitive data — pass.** Automated scan over all 30 records: zero emails,
  zero @/wiki mentions, zero pseudonym strings, zero URLs.
- **Evidence-key accuracy — pass** (machine-enforced ⊆ bucket at write time;
  re-verified). Reason-text ticket counts are exact in 28/30 records. Two
  records overcount by one (`FAB:145231` says seven, bucket has six;
  `TIMOB:167543` says five, bucket has four). Counts above the 8-key cap
  (e.g. "fourteen tickets", listing 8) are legitimate: the prompt caps
  `evidence_ticket_keys` at 3–8 while buckets hold up to 30.
- **Confidence calibration — 29/30.** Mediums (5) correctly cite thin evidence
  or missing resolution details; `TIMOB:166014` explicitly notes the
  leakage-safe records lack outcomes and hedges accordingly — desired behavior.
  The one defect: `TIMOB:167543`'s five-for-four miscount crosses the prompt's
  "high if ≥5 tickets" threshold, so its `high` label is unsupported (should be
  medium).
- **Skip quality — pass.** Zero skips is appropriate: the smallest pilot
  buckets (3–4 tickets) still carry substantive, capability-bearing text, and
  the model used `medium` confidence rather than skip for thin evidence.
- **Authorship calibration — acceptable.** Mostly "worked on / contributed
  to / investigated"; "implemented" appears where a ticket explicitly is an
  implementation task. Watch item for the full run, not a defect.

**Quality score: 29/30 (96.7%) fully accurate; the one miss is a
reason-arithmetic slip with a knock-on confidence label, not fabricated
content.** This clears the ≥90% acceptance bar.

## Prompt revision decision

One targeted revision is recommended before (or alongside) full-run
authorization — low-risk wording changes to `prompts/extraction.md`:

1. State that `evidence_ticket_keys` is a capped selection (3–8) from the
   bucket, and require the `reason` to count **accurately** — either the listed
   keys or an explicit "N of the bucket's M tickets" — with the confidence
   threshold applied to a count the model has actually enumerated.
2. Add one line: prefer activity-faithful verbs ("worked on", "investigated");
   use "implemented"/"led" only when ticket text explicitly supports it.

## Full-run projection (for the next authorization)

Scaling the earlier full-corpus worst-case dry run to OpenRouter pricing gives
roughly $3 worst case; scaling from observed pilot tokens gives a realistic
**≈ $1.70–2.00** for all 2,668 buckets — far under the $25.00
`llm.max_stage_cost_usd` ceiling, so the pilot-gate era ceiling concern is
moot at this pricing.

## Iteration 2 (revised prompt, 2026-08-10)

The prompt revision above was applied (`prompts/extraction.md`, commit
`49228ee`) and the same 30 buckets re-run with `--force`: 30 extracted, 0
skipped/invalid/failed, exit 0, ~5m, ~$0.019 (stage total $0.037 of the $1.00
pilot budget).

Re-review results:

- Zero high-confidence labels with fewer than 5 listed evidence keys — the
  iteration-1 calibration defect is fixed (`TIMOB:167543` is now `medium`).
- Reasons adopt the requested accurate-count phrasing ("All 4 of the 4 tickets
  shown…"); apparent overcounts on re-check were regex false positives
  (version strings "Android 4.3/API 18", document IDs "LSE-68/LCR-357").
- "Implemented" appears 3× — each backed by a ticket literally titled
  "Implement X" (MESOS-5083, TIMOB-10462, TIMOB-20441); "led" appears only as
  a substring of "ledger"/"handled". Verb calibration holds.
- Privacy scan again clean (0 emails/mentions/pseudonyms/URLs); no
  ticket-key-shaped string in any output absent from its bucket or bucket text.
- Sole residual: `TIMOB:167543`'s reason says "five" tickets where the bucket
  has four — a cosmetic prose miscount with correct confidence, correct keys,
  and accurate content.

**Iteration 2 score: 30/30 materially accurate.**

## Status

Pilot accepted after iteration 2. Owner authorized the full extraction run on
2026-08-10 (option "prompt iteration + pilot re-run, then full run"): stage
`stage2`, all 2,668 buckets, `llm.max_stage_cost_usd` $25.00 ceiling,
realistic projection ≈ $1.70–2.00.
