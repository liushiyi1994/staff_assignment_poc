# Benchmark v2 — frozen configuration

Work order: `docs/work-orders/benchmark-v2.md`. Branch `agent/benchmark-v2`.

This record is written **before** the test split is run and is not edited afterwards.
Everything below was decided on the 30 validation cases alone.

## What changed from v1, and why

**Exactly one lever is adopted: the deterministic score weights.** Everything else the
order listed was implemented, measured on validation, and rejected — including the
re-rank prompt revision, which the order expected to be the main lever.

| Component | v1 | v2 | Why (mechanism, not leaderboard position) |
|---|---|---|---|
| `scoring.weights.specialization_match` | 0.40 | 0.25 | *(rationale below)* |
| `scoring.weights.skill_overlap` | 0.25 | 0.30 | |
| `scoring.weights.recency` | 0.20 | 0.40 | |
| `scoring.weights.evidence_strength` | 0.15 | 0.05 | |

Unchanged: manifest and cases, holdout cutoff, roster construction, retrieval widths
(vector top-40 ∪ structured top-40), re-rank window (15), **the re-rank prompt
(`rerank`)**, intent and re-rank models, embedding model, recency half-life, and every
leakage guard from `docs/work-orders/stage7-benchmark.md`.

The frozen configuration is therefore the one measured as validation arm
`ab_weights_only`.

### Score weights

The four components are combined by `query/rank.py:combine_parts`, which the sweep
re-uses so a swept score is the arithmetic the engine actually computes.

The weighting was **not** taken from the top of the sweep. A 216-point grid over 30
cases has more than enough freedom to fit noise. What was read off the sweep instead is
the *marginal* effect of each component — its mean over every grid point that holds that
component at a given weight (`eval/scores.py:marginal_effects`):

| Component | Mean MRR at weight 0 | at weight 0.60 | Mean window recall, 0 → 0.60 |
|---|---:|---:|---|
| `recency` | 0.231 | **0.349** | 0.781 → **0.956** |
| `evidence_strength` | **0.346** | 0.242 | 0.909 → 0.872 |
| `specialization_match` | **0.310** | 0.258 | 0.933 → 0.773 |
| `skill_overlap` | 0.287 | **0.319** | 0.895 → 0.867 |

* **`recency` improves both monotonically.** Mechanism: the target is who a ticket was
  assigned to, and assignment follows current ownership of an area. Someone who worked
  on a subsystem last quarter is a likelier assignee than someone who worked on it three
  years ago, independently of depth. It gets the largest share.
* **`evidence_strength` degrades MRR monotonically.** Mechanism: it saturates in the
  *count* of supporting contributions, so at a high weight it approximates "who does the
  most work here" — which is the `most_active` baseline, the weakest of the four (v1
  test MRR 0.175). It is reduced to a small residual rather than removed, so a
  single-contribution match still ranks below a repeated one.
* **`specialization_match` trades precision for coverage.** Raising it collapses window
  recall (0.933 → 0.773) because it is a coarse satisfied-fraction that flattens the
  ranking and scores every vector-only candidate at zero — the candidates the union
  exists to include. It is reduced, not removed.
* **`skill_overlap` is the mildest signal** and the only other one pointing up; it
  absorbs weight from `specialization_match` while keeping the "right area" signal.

Robustness check before adopting: every one of the 81 weight vectors in the
neighbourhood `specialization 0.20–0.30 / skill 0.25–0.35 / recency 0.35–0.45 /
evidence 0.00–0.10` beats the v1 weighting on every metric (worst-case MRR 0.286 vs
0.280, worst-case Hit@10 0.733 vs 0.667, worst-case window recall 0.933 vs 0.900). The
adopted vector sits inside a plateau, not on a spike. On the same retrieved pools the
adopted vector scores 0.167 / 0.467 / 0.833 Hit@1/5/10, MRR 0.319, window recall 0.967,
against v1's 0.133 / 0.400 / 0.667, MRR 0.280, window recall 0.900.

### Re-rank prompt — written, measured, NOT adopted

`prompts/rerank_assignee.md` is a revision of `prompts/rerank.md` that re-aligns the
ranking criterion with what the benchmark measures. Every citation and validation rule
was carried over verbatim (the evidence validator in `query/rank.py` depends on them);
everything else changed:

| Change | Intended mechanism |
|---|---|
| Ranking criterion restated as "who the evidence shows is currently working in the specific area this brief is about" | v1 asked for the best-qualified candidate; the benchmark scores who the ticket went to. Ownership of an area is the observable that predicts assignment. |
| Criteria given an explicit priority order: specific overlap → currency → repetition in that same narrow area | v1 listed three criteria with no ordering, leaving the model to weigh them per case. |
| Added: do not reward breadth, total evidence count, or general project activity | v1 explicitly rewarded "breadth vs. the role's skill list", which pulls toward generalists; assignment goes to the specialist who owns the component. |
| `reason` must state which area the evidence covers and when | Makes the currency claim checkable by a reviewer rather than implicit. |
| `fit: "strong"` reserved for recent, specific, repeated evidence | v1 left the grades undefined, so "strong" drifted. |

**It scored below the unrevised prompt on every metric.** Holding the v2 weights fixed
and changing only the prompt (`ab_weights_only` → `ab_weights_prompt`), `capgraph_full`
moved 0.433 → 0.367 Hit@1, 0.767 → 0.700 Hit@5, 0.833 → 0.800 Hit@10, and 0.550 → 0.489
MRR. Every one of those is inside the 0.100 run-to-run noise floor measured below, so
the correct statement is not "the revision is worse" but "there is no evidence it helps,
and the point estimate is worse on all four metrics." A lever with no evidence behind it
is not adopted, so `llm.rerank_prompt` stays at `rerank`.

The prompt file is kept, not deleted: it is the record of what was tried. Nothing loads
it unless `llm.rerank_prompt` names it.

A plausible reading of why it failed, offered as a hypothesis and not as a finding: the
revision tells the model to discount breadth and total evidence volume, but those are
also the signals that separate a plausible assignee from a roster member with one
tangential ticket. Instructing against them may have removed a useful prior along with
the misaligned one. Testing that would need another arm, which the spend allowance did
not have room for.

## Levers considered and rejected

| Lever | Measured on validation | Decision |
|---|---|---|
| RRF fusion of `capgraph_full` × `bm25` | At the standard k=60: Hit@1 0.367 → 0.267, MRR 0.513 → 0.421, buying Hit@10 +0.034 (one case) | **Rejected** |
| RRF fusion of `capgraph_score` × `bm25` | Worse than `capgraph_full` on every metric at every k | **Rejected** |
| Weighted RRF (graph ×1.5–×3) | Recovers toward `capgraph_full` as the graph's weight rises, never past it — which is the tell: the best fusion of the two is not to fuse | **Rejected** |
| Roster backstop | Hit@1/5/10 unchanged, MRR +0.001, candidate recall 0.967 → 1.000 by construction | **Rejected** |
| Re-rank window 15 → 20 | Under the v2 weights, window recall is already 0.967 at 15 — the maximum reachable, since one case's truth is never retrieved at all. Widening cannot raise the ceiling and costs ~30% more prompt tokens | **Rejected as redundant** |
| Assignee-aligned re-rank prompt | Below the unrevised prompt on all four metrics at fixed weights (see above) | **Rejected** |
| Stronger re-rank model (`openai/gpt-5.6-sol`) | **Not run — projection exceeds the escalation threshold.** At $5/$30 per MTok it is 5× the current re-rank model, so one validation arm projects to ≈$5.0 (33 re-rank calls × ≈$0.15). With $2.23 already logged and ≈$4.4 needed for the test run, the total projects to ≈$11.6 — above the order's $10 escalate-first line. It would also need `llm.max_call_cost_usd` raised from $0.05, since a single call estimates ≈$0.21 and would be refused before it was sent. | **Escalated, not run** |

Why fusion cannot work here, stated as a mechanism rather than a number: RRF assumes
the ranked lists it fuses are comparably good. They are not. `capgraph_full` beats
`bm25` by 0.167 on validation Hit@5, so at any k large enough for the lists to vote
roughly equally, fusion imports BM25's worse ordering into a head that was already
better. The one grid point that does not lose (k=1) is the degenerate corner where RRF
stops being a fusion and becomes "trust rank 1", and its gains — one case on Hit@5, one
on Hit@10 — are inside the measured noise.

What the evidence does point to is a *complementary* retriever rather than a fused
ranking: on the one validation case the graph's retrieval union missed entirely, BM25
ranks the truth 5th. A lexical **retrieval arm** inside the union would put such a
candidate in front of the re-rank, where fusion only reshuffles a list that never
contained them. That lever is outside this order's scope and was **not** implemented; it
is recorded as a recommendation.

> **Disclosure.** While first orienting in the v1 checkpoints, before any v2 decision
> was made, I also computed where BM25 ranks the truth for the nine cases the v1 graph
> retrieval missed on the **test** split (ranks 1, 1, 1, 4, 8, 9, 13, 17, 20). That is
> test-split information beyond what the v1 report publishes. No v2 configuration
> decision used it — every lever above was adopted or rejected on the validation split
> alone, and the recommendation above is stated from the validation case. It is recorded
> here because disclosing it is what the split discipline requires.

Full tables are in `docs/eval-results.md` under "Benchmark v2".

## Run plan

1. Score components checkpointed for validation under `stage7b_val` (intent parses
   only) — $0.1137.
2. Two paid validation arms, each in its own checkpoint namespace:
   `ab_weights_only` (v2 weights, v1 prompt) — $1.108 — and `ab_weights_prompt` (v2
   weights, v2 prompt) — $1.012. Total `stage7b_val`: $2.2341.
3. This record frozen, with `ab_weights_only` as the adopted configuration.
4. `test` split run **once** under `stage7b_test` into `data/eval/v2/runs/`.

Spend is bounded by `eval.v2.max_total_cost_usd` ($8), checked before each split starts
against both v2 stages' logged spend combined.

## Validation A/B, as frozen

`capgraph_full`, 30 cases:

| Arm | Hit@1 | Hit@5 | Hit@10 | MRR |
|---|---:|---:|---:|---:|
| v1 (frozen) | 0.367 | 0.733 | 0.833 | 0.513 |
| `ab_weights_only` — **adopted** | 0.433 | 0.767 | 0.833 | 0.550 |
| `ab_weights_prompt` — rejected | 0.367 | 0.700 | 0.800 | 0.489 |

Read against a measured run-to-run noise floor of 0.100 (see `docs/eval-results.md`),
no single delta in this table is individually significant. What justifies adopting the
weights is not this table but the three things behind it: the marginal effect is
monotone across a 216-point grid, the adopted vector sits inside an 81-vector plateau
that beats v1 on every metric, and the offline sweep *predicted* the paid outcome before
it was paid for — it forecast +0.167 on the score arm's Hit@10 and the run delivered
+0.200. The prompt revision has none of that behind it and moved four metrics out of
four the wrong way.
