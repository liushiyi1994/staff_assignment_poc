# Work order: benchmark v3 — recall arm, wider window, re-rank hardening

- Issued: 2026-08-12 by the orchestrator
- Status: accepted 2026-08-12 by the orchestrator (see Acceptance record)
- Phase: research track extension (base is `main` @ `68a4869`, benchmark v2
  accepted)
- Working branch: `agent/benchmark-v3`
- LLM authorization: **granted 2026-08-12 by the owner — ceiling $25 total**
  across `stage7c_val` (validation A/B) and `stage7c_test` (ONE test run with
  the frozen config, including the self-consistency samples if adopted).
  Expected ~$11–22. Escalate before proceeding if any projection exceeds $25.
  Offline analyses spend nothing.

## Objective

Attack the two loss mechanisms benchmark v2 isolated — the candidate-recall
ceiling and the re-rank stage that bounds the full system — with
literature-backed levers; tune on validation only; run the test split once.

## Context (what v2 established)

The v2 test run decomposes the 120 cases into three loss buckets: 9 truths never
retrieved into the candidate pool (recall 0.925), 12 in the pool but below
deterministic rank 15 so the re-rank never sees them, and 28 that reached the
re-rank but ended below top-5 (13 of them demoted out of a top-5 the
deterministic score already had). The re-rank is strongly net-positive on Hit@1
(+22/−6) and neutral-to-negative on Hit@5. Feeding it a better ordering did not
change its output — the literature signature of listwise position bias
(Tang et al., NAACL 2024, arXiv:2310.07712; ECIR 2026 positional-bias study).

**Disclosure:** the bucket counts above are post-hoc aggregate diagnostics of the
accepted v2 test run, used here to *prioritize* levers. Adoption decisions in
this order must rest on the validation split and construction-level mechanisms
only, per the same discipline as v2.

## Protocol discipline (unchanged from v2, plus one addition)

- All tuning and A/B on the 30 validation cases. Test split run once, after the
  v3 config is frozen and recorded in `docs/benchmark-v3-config.md`.
- v1 and v2 results and checkpoints immutable; all new work under
  `data/eval/v3/`. Leakage rules from `docs/work-orders/stage7-benchmark.md`
  apply unchanged.
- On 30 cases the measured noise floor is ±0.10: a lever is adopted on a
  consistent, explainable mechanism, never on a leaderboard row.
- **Test-split wear:** this is the third exposure of the 120-case test split.
  Treat it as the last on this manifest — any v4 needs a freshly cut manifest.
- Report statistics as paired per-query comparisons (win/loss counts, McNemar or
  bootstrap) alongside aggregates — expected effect sizes are smaller than the
  aggregate noise band.

## Levers, in order — free first

1. **BM25 arm in the candidate-generation union ($0, offline).** Union becomes
   vector top-40 ∪ structured top-40 ∪ BM25 top-10 over evidence text.
   Mechanism, established on validation from existing checkpoints: recall rises
   0.967 → 1.000 with BM25 top-10, median one extra candidate per pool. Union,
   NOT rank fusion — v2 measured vanilla RRF dragging the strong ranking down,
   consistent with Bruch et al. (TOIS 2023, arXiv:2210.11934). Implement as an
   engine feature (config-driven arm width), reusing the existing BM25 eval
   code, with as-of-time discipline identical to the eval baseline.
2. **Compact candidate cards for the re-rank prompt ($0).** Replace long
   evidence dumps with a fixed structure per candidate: pseudonym, top skills
   with recency, deterministic score, 2–3 evidence ticket keys. Mechanism:
   shorter, uniform contexts reduce lost-in-the-middle failures and make lever 3
   affordable; putting the deterministic score in the prompt gives the ranker
   the signal it currently ignores. Evidence directional (SumRank 2026,
   arXiv:2603.24204); cheap A/B. Citation validity rules stay exactly as in
   `prompts/rerank.md` — the evidence validator must keep working unchanged.
3. **Re-rank window 15 → 32 (cards make this ~flat-cost).** Mechanism, by
   construction: with window ≥ the deterministic rank of every pool-resident
   truth, window recall equals candidate recall; 32 covers the pool median (33)
   without entering the deep-window degradation regime documented in "Drowning
   in Documents" (SIGIR 2025 ReNeuIR, arXiv:2411.11767). Gate on a validation
   no-regression A/B.
4. **Paid, validation A/B: permutation self-consistency on the re-rank.** Three
   samples of the same listwise call with shuffled candidate order, aggregated
   by Borda (Kemeny if cheap to implement). Mechanism: peer-reviewed 7–18%
   relative ranking gains and variance reduction (Tang et al., NAACL 2024,
   arXiv:2310.07712); directly targets the 13-demotion bucket and our ±0.10
   noise. Latency: parallelize the samples.
5. **Paid, validation A/B: strong-model finisher for Hit@1.** Current model
   ranks the window; one `openai/gpt-5.6-sol` setwise call orders the top-5
   cards only (tiny prompt — pennies per case, unlike the full-window sol arm
   escalated in v2). Mechanism: model-strength is the most reliable Hit@1 lever
   in the literature (RankGPT GPT-4 gap, arXiv:2304.09542; Setwise, SIGIR 2024,
   arXiv:2310.09497; EcoRank cascade, ACL Findings 2024, arXiv:2402.10866).
   Requires provider-map + pricing entries citing the models API, and a
   per-call-ceiling check before any call is sent.
6. **$0 reporting addition (not a lever): label-noise audit.** For every test
   miss in the frozen v3 run, use manifest audit fields to record whether the
   recorded truth was later reassigned/resolved by someone else. Report a
   secondary "truth ∪ resolver" metric alongside — never replacing — the
   headline. Motivation: ~18% assignee≠fixer and +18% MRR from label cleaning
   in the literature (IssueCourier, arXiv:2505.11205; Tüzün et al., IST 2022).

## Recorded don'ts (evidence-backed, do not spend here)

HyDE/doc2query query expansion (hurts non-weak retrievers — EACL Findings 2024,
arXiv:2309.08541); same-dimension embedding swap (~0.3 MTEB point for a forced
reindex); SPLADE/ColBERT infrastructure at 1–2k documents; long-CoT reasoning
re-rankers (arXiv:2505.16886); vanilla RRF of strong×weak lists (re-confirmed
in v2); LLM fine-tuning for triage (underperforms structured baselines,
arXiv:2508.21156); a decayed-activity fourth retrieval arm (BM25 already closes
validation recall — revisit only if v3 validation shows residual misses).

## Spend estimate (for owner approval)

| Item | Est. |
|---|---:|
| Validation arms (cards+window, +SC×3, +sol finisher) | ~$5–7 |
| Test run, single pass config (if SC not adopted) | ~$5–6 |
| Test run with SC×3 (if adopted) | ~$13–16 |
| **Total expected** | **~$11–22** |

Proposed guardrails: prospective per-split projection against the approved
ceiling before starting (as in v2, `eval.v3.max_total_cost_usd`); escalate
before proceeding if any projection exceeds the ceiling; `stage7c_val` /
`stage7c_test` stage names; every call through `src/capgraph/llm.py`.

## Deliverables

1. Adopted levers as tested engine/eval features (offline tests, suite green,
   ruff clean); config in `config/settings.yaml`; prompts in `prompts/`.
2. `docs/benchmark-v3-config.md` frozen before the test run: adopted config,
   per-lever validation findings incl. rejections, rationale by mechanism.
3. `docs/eval-results.md` gains a v3 section: v1/v2/v3 side-by-side, paired
   per-query statistics, label-noise audit table, spend. v1/v2 sections stay
   untouched.
4. Report back: headline table, per-lever findings, spend reconciled against
   `data/llm_costs.jsonl`, test/ruff output, deviations; escalate rather than
   improvise.

## Acceptance record (2026-08-12, orchestrator)

Reviewed independently on `agent/benchmark-v3` (merged as this record was
written): `uv run python -m pytest -q` → 430 passed; `uv run ruff check .`
clean; headline metrics recomputed from `data/eval/v3/runs/test.jsonl` raw
records and matching the published tables on every value for both graph
systems.

- **Test split touched exactly once.** 256 `stage7c_test` ledger calls (120
  intent + 136 re-rank incl. retries) in a single window beginning 40 seconds
  after freeze commit `8abb0db`; all 447 `stage7c_val` calls precede the
  freeze. `docs/benchmark-v3-config.md` has no post-freeze edits — the
  card-validity correction was placed in `docs/eval-results.md` instead, which
  is the right handling.
- **v1/v2 immutable.** Zero deletions in the v1/v2 halves of
  `docs/eval-results.md`; outside `data/eval/v3/` only the shared derived BM25
  docs cache was rewritten (same benign pattern as v2).
- **Spend $10.7854** ($6.9926 val + $3.7929 test) against the $25 ceiling,
  reconciled by stage name. The $0.1508 aborted finisher attempt is disclosed,
  ledgered, and in no arm; the sol per-call allowance was re-derived under the
  $0.05 ceiling before the re-run.
- **Adoption by construction, rejection by evidence.** Levers 1–3 adopted on
  candidate recall 1.000, window recall 1.000, and cost — all validation or
  construction-level. Self-consistency rejected on the study's only
  zero-excluding interval (MRR CI [−0.156, −0.014]) plus a mechanism (shuffling
  destroys the deterministic-order signal the card exposes); the sol finisher
  rejected on its target metrics, with its impossible Hit@5 "effect" correctly
  identified as retrieval variance. The internal noise gauge (score arm across
  re-rank-only arm pairs) independently reproduces the 0.100 floor.
- **Deviations, accepted as disclosed:** the aborted finisher spend; the
  frozen-record card-validity claim that did not survive the wider window
  (corrected transparently, per-entry-normalized); post-freeze reporting code
  that reads checkpoints only.

Outcome accepted as reported: v3 removed the retrieval ceiling (candidate
recall 0.925 → 0.975; window recall 1.000) and the re-rank did not convert it —
Hit@10 0.833 is the best of the three versions at the lowest cost, while Hit@1
fell 0.308 → 0.225 (6 wins / 16 losses, McNemar p = 0.052), the study's
sharpest signal and a regression that leaves Hit@1 below BM25. The re-rank
remains the binding constraint, now the only one left.

Orchestrator notes of record: (1) engine defaults now carry the frozen v3
configuration; v2's configuration (window 15, profile view, no BM25 arm)
remains the strongest *measured* Hit@1/MRR configuration and both are
reachable via `config/settings.yaml` — demo/pitch usage should pick
deliberately and say which it uses. (2) The 120-case test split is retired;
any v4 experiment requires a freshly cut manifest. (3) The research track's
tuning phase ends here — two rounds of disciplined improvement moved no
full-system aggregate beyond the measured noise floor, which is itself a
finding: on this benchmark, with this label noise, the honest gains left are
in the re-ranker or in cleaner truth, not in retrieval or scoring.
