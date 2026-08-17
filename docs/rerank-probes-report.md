# Re-rank probes — SOTA methods against the last-mile ranking

Benchmark v4 **validation** split (28 cases, rewritten briefs, `v3frozen` engine), manifest `tawos-v1.1-benchmark-v4`. Work order: `docs/work-orders/rerank-probes.md`. Ceiling $15 under stage `rerank_probes`; no v4 test-split access of any kind.

Read the labels: **measured** means the sentence restates a number in this document; **reasoned** means it is a judgement about what to do with those numbers, and a different reader could land somewhere else.

## What this study inherits, and does not re-buy

Every arm here replays the re-rank redesign study's pin (`data/eval/rerank_redesign/pin/`) **read-only** and is paired against that study's baseline arm, whose checkpoints are also read read-only. No retrieval is captured, no baseline is re-run, and the identity of both to the pin is checked from the artifacts rather than assumed (table below).

- **Baseline** (rerank-redesign reference arm, `rerank_cards`, score order, `gpt-5.6-terra`): Hit@1 **0.393** / Hit@5 **0.607** / MRR **0.501**.
- **Floors** (benchmark v4's own, per-metric, measured by a pinned-pool repeat of that exact arm — `docs/deterministic-sweeps-report.md`): Hit@1 **0.036**, Hit@5 **0.071**, Recall@5 **0.095**, MRR **0.034**. v1's borrowed 0.100 is not used anywhere here.
- **Dead ends not respent**: prompt rewording (evidence-first, no gain), permutation self-consistency / shuffle+vote (hurt; refused in code now, not merely avoided), strong-model top-5 permutation finisher (hurt), position controls (inside noise on pinned pools).

## Pre-registrations

Each arm below was written down — method, citation, mechanism, projected cost, failure condition — **before its first call**, and the study refuses to spend on an arm that has no section here (`assert_preregistered`, tested). Two arms, then the work order's sequencing gate.

### Arm S1 — strong model, full 32-card window, listwise permutation

- **Method and citation.** Listwise permutation generation with a stronger ranker: RankGPT (Sun et al., *Is ChatGPT Good at Search?*, arXiv:2304.09542). The window is presented in one call and the model answers with a permutation of the candidate identifiers. Model `openai/gpt-5.6-sol` (5× terra's price, already in the provider and pricing maps) over the identical 32-card window, identical cards, identical presentation order.
- **Mechanism aimed at the bucket.** The misses are not retrieval misses — on this split a correct person is inside the shown window in **17 of 17** top-1 misses, at a median depth of 11 in the deterministic ordering. What fails is discrimination across a wide window of near-identical teammates. RankGPT's finding is that this discrimination is where ranker strength shows up most, and that it shows up when the model is made to commit to a *whole-list* ordering rather than to per-item judgements. This is the one arm on the menu never tried anywhere in this project at full window.
- **Deviation from the order's suggestion, and why (declared before spending).** The order recommends "sol listwise over the full 32-card window" and separately authorizes a scoped per-call ceiling of $0.15. Those two do not both fit: the baseline's claim-generating answer averages 3,272 output tokens (max 5,380, measured over its own 54 calls in `data/llm_costs.jsonl`), so a sol arm answering in that shape needs a worst-case allowance costing $0.161 in output alone — a pre-call estimate of **$0.21**, which the gateway refuses at $0.15. Capping the allowance to fit would truncate ~20% of calls, and each truncation is a four-attempt retry at full price. So this arm uses RankGPT's **own** answer format — an ordering of ids, no prose — which is both faithful to the citation and affordable: worst-case pre-call estimate **$0.1332**, verified for all 54 calls offline before spending (`--preflight S1`). This is a **reorder-only** arm: it generates no claims, so it produces no reasons and cannot graduate on its own without a reasons pass. That cost is priced in the recommendation, not hidden.
- **Projected cost.** 54 calls, mean 7,980 input tokens. At an assumed 1,200 output tokens (sol's reasoning is billed as output; this project has only measured it on the 5-card finisher, at mean 317) that is **$0.076/call ≈ $4.10**. The guard projects at $0.11/call and re-checks before every chunk, so the arm stops rather than overruns if sol's reasoning runs longer than assumed.
- **Failure condition.** No signal if Hit@1 and MRR both move by less than their measured floors (0.036 / 0.034) against the baseline, or move beyond a floor with paired losses ≥ wins. A drop beyond a floor is a result too, and closes the model-strength hypothesis rather than inviting a retry with different wording.

### Arm S2 — rich evidence for the head, cards for the tail

- **Method and citation.** A hybrid candidate view: full contribution detail (the v1/v2 `profile_view` — summaries, periods, projects, per-contribution evidence keys) for the top 8 of the deterministic ordering, the compact v3 card for everyone else. Summarization-augmented re-ranking direction, arXiv:2603.24204 — thin evidence, cheap A/B, which is why it is an arm and not an adoption. Model, window, presentation order and answer shape are the baseline's; the candidate view is the single lever.
- **Mechanism aimed at the bucket.** A card carries capability *terms* with counts and dates and nothing else. Two people on the same project in the same quarter therefore have near-identical cards, and the observed failure is exactly that: the re-rank picks the wrong one of several plausible teammates. Contribution summaries are the only place in this data where what a person actually did is written down, so they are the only signal available that can separate two identical-looking cards. The card view removed them to make a 32-wide window affordable; this arm buys them back where the decision is actually made.
- **Why the head is 8.** Measured, not copied from the menu. Of the 17 shown-but-ranked-low misses, the number whose best truth person sits inside the top *k* of some role window is 6 at k=4, **8 at k=8**, 9 at k=12, 10 at k=16. The curve is flat between 8 and 16 while the added input roughly doubles, so 8 is where the mechanism stops paying for itself. This also bounds the arm honestly: **at most 8 of the 17 misses are reachable by it at all**, and an arm that fixed every one of them would move Hit@1 by at most +0.286.
- **Projected cost.** 54 calls at mean 16,744 input tokens against the baseline's 8,131 — roughly double, rendered offline from the pin through the real prompt builder. At terra's $1/$6 per MTok and the baseline's own output profile: **$0.036/call ≈ $1.96**. Worst-case pre-call estimate $0.0498, inside the unchanged $0.05 default per-call ceiling for all 54 calls (`--preflight S2`); no scoped raise is used or needed by this arm.
- **Prompt.** `prompts/rerank_hybrid_cards.md`. The five ranking rules and the answer schema are **byte-identical** to `rerank_cards.md` and a test asserts it; the only change is the paragraph that describes the data, which has to change because the data did. The evidence validator is untouched, so a citation that is not the candidate's own is discarded exactly as before.
- **Failure condition.** No signal if Hit@1 and MRR both move by less than their floors, or beyond a floor with paired losses ≥ wins. Because the reachable ceiling is 8 of 17 cases, a null result here is informative rather than ambiguous: it would say that richer evidence does not separate close teammates *even where it was supplied*.

### Sequencing gate

After these two arms, a third is run only if at least one shows a movement beyond its per-metric floor on Hit@1 or MRR **with paired support** (wins > losses). Otherwise the study stops and the remaining budget returns. The gate is enforced in code (`assert_gate_open`), not left to judgement.

<!-- measured-sections-below -->

## What is pinned, and proven so

Every arm below re-ranks the **same retrieval** as the rerank-redesign baseline it is paired against: the same intent parses, the same union candidate pools, the same deterministic scores, the same window. This study captured no retrieval of its own — it replays that study's pin (`data/eval/rerank_redesign/pin`) read-only, and pays for re-rank calls only.

Read back out of the arms' own checkpoints rather than asserted from the code path. The baseline row is the load-bearing one: it is what licenses pairing a probe against a number measured in a different study.

| Arm | Cases scored | Candidate pool identical to the pin | `capgraph_score` ranking identical |
|---|---:|---:|---:|
| baseline (baseline, read-only) | 28 | 28 | 28 |
| S1 | 28 | 28 | 28 |
| S2 | 28 | 28 | 28 |

## The bucket these probes aim at, measured on the pin

The work order commissions probes against one failure bucket. On this split it is not a fraction of the misses — it is all of them: of the 17 top-1 misses the baseline makes over 28 cases, **17 had a correct person inside the window the model was shown**, 0 had one in the pool but outside the window, and 0 had none in the pool at all. Retrieval is not what loses these cases. *(Measured.)*

How deep those people sit matters, because it decides whether a method that spends extra attention on the head of the window can reach them at all. Of those 17 misses, the number whose best truth person is inside the top *k* of the deterministic ordering of some role window:

| Detail head *k* | Misses reachable |
|---:|---:|
| 4 | 6 / 17 |
| 8 | 8 / 17 |
| 12 | 9 / 17 |
| 16 | 10 / 17 |
| 24 | 15 / 17 |
| 32 | 17 / 17 |

The curve is flat after 8: widening the detailed head from 8 to 16 reaches 2 more of these misses while roughly doubling the tokens spent on detail. *(Measured; the choice of 8 that follows is reasoned from it.)*

## Arms

Every row is `capgraph_full` on the same 28 pinned cases. The baseline row is the rerank-redesign reference arm, read out of its checkpoint — this study did not re-run or re-pay for it. The last row is the same pool ranked by the deterministic score alone, identical in every arm.

| Arm | Method | Model | Answer | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Cost (USD) |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **baseline** — rerank-redesign reference arm | listwise cards | `gpt-5.6-terra` | reasons + citations | 28 | 0.393 | 0.607 | 0.750 | 0.362 | 0.534 | 0.501 | 1.5447 |
| **S1** — strong model, full window, permutation answer | listwise permutation generation, stronger model | `gpt-5.6-sol` | ids only | 28 | 0.321 | 0.607 | 0.857 | 0.321 | 0.669 | 0.450 | 6.9795 |
| **S2** — rich evidence for the head, cards for the tail | hybrid card, full contribution detail for the top 8 | `gpt-5.6-terra` | reasons + citations | 28 | 0.429 | 0.571 | 0.679 | 0.313 | 0.491 | 0.516 | 2.0363 |
| `capgraph_score` — no re-rank at all | — | — | — | 28 | 0.143 | 0.500 | 0.679 | 0.244 | 0.449 | 0.319 | 0.0000 |

Cost is the logged spend of that arm's own re-rank calls.

## Against the measured floor

Benchmark v4's own per-metric floor, from a pinned-pool repeat of this exact baseline arm: Hit@1 0.036, Hit@5 0.071, MRR 0.034 (`docs/deterministic-sweeps-report.md`). v1's 0.100 is not used.

| Arm | Metric | Baseline | Arm | Δ | Floor | Beyond floor? | Wins | Losses |
|---|---|---:|---:|---:|---:|---|---:|---:|
| S1 | Hit@1 | 0.393 | 0.321 | -0.071 | 0.036 | **yes** | 0 | 2 |
| S1 | Hit@5 | 0.607 | 0.607 | +0.000 | 0.071 | no | 1 | 1 |
| S1 | MRR | 0.501 | 0.450 | -0.051 | 0.034 | **yes** | 8 | 9 |
| S2 | Hit@1 | 0.393 | 0.429 | +0.036 | 0.036 | no | 2 | 1 |
| S2 | Hit@5 | 0.607 | 0.571 | -0.036 | 0.071 | no | 2 | 3 |
| S2 | MRR | 0.501 | 0.516 | +0.015 | 0.034 | no | 8 | 9 |

## Did the mechanism act where it was aimed?

An aggregate delta cannot separate a targeted mechanism from a lucky draw. An arm that spends its extra evidence on the head of the window can only help a case whose truth person is in that head, so its fixes should concentrate there — and that is checkable per case rather than argued.

| Arm | Top-1 cases fixed | Broken | Fixed where the mechanism applied | Fixed where it did not |
|---|---:|---:|---|---|
| S1 | 0 | 2 | — (mechanism applies to the whole window) | — (mechanism applies to the whole window) |
| S2 | 2 | 1 | 2 / 8 | 0 / 9 |

- **S1** fixed nothing and broke `Week ending 2019-Feb-22`, `Studio 4: RI-22 63`.
- **S2** fixed `Containerization: RI-16 51`, `2020 Sprint 4` and broke `DRP S19-5`.

## Paired per-case statistics

**S1 (strong model, full window, permutation answer) against the rerank-redesign baseline arm, on the same pinned pools.**

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.393 | 0.321 | -0.071 | 0 | 2 | 26 | McNemar exact p = 0.500 |
| Hit@5 | 28 | 0.607 | 0.607 | +0.000 | 1 | 1 | 26 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.750 | 0.857 | +0.107 | 3 | 0 | 25 | McNemar exact p = 0.250 |
| Recall@5 | 28 | 0.362 | 0.321 | -0.041 | 3 | 3 | 22 | 95% bootstrap CI [-0.134, +0.027] |
| Recall@10 | 28 | 0.534 | 0.669 | +0.135 | 6 | 1 | 21 | 95% bootstrap CI [+0.029, +0.265] |
| MRR | 28 | 0.501 | 0.450 | -0.051 | 8 | 9 | 11 | 95% bootstrap CI [-0.125, +0.006] |

**S2 (rich evidence for the head, cards for the tail) against the rerank-redesign baseline arm, on the same pinned pools.**

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.393 | 0.429 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.607 | 0.571 | -0.036 | 2 | 3 | 23 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.750 | 0.679 | -0.071 | 1 | 3 | 24 | McNemar exact p = 0.625 |
| Recall@5 | 28 | 0.362 | 0.313 | -0.050 | 5 | 4 | 19 | 95% bootstrap CI [-0.174, +0.054] |
| Recall@10 | 28 | 0.534 | 0.491 | -0.043 | 6 | 6 | 16 | 95% bootstrap CI [-0.179, +0.086] |
| MRR | 28 | 0.501 | 0.516 | +0.015 | 8 | 9 | 11 | 95% bootstrap CI [-0.055, +0.090] |

## Rejection and validator accounting

| Arm | Generates claims | Cases | Entries offered | Accepted | Rejected | Rate | Reason classes |
|---|---|---:|---:|---:|---:|---:|---|
| baseline | yes (reason + citations) | 28 | 1687 | 1676 | 11 | 0.0065 | cites evidence not in this person's contributions, duplicate entry |
| S1 | no (orders ids only) | 28 | 1681 | 1681 | 0 | 0.0000 | — |
| S2 | yes (reason + citations) | 28 | 1710 | 1675 | 35 | 0.0205 | cites evidence not in this person's contributions, duplicate entry, not among the ranked candidates |

The evidence validator in `query/rank.py` is untouched by this study: a rejected entry is discarded, never repaired, in every arm. A reorder-only arm answers with an ordering of ids and no prose, so it offers the validator nothing to check — its zero rejection rate is a property of the answer shape, not evidence of better citation behaviour, and the `Generates claims` column is there so the two cannot be read as the same thing.

## Sequencing gate

| Arm | Hit@1 Δ | beyond 0.036? | W/L | MRR Δ | beyond 0.034? | W/L | Signal |
|---|---:|---|---|---:|---|---|---|
| S1 | -0.071 | no | 0/2 | -0.051 | no | 8/9 | none |
| S2 | +0.036 | no | 2/1 | +0.015 | no | 8/9 | none |

## Spend

| Arm | Cases | Calls | Cost (USD) | Per call | Projected per call |
|---|---:|---:|---:|---:|---:|
| S1 | 28 | 62 | 6.9795 | $0.1126 | $0.1100 |
| S2 | 28 | 57 | 2.0363 | $0.0357 | $0.0450 |

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `rerank_probes` | 119 | 9.0158 |

| Call type | Calls | Cost (USD) |
|---|---:|---:|
| `rerank` | 119 | 9.0158 |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included: **$9.0158** of the $15.00 the owner authorized on 2026-08-16. Every call this study made is under one stage name.

<!-- measured-sections-above -->
## Answer-integrity checks

Two things had to be true before either arm's ranking numbers could be read as a ranking difference, and both are measured rather than assumed:

- **S1's permutations are complete.** Its accepted entries total **1,681**, which is exactly the total window population across all 54 roles. Every role came back with an ordering of every candidate it was shown. So S1's Hit@1 drop is a genuine ordering difference and not a truncated or partial answer padded back by the deterministic remainder. *(Measured.)*
- **Eight of sol's 62 calls hit the 3,000-token allowance and were retried.** They are the whole of the gap between 54 roles and 62 logged calls, they are included in the $6.98, and each eventually returned a complete answer (the point above). No case failed in either arm. terra never came within 1,400 tokens of its own allowance. *(Measured.)*
- **The scoped per-call ceiling held.** The work order authorized $0.15/call for sol's window calls. The dearest call this study made was **$0.1404**; the gateway would have refused anything above $0.15 before sending. No in-session raise was requested or used, by this arm or any other. *(Measured.)*

## Recommendation

**Close the re-rank question with the accumulated negative evidence. Do not graduate either arm to a freeze order.** The sequencing gate is closed: neither arm moved Hit@1 or MRR beyond its measured floor with paired support, so the work order's rule applies and **$5.98 of the $15 goes back unspent**. Two of the four authorized arms were run; setwise/tournament and batched pointwise were not, because the gate closed before them. *(The gate is measured; stopping is the work order's own rule.)*

**The model-strength hypothesis is closed, and it failed in an informative direction.** A five-times-dearer ranker, given the identical 32 cards in the identical order and answering in RankGPT's own permutation format, ranked the *head* worse: Hit@1 0.393 → 0.321 (−0.071, twice the floor, 0 wins and 2 losses) and MRR 0.501 → 0.450. It fixed **none** of the 17 shown-but-ranked-low misses and broke two cases the baseline had right. Whatever separates the correct teammate from the plausible one in this data, more ranker capacity applied to the same cards does not find it. *(Measured.)*

**But S1 produced the only movement in this study whose interval excludes zero, and it is not on the metric the gate watches.** Fed the same window, sol ordered the *whole list* markedly better while ordering its head worse: Hit@10 0.750 → 0.857 (+0.107, 3 wins and 0 losses) and Recall@10 0.534 → 0.669 (+0.135, 95% CI [+0.029, +0.265]) — more than thirteen times the Recall@10 floor of 0.010. Read plainly: the strong model is better at deciding *who belongs on the list* and worse at deciding *who goes first*. That is a real and slightly surprising finding, and it is genuinely orthogonal to this study's target, because the product question this project has been optimizing is top-1 assignee prediction. It is recorded here rather than acted on: chasing it would be a different question with a different success metric, and it is not this order's to open. *(The numbers are measured; that they belong to a different question is reasoned.)*

**S2's mechanism is real, targeted, and too small to adopt.** The hybrid card did what it was pre-registered to do and did it only where it could: of the 8 misses whose truth person sat inside the detailed head, it fixed **2**; of the 9 whose truth person sat below the head, it fixed **0**. That concentration is what a working mechanism looks like rather than a lucky draw — though on 17 cases the split is not statistically separable on its own (Fisher exact p ≈ 0.21), so it is suggestive, not established. The trouble is the size. Net of the one case it broke, S2 gains exactly **one case**: Hit@1 +0.036, which is *precisely* the measured floor, because the floor was itself measured as one case of 28. MRR +0.015 is well inside its floor, and S2 is directionally **worse** on Hit@5, Hit@10, Recall@5 and Recall@10. A one-case validation gain on a 28-case instrument, bought by doubling the input tokens, is not something to spend this project's last reserved test exposure on. *(The counts and deltas are measured; the judgement not to graduate is reasoned.)*

**A note on S2's rejections, because the headline rate is misleading.** Its validator rejection rate tripled against the baseline (2.05% vs 0.65%), which reads at first like the richer, non-uniform window degrading citation discipline the way the redesign study saw under a reversed window. It is not that. Broken down by class, S2's fabricated-citation count actually *fell* — 4 entries citing evidence that was not the candidate's own, against the baseline's 5 — and one entry named someone not in the window. The increase is almost entirely **duplicate entries**: 30, against the baseline's 6. The model listed the same person twice, most often one of the eight it had just been shown in full detail. So the hybrid view costs answer *hygiene*, not evidence integrity, and every duplicate was discarded rather than repaired. *(Measured; the reading of the cause is reasoned.)*

**Where this leaves the re-rank.** Across this study and the two before it, the re-rank has now been probed on wording (no gain), on presentation order (inside noise on pinned pools), on sampling (permutation self-consistency, hurt), on model strength at the head (hurt, above), and on evidence richness (one case, at the floor). What has *not* moved in any of them is the top-1 decision among near-identical teammates. The consistent reading is that the discriminating signal is not in what the re-rank is shown or in how strong the model reading it is — the cards for two teammates on the same project in the same quarter are close to interchangeable, and the contribution summaries S2 supplied were only enough to separate two cases in eight. If this question is ever reopened, the honest next lever is the *evidence itself* — extraction granularity, or per-person signal that distinguishes collaborators — not another re-ranking method. That is a Part-A question, not a Part-B one. *(The five results are measured; the synthesis is reasoned, and a different reader could weigh S1's list-ordering gain more heavily than I have.)*

## What this study cannot say

- **28 cases, and the decisive movements are single cases.** S2's whole gain is one case net; the floor it is read against is one case of 28. Nothing here can distinguish a small real effect from a draw at that size, which is exactly why the gate is written in terms of the measured floor rather than the sign of the delta.
- **One run per arm.** Neither arm is repeated, so an arm effect and one draw of the model's own sampling variance are not separable — and the floor this is read against was itself measured from a single repeat.
- **Pinning removes retrieval variance, not model variance.** The retrieval is provably identical across the baseline and both arms (28/28 pools and deterministic rankings). The model is called afresh in every arm at temperature 0, with no other determinism guarantee, through a routed endpoint.
- **S1 is not a drop-in re-rank.** It answers with ids and no prose, so it produces no shortlist reasons at all. Its numbers are a ranking-quality measurement; a deployable version would need a reasons pass on top, at roughly terra's $0.029/call on top of sol's $0.113 — which its result does not come close to justifying.
- **Validation only.** No result here has been checked on the v4 test split, which this study never reads. The reserved final exposure is untouched.
- **The target is still assignee prediction.** Ranking the people who did the work first is evidence of relevance, not proof of optimal staffing, and nothing in this report should be read as an employment-decision recommendation.
