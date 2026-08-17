# Work order: near-miss quality study — are top-1 misses plausible substitutes?

- Issued: 2026-08-17 by the orchestrator, on the owner's go-ahead
- Status: accepted 2026-08-17 by the orchestrator (see Acceptance record)
- Phase: research reporting support (base is current `main`)
- Suggested working branch: `agent/nearmiss-study`
- LLM authorization: **granted 2026-08-17 by the owner — ceiling $4 total**
  across stage names `nearmiss_rewrite` (validation-case brief rewrites only,
  cheap model `openai/gpt-5.6-luna`, ≈ $0.02) and `nearmiss_val` (ONE
  28-case validation run, v3-default config, ≈ $2). **No test-split runs or
  output reads of any kind** — building manifest *rows* for test packages
  offline is permitted (that is data construction, not exposure), but no
  test-case brief is rewritten and no test case is ever run or scored.
  Escalate above $4; record any in-session raise here at the time.

## Objective

Turn a report claim currently resting on interpretation into a measured
number: when the system's top pick is not in the labeled truth set, is that
person a *plausible substitute* (same capability neighborhood, same team's
work) or a genuine error? Deliverable: a defensible sentence with a metric
behind it for the research report.

## Context and constraints from the incident

The frozen v4 manifest and all per-case run records were lost on 2026-08-16
(`docs/incident-2026-08-16-data-loss.md`). Therefore:

1. Rebuild the v4 manifest **structure** offline (package selection, truth
   sets, splits are seed-deterministic from the restored parquet — $0, no
   LLM). Verify the validation split matches the published record: 28 cases,
   DM 15 / TIMOB 6 / MESOS 6 / FAB 1, mean truth-set 3.39
   (`docs/benchmark-v4-manifest.md` §6.2). Escalate on drift.
2. Pay rewrites for the **28 validation cases only** (`nearmiss_rewrite`).
   Label the manifest file a **sibling** of the frozen original (rewrites
   re-generated, not byte-identical) in its metadata and filename.
3. Run the full system once on those 28 cases (`nearmiss_val`) to obtain
   fresh per-case ranked lists. All study metrics are computed **within this
   run** — no cross-comparison to the lost frozen runs beyond noting the
   aggregate is consistent with recorded history within the measured floors.
4. Person capability profiles come from the production Neo4j graph,
   **read-only**. **No symlinks under any checkout's `data/`, ever** —
   scratch copies or absolute paths only (incident rule).
5. Coordination: `agent/incident-restoration` may be in flight; it owns the
   v1 manifest, test skip-guards, and contributions reconstruction. Do not
   touch those. Both orders write under `data/eval/` — use
   `data/eval/nearmiss/` exclusively.

## Pre-specified metrics (compute exactly these; add nothing post-hoc)

For every **top-1 miss** in the run (first-ranked person not in the case's
truth set), and — as the sanity reference — for every top-1 hit:

1. **Profile similarity to nearest truth person**, three fixed definitions:
   (a) Jaccard over specialization sets; (b) Jaccard over top-10 skills by
   recency-weighted evidence; (c) cosine between mean contribution-embedding
   vectors. **Control:** the same three numbers against a random eligible
   roster member for that case (median of 100 seeded draws).
2. **Adjacent-sprint truth membership**: is the first-ranked person in the
   truth set of the immediately previous or next sprint of the same project
   (from the offline manifest structure, all packages)? Post-as-of
   information — legitimate for post-hoc diagnostics, never for tuning;
   say so in the report.
3. Summary: per-miss table (n is small — show every case), the three
   similarity distributions for misses vs the random control with bootstrap
   CIs, the adjacent-sprint share, and the same summaries for hits as
   reference.

Honesty requirements: n(misses) will be roughly 12–18 — this is a
**descriptive study**, not hypothesis testing; present it that way. If the
data comes out *against* the plausible-substitute reading (misses no closer
to truth than random), that is the finding and the report sentence must
change accordingly — the study exists to find out, not to confirm.

## Deliverables

1. `docs/nearmiss-study.md`: method, the verification of the rebuilt
   validation split, per-miss table, distributions vs control, adjacent-
   sprint share, and a **one-paragraph report-ready statement** of what may
   now be claimed (whichever direction it falls).
2. Sibling-manifest and run checkpoints under `data/eval/nearmiss/`.
3. Report back: findings, spend by stage (≤ $4), test/ruff output (suite
   green if code touched), deviations. Escalate rather than improvise.

## Acceptance criteria

1. Ledger ≤ $4 across the two stages; zero test-case rewrites, runs, or
   reads; validation-split identity verified against the published record.
2. Metrics exactly as pre-specified; hits reported alongside misses; the
   control present for every similarity number.
3. Graph read-only; no symlinks under `data/`; restoration order's territory
   untouched.
4. The report-ready statement follows the data, not the desired conclusion.

## Acceptance record (2026-08-17, orchestrator)

Reviewed and merged. My own runs and checks: suite 639 passed / 2 skipped /
1 failed — the failure is `test_weights_round`'s data-dependent test, proven
pre-existing by the worker at the base commit (one of the incident's four,
pending the restoration order). Ledger exact: `nearmiss_rewrite` 29/$0.0100 +
`nearmiss_val` 82/$1.6878 = $1.6978 of $4; call types (28 intent + 54 rerank
+ 29 rewrites) consistent with one 28-case run; zero test-split stages. The
sibling manifest's verification reproduced **every** published v4 structural
number (1,061/150, all exclusion counts, both splits, survivorship, caps) —
which is also welcome post-incident evidence that the manifest build is
deterministic end to end.

- **Findings accepted, including their direction.** The strong claim ("a
  miss surfaces an equally capable person") is NOT supported. What is
  measured: misses sit closer to the truth set than a random roster member
  on all three pre-specified definitions (Δ +0.057 / +0.025 / +0.021, CIs
  exclude zero), but below the intra-team yardstick (0.246 vs 0.321 / 0.030
  vs 0.036 / 0.913 vs 0.946); 20 misses collapse to 10 people, with
  `DM:145735` first in 10 of 20; adjacent-sprint membership is null (1/20)
  with the calendar explanation recorded. The report-ready paragraph is
  generated by a rule fixed before the run and is the only sanctioned
  wording.
- **New insight recorded for the MVP notes:** a single profile is
  over-preferred by the ranker across DM cases — top-1 concentration is a
  failure mode no aggregate metric showed, surfaced only because the study
  printed every case.
- **Deviations ruled:** (1) the `CAPGRAPH_DATA_ROOT` seam touching two
  private globals — accepted for a study module, flagged for cleanup if ever
  promoted; (2) the three labelled non-pre-specified diagnostics are KEPT —
  they weaken the claim, which is the direction the pre-specification rule
  exists to protect; the qualifiers stay in the statement; (3) extra
  verification welcomed; (4) definition-(b) as-of-invariance documented and
  tested; (6) scratch copies, no symlinks — per the incident rule.
- **Checkpoint loss, and a new standing rule.** The worker's worktree was
  auto-cleaned when its session ended, taking the study's local data root —
  the raw run records — with it. The committed report carries the complete
  per-case table, so nothing evidentiary is lost, and re-creating the run is
  ~$1.70 if ever needed. Standing rule from this: **durable study
  checkpoints must be written to the primary checkout's data root by
  absolute path** — worktree-local data is ephemeral by definition, and the
  report must always be self-sufficient (this one was).
