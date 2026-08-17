# Benchmark v3 — frozen configuration

Work order: `docs/work-orders/benchmark-v3.md`. Branch `agent/benchmark-v3`.

This record is written **before** the test split is run and is not edited afterwards.
Everything below was decided on the 30 validation cases and on construction-level
mechanisms alone. Configuration digest of the frozen validation arm:
`1b74f4a2022b5cd7`.

## What changed from v2, and why

**Three of the five levers are adopted: the lexical retrieval arm, the compact candidate
cards, and the re-rank window at 32.** The two paid re-rank levers were implemented,
measured on validation, and rejected.

| Component | v2 | v3 | Why (mechanism, not leaderboard position) |
|---|---|---|---|
| Candidate generation | vector top-40 ∪ structured top-40 | + BM25 top-10 (`retrieval.bm25_top_k`) | Validation candidate recall 0.967 → **1.000**, for a median of one extra candidate per pool |
| Re-rank candidate view | full profile with contribution summaries | fixed-size card (`retrieval.rerank_candidate_view: card`) | Citation rejections 8 → **0**; re-rank input cost −38%, which is what pays for the wider window |
| Re-rank prompt | `rerank` | `rerank_cards` (20aa66afc9d8) | Describes the card fields and the deterministic score; every citation and validation rule carried over verbatim |
| Re-rank window | 15 | **32** | Validation window recall 0.833 → **1.000**: 32 exceeds the deterministic rank of every pool-resident truth (max 27) |
| Re-rank samples | 1 | 1 (unchanged) | Permutation self-consistency measured and rejected — see below |
| Strong-model finisher | none | none (unchanged) | `openai/gpt-5.6-sol` top-5 finisher measured and rejected — see below |

Unchanged: manifest and cases, holdout cutoff, roster construction, score weights,
intent and re-rank models, embedding model, recency half-life, and every leakage guard
from `docs/work-orders/stage7-benchmark.md`.

The frozen configuration is therefore the one measured as validation arm
`ab_window32`.

## The instrument, before the results

Every validation arm re-runs the whole pipeline, and two of its steps are model calls.
The intent parse decides what is retrieved, so **two arms never see the same candidate
pools**, and an arm-to-arm delta is a lever plus a fresh draw of run-to-run variance.
v2 measured that variance at 0.100 by re-running one configuration twice. v3 can measure
it inside its own arms, because the deterministic `capgraph_score` arm ranks the whole
pool and never sees a prompt, a window, or a sample — no lever in arms 1–3 can touch it,
so whatever it moves by is noise:

| Comparison (nothing in it can move the score arm) | Hit@1 | Hit@5 | Hit@10 | MRR |
|---|---:|---:|---:|---:|
| `ab_lexical` → `ab_cards` (prompt + view only) | −0.067 | −0.067 | −0.033 | −0.050 |
| `ab_cards` → `ab_window32` (window only) | +0.033 | +0.033 | +0.100 | +0.034 |

So a 0.100 swing on this benchmark's 30 validation cases is what *no change at all*
produces, reproducing v2's figure from a different direction. Every table below is read
against that, and no lever is adopted or rejected on a delta alone.

## Lever 1 — BM25 arm in the candidate union: **adopted**

Implemented as an engine feature (`retrieval.bm25_top_k`, 0 disables it), reusing the
benchmark's own BM25 index rather than a second lexical retriever: `capgraph/lexical.py`
now builds the per-person index that both `eval/baselines.py` and
`query/retrieve.py` consume, so the arm is exactly the `bm25` baseline's ranking
truncated to its top 10. Union, never fusion — v2 measured vanilla RRF of these two
lists dragging the stronger one down. As-of discipline is the baseline's, unchanged: the
corpus is the Stage 1 pre-cutoff evidence view and every query time is after the cutoff.

Offline from the v3 score checkpoint, on identical retrieval:

| Pool | Candidate recall | Median pool |
|---|---:|---:|
| vector ∪ structured | 0.967 | 37 |
| vector ∪ structured ∪ BM25 top-10 | **1.000** | 38 |

One validation case (`TIMOB-27583`) has its truth in the pool only because of this arm.
Across the split the arm contributes 56 candidates no other arm found, at a median of
one per pool.

**A scoring change was required to make the arm more than cosmetic.** BM25 ranks a
person's whole evidence document, so unlike the vector arm it cannot say which
contributions matched. Without a relevance source, a lexical-only candidate scored a
structural zero on all four components and sorted last however well its evidence
matched — candidate recall would have risen and nothing else. `query/rank.py` now falls
back to the contributions expansion retained, exactly as the vector arm falls back to
its hits. The fallback is reached only when a candidate has *neither* a matched term
*nor* a vector hit, which no v1/v2 pool member could be, so it cannot change any earlier
number; a test pins that. With it, 55 of those 56 lexical-only candidates land inside the
top 32.

**The arm cannot pay off at a window of 15**, and that is visible before any money is
spent: with or without it, validation window recall at 15 is 0.833. Levers 1 and 3 are
therefore adopted as a pair, which is also why the paid `ab_lexical` arm shows nothing —
see the A/B table.

## Lever 2 — compact candidate cards: **adopted**

`prompts/rerank_cards.md` renders each candidate as a fixed structure: pseudonym,
deterministic score, retrieval arms, top specializations and skills as
`term (xN, last YYYY-MM-DD)`, and up to three cite-able evidence ticket keys. The
contribution summaries — which dominate the old view and vary tenfold in length between
candidates — are gone. Every citation and validation rule was carried over verbatim,
because `query/rank.py`'s evidence validator depends on them, and the card shows fewer
keys than the validator checks against, so it cannot widen what may be cited.

Two measured, construction-level effects, neither of them a leaderboard row:

* **Citation rejections went 8 → 0** on the same 30 cases (`ab_lexical` → `ab_cards`).
  Every entry the re-rank produced was valid on the first attempt. That is the failure
  mode the whole evidence-citation design exists to prevent, and the uniform card is
  what removed it.
* **Re-rank spend fell 38%** ($1.0248 → $0.6310 for 30 cases). That is what makes lever
  3 affordable: at window 32 the card arm costs $0.8832, *below* v2's $0.9960 at window
  15.

Its own metric deltas are inside the noise band and mixed in sign (Hit@1 −0.100 with 3
wins and 6 losses, Hit@5 +0.067 with 4 and 2). Neither is evidence of anything. The card
is adopted for the two effects above, not for those.

## Lever 3 — re-rank window 15 → 32: **adopted**

Gated on a validation no-regression A/B, and justified by construction. On the v3 score
checkpoint the truth's deterministic rank across the 30 validation cases is at most
**27**:

    1 1 1 2 2 2 2 2 3 3 3 4 5 5 6 6 7 9 9 9 10 11 12 15 15 17 18 21 24 27

A window of 32 therefore shows the re-rank the truth in every case whose pool contains
it, and with lever 1 that is every case:

| Pool | Window 15 | Window 20 | Window 32 | Window 40 |
|---|---:|---:|---:|---:|
| with lexical arm | 0.833 | 0.900 | **1.000** | 1.000 |
| without lexical arm | 0.833 | 0.867 | 0.967 | 0.967 |

Window recall is the ceiling on the full system's Hit@K — a candidate the re-rank is
never shown cannot be ranked — so this is the only lever in v3 that removes a hard
limit rather than reordering inside one. 32 also sits below the median pool (38), so the
window is not simply "rank everything".

The paid A/B is consistent with no regression: Hit@1 +0.100 (**3 wins, 0 losses**),
Hit@10 +0.100 (4 wins, 1 loss), MRR +0.071 (95% bootstrap CI [−0.006, +0.167]), against
Hit@5 −0.100 (2 wins, 5 losses). Every one of those is inside the noise band measured
above, so the correct statement is "no evidence of regression", not "it gains 0.100".

## Lever 4 — permutation self-consistency: implemented, measured, **NOT adopted**

Three listwise re-ranks over independently shuffled candidate orders, run concurrently
and aggregated by Borda count (`retrieval.rerank_samples`). Implemented as described in
Tang et al. (NAACL 2024): all samples shuffled, seeded from the shortlist so the arm is
reproducible, and aggregation restricted to people at least one sample ranked, so it can
never buy coverage a single call would not have had. Kemeny was not implemented — exact
Kemeny aggregation is NP-hard and a 32-item window puts it out of reach; Borda is the
order's stated fallback.

Against `ab_window32`, holding everything else fixed:

| Metric | window 32 | +self-consistency | Δ | Wins | Losses | Test |
|---|---:|---:|---:|---:|---:|---|
| Hit@1 | 0.400 | 0.267 | −0.133 | 0 | 4 | McNemar exact p = 0.125 |
| Hit@5 | 0.633 | 0.633 | +0.000 | 1 | 1 | p = 1.000 |
| Hit@10 | 0.867 | 0.833 | −0.033 | 2 | 3 | p = 1.000 |
| MRR | 0.523 | 0.443 | −0.080 | 6 | 12 | 95% bootstrap CI **[−0.156, −0.014]** |

That MRR interval is the only one in the whole v3 study that excludes zero, and it
points down. The score-arm gauge for this same pair moved MRR by +0.006, so retrieval
variance does not explain it. It costs 3.1× the re-rank spend ($2.7174 against $0.8832)
and 1.7× the latency. Rejected on the number *and* on a mechanism: the method
marginalizes out position bias by averaging over permutations, which is worth doing when
the input order carries no information — but here the input order is the deterministic
score, and the card puts that score in front of the model anyway. Shuffling destroys a
signal the model was using and Borda averages three noisier lists.

## Lever 5 — strong-model finisher for Hit@1: implemented, measured, **NOT adopted**

One `openai/gpt-5.6-sol` setwise call over the top-5 cards, ordering ids only: the
finisher may permute entries the validated re-rank already produced and may not add a
person, write a reason, or cite anything, so nothing reaches the shortlist without
having passed the evidence validator. `openai/gpt-5.6-sol` was added to
`llm.model_providers` and to `llm.pricing_usd_per_mtok` at $5.00/$30.00 per MTok,
verified against the OpenRouter models API on 2026-08-12 (`pricing.prompt` 0.000005,
`pricing.completion` 0.00003), and the per-call ceiling was checked before the first
call was sent.

**This lever can only move Hit@1 and MRR.** It permutes a prefix of at most five entries
and preserves the set, so for a single-role case the top-5 membership — and therefore
Hit@5 and Hit@10 — is invariant by construction. On the metrics it can reach:

| Metric | window 32 | +finisher | Δ | Wins | Losses | Test |
|---|---:|---:|---:|---:|---:|---|
| Hit@1 | 0.400 | 0.333 | −0.067 | 1 | 3 | McNemar exact p = 0.625 |
| MRR | 0.523 | 0.498 | −0.025 | 8 | 8 | 95% bootstrap CI [−0.107, +0.054] |

No evidence it helps, the point estimate on its target metric is negative, and it adds
$0.0226 per case and ~15 s of latency. Rejected.

**The same arm produced the clearest warning in this work order.** It also showed Hit@5
+0.133 with **4 wins and 0 losses** (p = 0.125) — a table that reads like a real effect.
All four of those cases are single-role, where the finisher cannot change the top-5 set
at all. Every one of them is re-retrieval variance wearing a lever's name. That is the
concrete reason nothing in v3 is adopted on a paired win/loss count.

## Levers considered and not spent on

| Lever | Decision |
|---|---|
| HyDE / doc2query query expansion | Not implemented — the order records it as harmful to non-weak retrievers (EACL Findings 2024, arXiv:2309.08541) |
| Same-dimension embedding swap | Not implemented — ~0.3 MTEB point for a forced reindex |
| SPLADE / ColBERT | Not implemented — infrastructure without a case at 2,666 contributions |
| Long-CoT reasoning re-rankers | Not implemented (arXiv:2505.16886) |
| Vanilla RRF of the graph ranking with BM25 | Not re-run — v2 measured it losing, and v3 unions instead of fusing |
| LLM fine-tuning for triage | Not implemented (arXiv:2508.21156) |
| Decayed-activity fourth retrieval arm | Not implemented — BM25 closes validation candidate recall at 1.000, so there is no residual miss to chase |
| Score-weight retuning | Out of scope; v2's weighting is carried unchanged |

## Validation A/B, as frozen

`capgraph_full`, 30 cases, each arm changing one thing against the arm above it:

| Arm | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| v2 (frozen) | 0.433 | 0.767 | 0.833 | 0.550 | 0.967 | 0.9960 |
| `ab_lexical` — + BM25 arm | 0.400 | 0.667 | 0.800 | 0.519 | 1.000 | 1.0248 |
| `ab_cards` — + card view/prompt | 0.300 | 0.733 | 0.767 | 0.452 | 1.000 | 0.6310 |
| `ab_window32` — + window 32 — **adopted** | 0.400 | 0.633 | 0.867 | 0.523 | 1.000 | 0.8832 |
| `ab_selfconsistency` — + 3 samples — rejected | 0.267 | 0.633 | 0.833 | 0.443 | 1.000 | 2.7174 |
| `ab_finisher` — + sol top-5 — rejected | 0.333 | 0.767 | 0.867 | 0.498 | 1.000 | 1.4718 |

Read against the 0.100 noise band, no single delta in this table is individually
significant, and the frozen arm is not the table's best row on Hit@1 or Hit@5. What
justifies the adopted configuration is the three things behind it: candidate recall is
1.000 rather than 0.967, window recall is 1.000 rather than 0.833 because the window
now exceeds every pool-resident truth's deterministic rank, and the re-rank produced
zero invalid citations rather than eight — all three measured on validation, all three
true by construction rather than by margin, and all three at a cost *below* v2's.

## Run plan

1. Score components checkpointed for validation under `stage7c_val` (intent parses
   only) — $0.1136.
2. Five paid validation arms, each in its own checkpoint namespace, in the order the
   work order lists them: `ab_lexical` ($1.0248), `ab_cards` ($0.6310),
   `ab_window32` ($0.8832), `ab_selfconsistency` ($2.7174), `ab_finisher` ($1.4718).
   A sixth $0.1508 sits in the ledger and in no arm: the first `ab_finisher` attempt
   was stopped after four cases and its checkpoint discarded, because a 400-token
   finisher allowance truncated sol's answer mid-JSON and paid for a retry each time.
   Sum: $6.9926, reconciling exactly with `stage7c_val` in `data/llm_costs.jsonl`.
3. This record frozen, with `ab_window32` as the adopted configuration.
4. Baselines (no model call) into the frozen namespaces.
5. `test` split run **once** under `stage7c_test` into `data/eval/v3/runs/`.

Spend is bounded by `eval.v3.max_total_cost_usd` ($25, the owner's 2026-08-12
authorization), checked before each split starts against both v3 stages' logged spend
combined. The projection is derived from the live configuration rather than a single
per-case constant, because a v3 arm can triple the re-rank calls or add a call on a
model five times the price. The frozen configuration projects **$4.80** for the 120-case
test split, against $6.9926 already logged — $11.79 of the $25 ceiling.

## Test-split wear

This is the **third and last** exposure of the 120 test cases on manifest
`tawos-v1.1-benchmark-v1`. Any v4 needs a freshly cut manifest.
