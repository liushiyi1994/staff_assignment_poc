# Work order: Stage 7 benchmark run

- Issued: 2026-08-11 by the orchestrator
- Status: **accepted 2026-08-11** (commits `295ec37`, `29b7020`, branch `agent/stage7-benchmark`)

## Acceptance record (orchestrator, 2026-08-11)

Verified: 328 tests pass, ruff clean; stage7_eval ledger reconciles at $5.4922
across 320 calls; all 750 case-system records accounted with zero failures;
splits run in the required order with the configuration digest recorded; both
headline tables and the best-baseline-per-metric comparison read directly from
the tracked `docs/eval-results.md`. All four deviations accepted: the
intent-parse-retaining ablation (correctly labelled, cost split measured), the
roster/as-of threading through accepted Stage 6 code (required by this order's
correctness rules; unifies decay into one implementation), baselines reading
the Stage 1 evidence view (identical information budget — the fairest
comparison), and the warm-load latency convention.

Result summary (test split, frozen): capgraph_full 0.325 / 0.567 / 0.767
Hit@1/5/10, MRR 0.449 — beats plain-RAG vector search on every metric and BM25
on Hit@1, Hit@10, and MRR; narrowly behind BM25 on Hit@5 (−0.025). The
ablation shows the LLM re-rank carries the ranking gain (score-only trails
BM25), and candidate recall 0.925 bounds the graph system — 9/120 misses are
retrieval, not ranking. Honest caveats recorded in the report.
- Phase: research track — the final milestone: the numbers the experiment
  exists to produce
- Base branch: `agent/stage6-query` (worker branch + acceptance commit)
- Suggested working branch: `agent/stage7-benchmark`
- LLM authorization: harness build and tests are offline. The eval run is
  authorized on owner spawn: intent + re-rank for 150 manifest briefs under
  stage name `stage7_eval` (draws `llm.max_stage_cost_usd`, $25 ceiling;
  realistic ≈ $6). Baselines make no LLM calls.

## Objective

Implement and run the deterministic temporal benchmark
(`src/capgraph/eval/`): the frozen 150-brief manifest against the full system
and three baselines, producing Hit@1/5/10, MRR, candidate recall, latency,
and cost — overall, per project, and per split.

## Correctness requirements (leakage rules — non-negotiable)

1. **Roster restriction.** Every system and every baseline ranks only the
   case's recorded eligible roster — Cypher-parameterized for the structured
   arm, filtered for the vector arm, enforced in the harness with a test.
2. **Recency at the case's as-of time.** Recompute decay from each capability
   edge's `last_used` at the case's as-of via Stage 4's `decay()` — never the
   stored cutoff-frozen `decay_score` (query times are post-cutoff), never
   wall-clock. All evidence in the graph is already pre-cutoff, so exposure
   is safe by construction.
3. **Identical information budget for baselines**, all roster-restricted,
   deterministic, LLM-free:
   - **BM25** over the sanitized pre-cutoff ticket text (Stage 1 evidence
     view), aggregated to people;
   - **Pure vector** ("plain RAG"): embed the same ticket text locally, rank
     people by nearest tickets to the brief;
   - **Most-active**: pre-cutoff resolved-ticket count in the case's project.
4. **Split discipline.** Any tuning happens on the 30 validation cases only;
   the 120 test cases are run once, at the end, with the configuration frozen
   and recorded. Report both splits separately.

## Tasks

1. Harness in `run_eval.py`: iterate manifest cases deterministically
   (sorted, fixed seed), run all four systems per case, checkpoint results
   per case/system so an interrupted run resumes without re-spending.
2. **Ablation for free:** report the graph system twice — deterministic
   score only (no re-rank, no LLM) and full (with re-rank) — isolating what
   the LLM adds.
3. Metrics: Hit@1/5/10, MRR (first truth hit), candidate recall (truth
   present in the pre-rerank pool), mean/median latency per system, total
   LLM cost. Overall, per project, per split.
4. Tests (offline, mocked engine/LLM): metric math incl. edge cases,
   roster-restriction enforcement, as-of decay recompute, baseline
   determinism, checkpoint/resume. Suite green, ruff clean.
5. Run validation split first; sanity-review; then the frozen test run.
   Write `data/eval/results.{md,json}` and copy the final report to
   **`docs/eval-results.md` (tracked)** with the run configuration recorded.

## Acceptance criteria

- All 150 cases accounted for per system (no silent drops; failures listed
  with reasons); spend within ceiling; resume works (demonstrated or tested).
- `docs/eval-results.md` contains the full metric tables, config, and honest
  caveats (assignee-prediction target, not proof of optimal fit).
- Suite green offline, ruff clean.

## Report back

Branch/commits, the headline table (all systems × Hit@K/MRR/recall, both
splits), latency/cost, test output, deviations; escalate rather than
improvise.
