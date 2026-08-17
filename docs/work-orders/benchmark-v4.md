# Work order: benchmark v4 — work-package briefs, multi-person truth

- Issued: 2026-08-14 by the orchestrator, per the owner's direction decision of
  the same date (`docs/direction-decision.md`: v4 before MVP)
- Status: accepted 2026-08-15 by the orchestrator (see Acceptance record)
- Phase: research track, benchmark rebuild (backlog G12 — the gate for
  everything in `docs/improvement-backlog.md` Waves 2–3)
- Suggested working branch: `agent/benchmark-v4`
- LLM authorization: **granted 2026-08-14 by the owner — ceiling $15 total**
  across stage names `bench4_rewrite` (brief rewriting, cheap model
  `openai/gpt-5.6-luna` per the owner's instruction), `bench4_val`, and
  `bench4_test`. Expected ~$10–14 (rewrite ~$0.005/brief; the bulk is engine
  runs on the new splits). Project prospectively before each split; escalate
  before proceeding if any projection exceeds $15. Offline work spends nothing.
- Coordination: `agent/improvement-wave1` is open and also touches
  `stage0_load.py` (G1 truncation). The manifest/design/verification work here
  is independent and can start immediately; if both branches are in flight
  when you modify Stage 0, rebase on whichever merges first and escalate on
  any real conflict.

## Objective

Replace the retired single-ticket benchmark with one that measures the actual
product question — "who should work on this body of work?" — using real
work packages, real multi-person ground truth, and a leakage-guarded natural
brief, on a freshly cut, deterministic manifest. Then establish the new
baseline numbers for all systems and both frozen engine configurations.

## Why (from the backlog, verified)

Single-ticket briefs reward narrow term-matching (flattering BM25), collapse
Recall@K into Hit@K via single-truth labels, make headcount untestable, and —
measured from the v1 manifest — dropped 4,992 otherwise-usable cases because
the single recorded assignee wasn't roster-eligible. The scoring harness
already handles multi-truth (`eval/metrics.py` takes `truth: set[str]`;
`EvalBrief.true_person_ids` is a list), so the work concentrates in Stage 0
export and the manifest builder, not in scoring.

## Design requirements

1. **Grouping unit — verify, then choose.** First verify whether TAWOS carries
   real epic→child hierarchy (the observed `Issue_Link` types are semantic
   only; 3,032 Epic-type issues exist in the slice). If a defensible
   hierarchical link exists, epics are the preferred unit; otherwise use
   **sprints** (`Issue.Sprint_ID` → `Sprint`, confirmed present with
   `Start_Date`/`End_Date`/`Activated_Date`/`Complete_Date`). Report the
   verification evidence and the resulting package-count distribution before
   building the manifest. Version/`Issue_Link`-cluster grouping only as a
   recorded fallback.
2. **As-of discipline, unchanged in spirit from v1.** Each package gets one
   as-of time at its start boundary (e.g. sprint activation). Everything the
   benchmark exposes — brief text, roster, eligibility, evidence, recency —
   uses only information from strictly before the as-of time. Issues joining a
   package after as-of contribute to *truth* but never to the brief. Every
   v1 leakage guard (`docs/work-orders/stage7-benchmark.md`) applies.
3. **The brief: leakage-guarded cheap-model rewrite.** Input to the rewriter:
   creation-time titles/descriptions of the package's pre-as-of issues ONLY —
   no assignees, no comments, no resolution data, nothing post-as-of. Output
   must pass the existing `LeakageSanitizer` (identifiers, pseudonyms,
   mentions, emails stripped). Rewrites are checkpointed and **frozen into the
   manifest** so every later run is deterministic and free. Retain the
   un-rewritten package text as a parallel brief variant, and evaluate a
   held-out slice on both, so the rewrite's own effect on the numbers is
   measured, not assumed.
4. **Truth: everyone who worked the package.** Multi-person truth reconstructed
   per person at the same safe resolution boundaries as v1; every truth ID
   must be roster-eligible at as-of and have a retained Stage 1 profile
   bucket. Record per-case truth-set sizes. Cases whose truth set becomes
   empty under these rules are excluded with a recorded reason — do not relax
   the rules to save cases.
5. **Manifest discipline.** Deterministic, versioned (`tawos-v1.1-benchmark-v4`),
   fixed seed, project-stratified, exclusion reasons recorded, split sizes
   chosen from what the data supports (target the same order as v1: ~30
   validation / ~120 test; report the achievable counts and escalate if the
   test split would fall below ~80). **Exposure budget stated in the manifest
   doc:** tuning on validation only; the test split is run once per engine
   version, same as before.
6. **What gets measured on the new benchmark.** All five systems
   (`capgraph_full`, `capgraph_score`, `bm25`, `vector_only`, `most_active`).
   For the two graph systems, run **both frozen configurations** (v2-frozen
   and v3-frozen/default) if the prospective projection keeps total spend ≤
   $15 — the v2-vs-v3 Hit@1 disagreement on broader briefs is a key open
   question; if it does not fit, run v3-default only and escalate for the
   second. Report Hit@K, **Recall@K (now meaningfully distinct)**, MRR,
   candidate recall, latency, cost, per-project tables, paired statistics
   between the two configs, and the rewrite-effect measurement (req. 3).
7. **The old suite stays archival.** No v1-manifest re-runs; v1–v3 numbers
   remain quotable next to v4's with the difference in instrument stated.

## Success tests (from the backlog, unchanged)

1. Recall@K diverges from Hit@K — multi-truth is real, not cosmetic.
2. The graph-vs-BM25 gap is re-measured on staffing-shaped briefs. If broader
   briefs do not change that comparison, report it plainly — that negative
   result is important.

## Deliverables

1. Stage 0 export of the chosen grouping key (+ effort fields if free to
   carry); manifest builder for package cases; brief-rewrite stage through
   `src/capgraph/llm.py` with checkpointing; all as tested, config-driven
   features (suite green, ruff clean, toy-data unit tests).
2. `docs/benchmark-v4-manifest.md`: grouping verification evidence, design
   choices, exclusion table, split sizes, truth-set-size distribution,
   exposure budget, rewrite guards.
3. `docs/eval-results.md` gains a clearly-separated **Benchmark v4** part
   (new instrument — do not present v4 numbers as comparable rows to v1–v3):
   baseline tables for all systems and both configs, paired stats, the
   rewritten-vs-raw brief comparison, spend.
4. Report back: design decisions taken vs escalated, headline tables, spend
   reconciled against `data/llm_costs.jsonl` by stage name, test/ruff output,
   deviations. Escalate rather than improvise — especially on grouping-unit
   surprises and split-size shortfalls.

## Acceptance criteria

1. Leakage guards demonstrably enforced (tests cover: no post-as-of text in
   briefs, sanitizer applied, truth-eligibility rules, deterministic rebuild
   from the frozen manifest).
2. Ledger ≤ $15 across the three `bench4_*` stages, reconciled.
3. Manifest deterministic: rebuilding produces byte-identical case sets and
   briefs without new LLM calls.
4. Baselines' v4 numbers reproducible offline from checkpoints; suite green;
   ruff clean.

## Acceptance record (2026-08-15, orchestrator)

Reviewed independently from a clean worktree of `agent/benchmark-v4` (the
primary checkout belonged to the in-flight wave-1 worker; nothing there was
disturbed). Merged with this record.

- **Suite and lint, my own run:** 457 passed (26 new v4 tests), ruff clean.
- **Recompute:** I recomputed the test-split table from
  `data/eval/v4/runs/v3frozen/rewritten/test.jsonl` and the v4 manifest with
  my own arithmetic — every value matches the published tables exactly for
  both `capgraph_full` and `bm25` (Hit@1/5/10, Recall@5/10, MRR, and
  candidate recall 0.974 under the truth-fraction definition).
- **Ledger:** `bench4_rewrite` 164 calls $0.0614 (all `gpt-5.6-luna`, per the
  owner's cheap-model instruction), `bench4_val` 302 calls $6.8280,
  `bench4_test` 357 calls $7.3159 — total **$14.2053 of the $15 ceiling**,
  reconciled by stage name. The test window (08-15 10:20–12:00) begins after
  the manifest was recorded (08-14 21:47) and holds exactly one exposure:
  `v2frozen`'s test file contains only the three deterministic baseline rows,
  no graph rows — consistent with the escalation.
- **Immutability:** zero deletions in the v1–v3 halves of
  `docs/eval-results.md`; the v4 section sits under its own marker and opens
  by stating it is a different instrument, not a comparable fourth round.
- **Escalation handled correctly:** the second test run (`v2frozen`,
  projected +$7.56 → $22.02 > $15) was not made. **Orchestrator decision:
  declined for now** — on the new instrument's validation split the two
  frozen configs are within noise (v3 no longer trails on Hit@1), the run
  would spend a test-split exposure on a within-noise comparison, and the
  ceiling doesn't cover it. It can be a separately approved one-run order if
  the owner wants it.
- **Deviations accepted as disclosed:** worktree isolation with direct
  coordination with the wave-1 session (graph verified unchanged at both ends,
  316/2,666/10,630 node counts); headcount left untestable by design (the
  rewriter must not state a team size — the only source would be the truth
  set); splits 28/122 against the ~30/~120 target; mid-study projection
  re-parameterization from measured per-case costs; shared-module extensions
  including the `run_v3` section-writer fix; the wave-1 G1 interaction guard
  (stale rewrites refused by input digest, never silently reused).
- **Quality worth naming:** the grouping verification *corrected the backlog*
  (epic membership does exist, in the dated change log) and still chose
  sprints on the order's own boundary requirement; survivorship is now
  measured per case (502 ineligible resolvers narrow truth sets) instead of
  silently deleting cases; the case-correlation caveat (mean truth-set
  Jaccard 0.34 between consecutive sprints) is disclosed and is why all
  comparisons are paired; the raw-brief control ran on validation only,
  preserving the single test exposure.

Outcome accepted as reported: **the first statistically significant
separation in this project.** On staffing-shaped briefs with multi-person
truth, `capgraph_full` leads every baseline on all six metrics; against BM25,
Hit@1 +0.205 (31 wins / 6 losses, McNemar p = 0.000), Hit@5 +0.123
(p = 0.001), Hit@10 +0.082 (p = 0.031), with bootstrap CIs excluding zero on
Recall@5, Recall@10, and MRR. The backlog's G12 hypothesis is confirmed: the
single-ticket instrument, not the system, was hiding the difference. The
rewrite made the benchmark more realistic *and* cheaper to run, and its
effect is itself measured (validation MRR +0.089, CI [+0.005, +0.186],
with `most_active` flat as the control).
