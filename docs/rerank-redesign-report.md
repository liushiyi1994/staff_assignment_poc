# Re-rank prompt redesign — ranking on evidence instead of position

Generated 2026-08-15 on the benchmark v4 **validation** split (28 cases, rewritten briefs, `v3frozen` engine), manifest `tawos-v1.1-benchmark-v4`. Work order: `docs/work-orders/rerank-redesign.md`.

## What is pinned, and why it matters

Every arm below re-ranks the **same retrieval**: the same intent parses, the same union candidate pools, the same deterministic scores, the same 32-card window. That retrieval was captured once and replayed byte-identically into each arm, so the difference between two arms is the re-rank stage and nothing else. No previous A/B in this project could say that — each re-ran the whole engine, so a fresh intent parse and its retrieval moved with the lever.

Read back out of the arms' own checkpoints rather than asserted from the code path — every arm's recorded candidate pool and deterministic ranking, against the pin it replayed:

| Arm | Cases scored | Candidate pool identical to the pin | `capgraph_score` ranking identical |
|---|---:|---:|---:|
| baseline | 28 | 28 | 28 |
| A | 28 | 28 | 28 |
| B | 28 | 28 | 28 |
| C | 28 | 28 | 28 |

### The baseline is not free, and that is a finding

The work order's plan was for the existing v4 validation run to *be* the "current prompt, ordered" arm at $0, on the grounds that all arms would reuse its checkpointed intent parses and candidate pools. **That run never checkpointed its intent parses** — benchmark v4 records rankings, pools and role *names*, not the parsed specializations and skills that drive retrieval — and re-parsing the same briefs with the same model at temperature 0 does not reproduce them:

| Comparison of the captured pin against the frozen v4 validation run | Cases |
|---|---:|
| Same role names | 4 / 28 |
| Same candidate pool, same order | 2 / 28 |
| Same deterministic `capgraph_score` ranking | 0 / 28 |
| Median pool overlap (Jaccard) | 0.833 |

So the frozen run ranks *a different pool* on almost every case, and pairing an arm against it would measure the prompt plus a fresh draw of retrieval — exactly the confound this study exists to remove. The ordered current-prompt arm is therefore a paid arm like the others. This was escalated before any arm was run; the frozen run is kept above as the evidence, not as a baseline.

## Arms

Every row is `capgraph_full` on the same 28 pinned cases; the last row is the same pool ranked by the deterministic score alone, which is identical in every arm and is the floor an LLM re-rank has to beat to be worth its cost.

| Arm | Prompt | Order | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Cost (USD) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **baseline** — current prompt, ordered | `rerank_cards` | score | 28 | 0.393 | 0.607 | 0.750 | 0.362 | 0.534 | 0.501 | 1.5447 |
| **A** — current prompt, reversed | `rerank_cards` | reverse | 28 | 0.321 | 0.536 | 0.786 | 0.274 | 0.580 | 0.450 | 1.5680 |
| **B** — redesigned prompt, ordered | `rerank_evidence_first` | score | 28 | 0.321 | 0.607 | 0.750 | 0.348 | 0.541 | 0.455 | 2.1363 |
| **C** — redesigned prompt, reversed | `rerank_evidence_first` | reverse | 28 | 0.357 | 0.536 | 0.750 | 0.267 | 0.535 | 0.454 | 2.2162 |
| `capgraph_score` — no re-rank at all | — | — | 28 | 0.143 | 0.500 | 0.679 | 0.244 | 0.449 | 0.319 | 0.0000 |

Cost is the logged spend of that arm's re-rank calls only: retrieval was paid for once, at capture, and is shared by every arm.

## The anti-position mechanism

The redesigned prompt is `prompts/rerank_evidence_first.md`. It keeps the same model, the same window (32), the same card *data* and the same citation rules; what it changes is the order in which the model is made to think, and what it is given to fall back on. Four mechanisms, not a reworded preamble:

1. **Per-candidate assessment emitted before any ranking.** The answer must open with an `assessments` array — one line per candidate, judging that person against the *role* from that person's card alone, explicitly "never against another candidate". Generation is left-to-right, so by the time the model writes a ranking it has already committed to a per-candidate evidence reading; the ranking is conditioned on that text rather than on the input list. The line is a fixed template (id, score, matched terms, last date, tier), which also makes coverage checkable: one line per card, none skipped or merged.
2. **The printed deterministic score as the stated ordering signal.** The card has always carried the score and the model has always ignored it (wave-1 G7). The redesign names it as *the only* ordering signal in the input — "the order the cards are printed in carries no information … the only ordering signal in your input is the `score`" — which replaces the implicit signal the model was using rather than merely forbidding it.
3. **An order-free tie-break.** Position bias does its damage where the evidence does not separate two people, because something has to break the tie and presentation order is the nearest thing to hand. The redesign gives that vacuum an explicit filler: when two pass-1 lines do not separate two candidates, the higher printed score goes first — "never the one that was printed first".
4. **Evidence-grounded comparative justification at the head.** For the top 3 the model must say, in `head_note`, why each ranks above the person immediately below, naming the evidence from its own pass-1 line. Hit@1 is where G7's damage was concentrated, so the justification is required exactly there — and it is required in the model's own earlier words, which a ranking copied from position cannot supply.

**What is deliberately not the mechanism.** The current prompt already ends its first paragraph with "The cards are in no meaningful order", and the G7 probe halved Hit@1 anyway. Telling the model that order is meaningless is therefore known — on this instrument, with this model — to be insufficient on its own. The redesign keeps that framing (it is true, and in the reversed arms it is emphatically true) but does not rely on it: every mechanism above changes what the model must *emit*, and in what order, rather than what it is told.

**Carried over verbatim.** The five ranking rules — rank only who was given, the one-sentence evidence-citing `reason`, the 1-4 own-card `evidence_ticket_keys` with "discarded, not corrected", the `fit` values, and the honest bottom-of-list entry — are byte-identical to `rerank_cards.md`, and a test asserts it. `query/rank.py`'s validator is untouched, so a rejected entry in any arm is discarded exactly as before. The extra `assessments` and `head_note` fields are read by nothing: no prose or citation reaches a shortlist without passing the same `validated_evidence` check.

**And the mechanism was actually exercised.** The arms record what the *validator* kept, which says nothing about whether the assessment pass happened, so it was checked directly on a fresh call per prompt order (`inspect_mechanism`, one re-rank call each, reported not scored):

| Arm | Cards | Assessment lines | Covers every card once | Follows printed order | Template kept | Head notes | Ranked |
|---|---:|---:|---|---|---|---:|---:|
| B | 30 | 30 | yes | yes | yes | 3 | 30 |
| C | 30 | 30 | yes | yes | yes | 3 | 30 |

So the redesign is doing what it says on the tin in both presentation orders. Whatever the ranking numbers below say, they are not the result of a prompt the model quietly ignored.

## Adoption criteria

- **Primary criterion — position dependence.** Reversing the window moves Hit@1 by -0.071 under the current prompt and +0.036 under the redesign (MRR -0.051 and -0.001). The gap is **smaller** under the redesign, and the difference between the two gaps is below half the 0.100 run-to-run floor.
- **Guard — is B worse than the baseline?** B minus baseline, both ordered: Hit@1 -0.071, MRR -0.045 — inside the noise floor, so the guard holds.

## Recommendation

**Do not flip `llm.rerank_prompt` at the next freeze.** The redesign buys no ranking gain — Hit@1 -0.071 against the current prompt in the same order, inside the noise floor and directionally negative — and it costs about 38% more per call, because the assessment pass is output tokens. There is no ranking case for adopting it. *(Measured; the cost figure is the logged per-call spend of the two arms.)*

**But the premise it was commissioned against needs revising, and that is the finding worth carrying forward.** With retrieval pinned, reversing the window under the *current* prompt moves Hit@1 -0.071 on this instrument — inside the 0.100 run-to-run floor, on two discordant cases, with the MRR interval spanning zero — and under the redesign it moves +0.036, which is to say the reversed arm scored marginally *better*. Neither prompt shows a position effect this instrument can resolve. Wave-1's G7 measured −0.200 (p = 0.031) and concluded that presentation order dominates the re-rank. Two things changed at once here, and this study cannot separate them: the instrument (v4 packages carry multi-person truth, so Hit@1 is less sensitive to reshuffling the head) and the confound (G7 compared two *separate engine runs*, so its gap bundled presentation order together with a fresh draw of retrieval; here retrieval is byte-identical by construction). Either way, **the standing pause on re-rank tuning rests on a number that this instrument does not reproduce.** *(The two gaps are measured; which of the two explanations dominates is reasoned, and this study is not built to separate them.)*

**The re-rank earns its keep, and not by following order.** Against the identical pool ranked by deterministic score alone, the re-rank adds Hit@1 +0.250 / MRR +0.182 under the current prompt — and still adds Hit@1 +0.179 / MRR +0.131 when its window is handed to it worst-first. A re-rank that were substantially re-expressing presentation order could not do that. This is the number the work order asked for to size the re-rank's real contribution, and it is the first time it has been measurable without retrieval moving underneath it. *(Measured.)*

**Keep the redesigned prompt as a file, for its citation behaviour.** Its one unambiguous effect is on evidence discipline: the validator discarded 0.2% of its entries in both orders, against 0.6% for the current prompt ordered and **2.8% reversed**. The current prompt degrades sharply when the window is perturbed — inventing people who were not candidates — and the redesign does not. Nothing in this study's ranking metrics rewards that, but a shortlist whose citations survive a perturbed input is worth more in the MVP than a 0.036 Hit@1 difference is worth here. *(The rejection rates are measured; what they are worth in the MVP is reasoned.)*

**The algorithmic alternatives the work order names — setwise selection over the top 5, pointwise scoring with a deterministic tie-break — should not be commissioned on the strength of the position argument.** They were motivated by a position effect that this instrument puts inside the noise. If the re-rank is to be improved further, the case for it now has to be built on something this study did measure, and the honest reading is that the remaining headroom is not in the re-rank: it turns a 0.143 Hit@1 pool into 0.393, and the pool is what is weak. *(The pool and re-ranked figures are measured; that retrieval is the better place to spend next is reasoned.)*

**On the deferred iteration.** The work order allows one B′/C′ iteration if ≥ $1.50 of ceiling remains. $0.04 remains, so no iteration was run — and on these numbers none is warranted: there is no measured gap left for a second draft of the wording to close.

## Paired per-case statistics

**Position control on the current prompt — baseline (ordered) against A (reversed). This is the G7 effect on this instrument.**

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.393 | 0.321 | -0.071 | 0 | 2 | 26 | McNemar exact p = 0.500 |
| Hit@5 | 28 | 0.607 | 0.536 | -0.071 | 1 | 3 | 24 | McNemar exact p = 0.625 |
| Hit@10 | 28 | 0.750 | 0.786 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.362 | 0.274 | -0.088 | 1 | 6 | 21 | 95% bootstrap CI [-0.202, +0.007] |
| Recall@10 | 28 | 0.534 | 0.580 | +0.046 | 4 | 5 | 19 | 95% bootstrap CI [-0.053, +0.165] |
| MRR | 28 | 0.501 | 0.450 | -0.051 | 6 | 8 | 14 | 95% bootstrap CI [-0.143, +0.018] |

**Position control on the redesigned prompt — B (ordered) against C (reversed). The gap the redesign had to close.**

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.321 | 0.357 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.607 | 0.536 | -0.071 | 1 | 3 | 24 | McNemar exact p = 0.625 |
| Hit@10 | 28 | 0.750 | 0.750 | +0.000 | 1 | 1 | 26 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.348 | 0.267 | -0.081 | 2 | 8 | 18 | 95% bootstrap CI [-0.209, +0.046] |
| Recall@10 | 28 | 0.541 | 0.535 | -0.007 | 3 | 5 | 20 | 95% bootstrap CI [-0.114, +0.103] |
| MRR | 28 | 0.455 | 0.454 | -0.001 | 7 | 10 | 11 | 95% bootstrap CI [-0.066, +0.068] |

**The guard — B against the baseline, both ordered. Is the redesign worse than what it replaces?**

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.393 | 0.321 | -0.071 | 1 | 3 | 24 | McNemar exact p = 0.625 |
| Hit@5 | 28 | 0.607 | 0.607 | +0.000 | 1 | 1 | 26 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.750 | 0.750 | +0.000 | 0 | 0 | 28 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.362 | 0.348 | -0.014 | 2 | 2 | 24 | 95% bootstrap CI [-0.121, +0.089] |
| Recall@10 | 28 | 0.534 | 0.541 | +0.008 | 3 | 1 | 24 | 95% bootstrap CI [-0.018, +0.033] |
| MRR | 28 | 0.501 | 0.455 | -0.045 | 6 | 9 | 13 | 95% bootstrap CI [-0.139, +0.035] |

**Both prompts under reversed presentation — A against C. What the redesign is worth when order cannot help.**

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.321 | 0.357 | +0.036 | 1 | 0 | 27 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.536 | 0.536 | +0.000 | 1 | 1 | 26 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.786 | 0.750 | -0.036 | 1 | 2 | 25 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.274 | 0.267 | -0.007 | 2 | 4 | 22 | 95% bootstrap CI [-0.089, +0.091] |
| Recall@10 | 28 | 0.580 | 0.535 | -0.045 | 5 | 4 | 19 | 95% bootstrap CI [-0.167, +0.051] |
| MRR | 28 | 0.450 | 0.454 | +0.004 | 8 | 8 | 12 | 95% bootstrap CI [-0.031, +0.050] |

## What the re-rank is worth over the deterministic score

With retrieval pinned, `capgraph_score` is the same ranking of the same pool in every arm, so each block below is the whole contribution of that arm's LLM call — and in a reversed arm, that contribution with presentation order taken away.

**baseline** (current prompt, ordered) against `capgraph_score` on the same pool:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.393 | +0.250 | 8 | 1 | 19 | McNemar exact p = 0.039 |
| Hit@5 | 28 | 0.500 | 0.607 | +0.107 | 5 | 2 | 21 | McNemar exact p = 0.453 |
| Hit@10 | 28 | 0.679 | 0.750 | +0.071 | 5 | 3 | 20 | McNemar exact p = 0.727 |
| Recall@5 | 28 | 0.244 | 0.362 | +0.119 | 10 | 2 | 16 | 95% bootstrap CI [-0.015, +0.254] |
| Recall@10 | 28 | 0.449 | 0.534 | +0.084 | 10 | 7 | 11 | 95% bootstrap CI [-0.092, +0.260] |
| MRR | 28 | 0.319 | 0.501 | +0.182 | 20 | 4 | 4 | 95% bootstrap CI [+0.046, +0.314] |

**A** (current prompt, reversed) against `capgraph_score` on the same pool:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.321 | +0.179 | 6 | 1 | 21 | McNemar exact p = 0.125 |
| Hit@5 | 28 | 0.500 | 0.536 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.679 | 0.786 | +0.107 | 5 | 2 | 21 | McNemar exact p = 0.453 |
| Recall@5 | 28 | 0.244 | 0.274 | +0.031 | 8 | 4 | 16 | 95% bootstrap CI [-0.077, +0.126] |
| Recall@10 | 28 | 0.449 | 0.580 | +0.130 | 10 | 8 | 10 | 95% bootstrap CI [-0.039, +0.306] |
| MRR | 28 | 0.319 | 0.450 | +0.131 | 17 | 5 | 6 | 95% bootstrap CI [+0.022, +0.245] |

**B** (redesigned prompt, ordered) against `capgraph_score` on the same pool:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.321 | +0.179 | 6 | 1 | 21 | McNemar exact p = 0.125 |
| Hit@5 | 28 | 0.500 | 0.607 | +0.107 | 5 | 2 | 21 | McNemar exact p = 0.453 |
| Hit@10 | 28 | 0.679 | 0.750 | +0.071 | 5 | 3 | 20 | McNemar exact p = 0.727 |
| Recall@5 | 28 | 0.244 | 0.348 | +0.104 | 10 | 2 | 16 | 95% bootstrap CI [-0.026, +0.236] |
| Recall@10 | 28 | 0.449 | 0.541 | +0.092 | 9 | 6 | 13 | 95% bootstrap CI [-0.083, +0.267] |
| MRR | 28 | 0.319 | 0.455 | +0.137 | 18 | 4 | 6 | 95% bootstrap CI [+0.012, +0.255] |

**C** (redesigned prompt, reversed) against `capgraph_score` on the same pool:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.357 | +0.214 | 7 | 1 | 20 | McNemar exact p = 0.070 |
| Hit@5 | 28 | 0.500 | 0.536 | +0.036 | 3 | 2 | 23 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.679 | 0.750 | +0.071 | 4 | 2 | 22 | McNemar exact p = 0.688 |
| Recall@5 | 28 | 0.244 | 0.267 | +0.024 | 6 | 5 | 17 | 95% bootstrap CI [-0.094, +0.143] |
| Recall@10 | 28 | 0.449 | 0.535 | +0.085 | 9 | 7 | 12 | 95% bootstrap CI [-0.067, +0.239] |
| MRR | 28 | 0.319 | 0.454 | +0.135 | 16 | 5 | 7 | 95% bootstrap CI [+0.005, +0.268] |

## Rejection accounting

| Arm | Cases | Entries offered | Accepted | Rejected | Rate | Reason classes |
|---|---:|---:|---:|---:|---:|---|
| baseline | 28 | 1687 | 1676 | 11 | 0.006 | cites evidence not in this person's contributions, duplicate entry |
| A | 28 | 1725 | 1676 | 49 | 0.028 | cites evidence not in this person's contributions, duplicate entry, not among the ranked candidates |
| B | 28 | 1681 | 1677 | 4 | 0.002 | cites evidence not in this person's contributions, not among the ranked candidates |
| C | 28 | 1681 | 1678 | 3 | 0.002 | cites evidence not in this person's contributions |

Rejected entries are discarded, never repaired — the validator in `query/rank.py` is untouched by this study and every arm's citations pass exactly the same check.

## What this study cannot say

- **28 cases is a small instrument.** The run-to-run floor quoted above (0.100 Hit@1) was measured on the v1 benchmark by re-running one configuration twice; benchmark v4 has never had a floor of its own measured. Every delta here is read against that borrowed gauge, and the paired win/loss counts are more informative than the aggregates.
- **One run per arm.** Nothing here separates a prompt effect from a single draw of sampling variance in the model's own output; the arms are not repeated.
- **Pinning removes retrieval variance, not model variance.** The retrieval is provably identical across arms. The model is called afresh in every arm, at temperature 0 but with no other determinism guarantee.
- **Validation only.** No result here has been checked on the v4 test split, which this study never reads. Flipping `llm.rerank_prompt` is a later config-freeze decision, not this study's to make — defaults are untouched.
- **The target is still assignee prediction.** Ranking people who did the work first is evidence of relevance, not proof of optimal staffing.

## Spend

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `rerank_redesign` | 259 | 7.9603 |

| Call type | Calls | Cost (USD) |
|---|---:|---:|
| `intent` | 28 | 0.1550 |
| `mechanism_check` | 4 | 0.1434 |
| `rerank` | 227 | 7.6619 |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included: **$7.9603** of the $8.00 the owner authorized on 2026-08-15 (raised twice that day from the work order's $6, once the baseline turned out to need paying for and again once the arms' per-call cost was measured rather than projected). Every call this study made is under one stage name.
