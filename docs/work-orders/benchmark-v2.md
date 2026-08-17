# Work order: benchmark v2 — improvement experiments

- Issued: 2026-08-11 by the orchestrator
- Status: accepted 2026-08-12 by the orchestrator (see Acceptance record)
- Phase: research track extension (post-wrap; base is `main` @ merge `7808c55`)
- Suggested working branch: `agent/benchmark-v2`
- LLM authorization: validation-split A/B calls and ONE frozen test-split
  re-run, under stage names `stage7b_val` / `stage7b_test`. Expected total
  < $8; escalate before proceeding if any projection exceeds $10. Offline
  analyses spend nothing.

## Objective

Produce an honest, better benchmark number: tune on the 30 validation cases
only, freeze one improved configuration, run the 120-case test split exactly
once more, and report v1 and v2 side by side with every change disclosed.

## Protocol discipline (non-negotiable)

- All tuning and A/B on the validation split. The test split is run once,
  after the v2 config is frozen and recorded (components + rationale).
- Pick the v2 config by mechanism, not by leaderboard: on 30 cases, gains
  under ~5 points are noise. A lever is adopted when its effect is consistent
  and explainable (e.g. "recall now 1.0 by construction", "fusion helps on
  every metric"), not because one configuration topped the sweep.
- v1 results and checkpoints are immutable — new runs under a separate
  checkpoint namespace. Leakage rules from
  `docs/work-orders/stage7-benchmark.md` apply unchanged (roster restriction,
  as-of recency, no wall-clock).

## Levers, in order — free first

1. **Offline, from existing v1 checkpoints ($0):**
   a. Reciprocal-rank fusion of `capgraph_full` × `bm25` rankings (standard
      RRF, k=60 default; sweep k on validation). Also try fusing
      `capgraph_score` × `bm25` as the LLM-free variant.
   b. Roster backstop: append un-retrieved roster members below retrieved
      candidates, ordered by deterministic score — candidate recall becomes
      1.0 by construction. Simulate its Hit@K effect from checkpoints where
      possible.
   c. Deterministic score-weight sweep (intent parses are checkpointed, so
      re-scoring is free): coarse grid over the four weights.
2. **Paid, validation only:** assignee-aligned re-rank prompt revision — the
   target is "who most plausibly takes this ticket", so weight fresh,
   subsystem-specific evidence most heavily; flag all prompt changes.
   Optionally widen re-rank window to 20 if fusion/backstop shifts who
   reaches it.
3. **Only if step 2 underdelivers:** stronger re-rank model
   (`openai/gpt-5.6-sol`, $5/$30 per MTok — add pricing + provider-map
   entries, cite the models API) on validation for one A/B.

## Deliverables

1. Implementation of adopted levers as real engine/eval features (tested,
   offline tests, suite green, ruff clean) — not eval-script hacks.
2. Frozen v2 config record; single test-split run under `stage7b_test`.
3. `docs/eval-results.md` gains a **v2 section**: side-by-side v1/v2 headline
   tables, per-lever validation findings (including what did NOT help), the
   frozen config, spend. The v1 tables stay untouched.
4. Report back: v1 vs v2 headline table, per-lever findings, spend, test/ruff
   output, deviations; escalate rather than improvise.

## Acceptance record (2026-08-12, orchestrator)

Reviewed independently on `agent/benchmark-v2` (merged as this record was written):
`uv run python -m pytest -q` → 376 passed; `uv run ruff check .` clean.

- **Test split touched exactly once.** 255 `stage7b_test` ledger entries — 120
  case-pairs plus 15 retries — in a single 45-minute window whose first call lands
  11 seconds after freeze commit `4bbe0b8`. All 153 `stage7b_val` entries precede
  the freeze. Ledger timestamps, not report claims.
- **v1 immutable.** `data/eval/results.json` still reproduces the frozen v1 numbers
  (test `capgraph_full` 0.325/0.567/0.767, MRR 0.449, recall 0.925). The v1 run
  files' mtimes match v1's own `stage7_eval` run window (Aug 11 15:23–16:18 PDT,
  six hours before the worker's first commit), so v1 artifacts were last written by
  v1 itself. v2 work is confined to `data/eval/v2/` namespaces. Zero deletions in
  the v1 half of `docs/eval-results.md`. The shared BM25 docs cache
  (`data/eval/cache/evidence_person_docs.json`) was regenerated during the v2 run —
  derived data; baselines reproduce v1 rankings byte-for-byte, so benign.
- **Spend $6.58** ($2.23 val + $4.35 test) against the $10 ceiling, reconciled by
  stage name against `data/llm_costs.jsonl`.
- **Config chosen by mechanism.** Marginal effects monotone across a 216-point
  grid, adopted vector inside an 81-vector plateau that beats v1 everywhere, and
  the offline sweep predicted the paid outcome before it was paid for. Rejections
  (fusion, backstop, prompt revision, window widening) each carry a mechanism. The
  stronger re-rank model was correctly escalated rather than run when its
  projection crossed the $10 line.
- **Deviation, accepted as disclosed:** a pre-decision diagnostic computed BM25
  truth ranks for the nine v1 *test* retrieval misses. Disclosed in
  `docs/benchmark-v2-config.md`; no v2 decision used it. Consequence recorded
  here: the lexical-retrieval-arm recommendation is partially test-informed and
  must be re-established on the validation split in any follow-on order.

Outcome accepted as reported: the full system is unchanged within the measured
0.100 noise floor; the deterministic arm improved materially (test Hit@5 +0.117,
MRR +0.047) and now matches the full system on Hit@5/Hit@10 at ~10x lower cost.
The finding of record: the LLM re-rank, not the deterministic score, bounds the
full system.
