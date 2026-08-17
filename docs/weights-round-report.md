# The final weights round — the control's lead, measured to the end at $0

Generated 2026-08-16 on the benchmark v4 **validation** split (28 cases, 54 roles, 2187 scored candidates), manifest `tawos-v1.1-benchmark-v4`. Work order: `docs/work-orders/weights-round.md`.

The deterministic-sweeps study closed both of its levers and left exactly one lead: its G6 *control* — a flat down-weighting of `specialization_match` — moved the offline deterministic arm from 0.143 to 0.214 Hit@1, on a constant fitted to the same 28 cases it was measured on. That is a lead about a **weight**, and this round is the sweep it asked for.

Read the labels: **measured** means the sentence restates a number in this document; **reasoned** means it is a judgement about those numbers, and a different reader could land somewhere else.

The whole of this document cost **$0.00**. The score *components* of every candidate are checkpointed by the sweeps study under pinned intent parses, so any weight vector is re-scored exactly — through the engine's own `query/rank.py:combine_parts`, not a second implementation of it.

## What is being re-scored, and the control that licenses it

Every vector in this round is applied to one checkpoint: the sweeps study's `base` condition — production graph, every improvement flag at its default, replayed from the rerank-redesign pin (`eeb4b76409a3c91b`, parses digest `449457e230310133`). A lever's condition is refused by the loader: re-scoring one would measure the lever and the weights together, which is exactly the confound the rerank-redesign acceptance made a standing rule against.

That checkpoint reproduces the pin it replays, re-verified here rather than taken from the earlier report:

| Check against the source pin | Cases |
|---|---:|
| Candidate pool identical, in the engine's own order | 28 / 28 |
| Deterministic ranking identical (engine scores) | 28 / 28 |
| Deterministic ranking identical (recombined from stored components) | 28 / 28 |
| Re-rank window population identical | 28 / 28 |

The third row is the one this round stands on: every ordering below is re-derived from the stored components through `combine_parts`, so the current weighting and a candidate weighting come out of the same arithmetic. *(Measured.)*

## Tier 0, step 1 — the mechanism: marginal effects, not a leaderboard

Each component's effect is the mean over **every** point of a 216-point coarse grid that holds that component at one weight. This is what a weight decision is read from: a single top row on 28 cases is a coin flip, while a component whose mean metric moves one way across a whole grid is a mechanism. *(The method is benchmark v2's, reused rather than reinvented.)*

| Component | Weight | Grid points | Hit@1 | Hit@5 | MRR | Window hit rate | Window recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| `specialization_match` | 0.00 | 48 | 0.1510 | 0.4256 | 0.2889 | 0.9940 | 0.9612 |
| `specialization_match` | 0.12 | 6 | 0.1250 | 0.4524 | 0.2812 | 0.9821 | 0.9545 |
| `specialization_match` | 0.14 | 10 | 0.1393 | 0.4536 | 0.2921 | 0.9857 | 0.9551 |
| `specialization_match` | 0.17 | 12 | 0.1399 | 0.4673 | 0.2934 | 0.9821 | 0.9493 |
| `specialization_match` | 0.20 | 15 | 0.1381 | 0.4691 | 0.2971 | 0.9810 | 0.9490 |
| `specialization_match` | 0.22 | 6 | 0.1310 | 0.4703 | 0.3036 | 0.9762 | 0.9461 |
| `specialization_match` | 0.25 | 18 | 0.1389 | 0.4881 | 0.2999 | 0.9821 | 0.9504 |
| `specialization_match` | 0.29 | 12 | 0.1220 | 0.4792 | 0.2918 | 0.9792 | 0.9484 |
| `specialization_match` | 0.30 | 6 | 0.1250 | 0.4524 | 0.2984 | 0.9762 | 0.9497 |
| `specialization_match` | 0.33 | 21 | 0.1241 | 0.4762 | 0.2905 | 0.9779 | 0.9478 |
| `specialization_match` | 0.38 | 12 | 0.1161 | 0.4762 | 0.2850 | 0.9762 | 0.9460 |
| `specialization_match` | 0.40 | 9 | 0.1190 | 0.4881 | 0.2834 | 0.9762 | 0.9460 |
| `specialization_match` | 0.43 | 12 | 0.1131 | 0.4762 | 0.2787 | 0.9792 | 0.9492 |
| `specialization_match` | 0.50 | 12 | 0.1101 | 0.4881 | 0.2768 | 0.9732 | 0.9440 |
| `specialization_match` | 0.60 | 5 | 0.1071 | 0.4786 | 0.2725 | 0.9714 | 0.9435 |
| `skill_overlap` | 0.00 | 48 | 0.1280 | 0.4978 | 0.2865 | 0.9836 | 0.9548 |
| `skill_overlap` | 0.12 | 6 | 0.1190 | 0.4643 | 0.2894 | 0.9821 | 0.9530 |
| `skill_overlap` | 0.14 | 10 | 0.1178 | 0.4536 | 0.2833 | 0.9822 | 0.9531 |
| `skill_overlap` | 0.17 | 12 | 0.1160 | 0.4554 | 0.2815 | 0.9851 | 0.9557 |
| `skill_overlap` | 0.20 | 15 | 0.1262 | 0.4476 | 0.2874 | 0.9786 | 0.9483 |
| `skill_overlap` | 0.22 | 6 | 0.1310 | 0.4583 | 0.2982 | 0.9821 | 0.9545 |
| `skill_overlap` | 0.25 | 18 | 0.1329 | 0.4504 | 0.2890 | 0.9802 | 0.9487 |
| `skill_overlap` | 0.29 | 12 | 0.1250 | 0.4613 | 0.2872 | 0.9851 | 0.9547 |
| `skill_overlap` | 0.30 | 6 | 0.1250 | 0.4583 | 0.2942 | 0.9821 | 0.9545 |
| `skill_overlap` | 0.33 | 21 | 0.1344 | 0.4541 | 0.2895 | 0.9796 | 0.9470 |
| `skill_overlap` | 0.38 | 12 | 0.1220 | 0.4643 | 0.2835 | 0.9821 | 0.9514 |
| `skill_overlap` | 0.40 | 9 | 0.1508 | 0.4484 | 0.2967 | 0.9802 | 0.9464 |
| `skill_overlap` | 0.43 | 12 | 0.1280 | 0.4375 | 0.2805 | 0.9792 | 0.9469 |
| `skill_overlap` | 0.50 | 12 | 0.1488 | 0.4494 | 0.2914 | 0.9821 | 0.9474 |
| `skill_overlap` | 0.60 | 5 | 0.1500 | 0.4357 | 0.2885 | 0.9929 | 0.9549 |
| `recency` | 0.00 | 40 | 0.1080 | 0.4161 | 0.2599 | 0.9714 | 0.9331 |
| `recency` | 0.12 | 6 | 0.1131 | 0.4583 | 0.2785 | 0.9643 | 0.9283 |
| `recency` | 0.14 | 10 | 0.1107 | 0.4607 | 0.2733 | 0.9643 | 0.9313 |
| `recency` | 0.17 | 12 | 0.1131 | 0.4673 | 0.2750 | 0.9643 | 0.9317 |
| `recency` | 0.20 | 15 | 0.1214 | 0.4643 | 0.2817 | 0.9691 | 0.9386 |
| `recency` | 0.22 | 6 | 0.1191 | 0.4583 | 0.2886 | 0.9643 | 0.9354 |
| `recency` | 0.25 | 19 | 0.1297 | 0.4831 | 0.2886 | 0.9756 | 0.9459 |
| `recency` | 0.29 | 12 | 0.1220 | 0.4673 | 0.2858 | 0.9911 | 0.9633 |
| `recency` | 0.30 | 6 | 0.1250 | 0.4583 | 0.2994 | 0.9881 | 0.9631 |
| `recency` | 0.33 | 22 | 0.1396 | 0.4757 | 0.2995 | 0.9935 | 0.9669 |
| `recency` | 0.38 | 12 | 0.1339 | 0.4792 | 0.2974 | 1.0000 | 0.9740 |
| `recency` | 0.40 | 10 | 0.1500 | 0.4857 | 0.3037 | 1.0000 | 0.9731 |
| `recency` | 0.43 | 12 | 0.1458 | 0.4643 | 0.3005 | 1.0000 | 0.9737 |
| `recency` | 0.50 | 13 | 0.1648 | 0.4753 | 0.3139 | 1.0000 | 0.9726 |
| `recency` | 0.60 | 6 | 0.1845 | 0.4941 | 0.3273 | 1.0000 | 0.9700 |
| `evidence_strength` | 0.00 | 40 | 0.1643 | 0.5321 | 0.3205 | 0.9929 | 0.9622 |
| `evidence_strength` | 0.12 | 6 | 0.1369 | 0.4822 | 0.3074 | 0.9881 | 0.9640 |
| `evidence_strength` | 0.14 | 10 | 0.1429 | 0.4607 | 0.3045 | 0.9893 | 0.9640 |
| `evidence_strength` | 0.17 | 12 | 0.1429 | 0.4613 | 0.3025 | 0.9851 | 0.9578 |
| `evidence_strength` | 0.20 | 15 | 0.1429 | 0.4476 | 0.2989 | 0.9833 | 0.9555 |
| `evidence_strength` | 0.22 | 6 | 0.1369 | 0.4524 | 0.3058 | 0.9821 | 0.9545 |
| `evidence_strength` | 0.25 | 19 | 0.1335 | 0.4455 | 0.2899 | 0.9812 | 0.9511 |
| `evidence_strength` | 0.29 | 12 | 0.1250 | 0.4494 | 0.2847 | 0.9821 | 0.9519 |
| `evidence_strength` | 0.30 | 6 | 0.1071 | 0.4405 | 0.2805 | 0.9703 | 0.9414 |
| `evidence_strength` | 0.33 | 22 | 0.1152 | 0.4367 | 0.2721 | 0.9773 | 0.9456 |
| `evidence_strength` | 0.38 | 12 | 0.1071 | 0.4494 | 0.2664 | 0.9762 | 0.9432 |
| `evidence_strength` | 0.40 | 10 | 0.1071 | 0.4286 | 0.2614 | 0.9786 | 0.9457 |
| `evidence_strength` | 0.43 | 12 | 0.1071 | 0.4316 | 0.2583 | 0.9732 | 0.9387 |
| `evidence_strength` | 0.50 | 13 | 0.1071 | 0.4313 | 0.2579 | 0.9725 | 0.9366 |
| `evidence_strength` | 0.60 | 6 | 0.1071 | 0.4345 | 0.2583 | 0.9762 | 0.9372 |

Read across the grid on the target metric (`hit_at_1`), the four components point like this:

| Component | Marginal at the lowest weight | at the highest | Span | Direction | Steps agreeing | Steps against |
|---|---:|---:|---:|---|---:|---:|
| `evidence_strength` | 0.1643 | 0.1071 | -0.0572 | down | 6/14 | 2/14 |
| `recency` | 0.1080 | 0.1845 | +0.0765 | up | 9/14 | 5/14 |
| `skill_overlap` | 0.1280 | 0.1500 | +0.0220 | up | 7/14 | 6/14 |
| `specialization_match` | 0.1510 | 0.1071 | -0.0439 | down | 9/14 | 5/14 |

**This reproduces benchmark v2's reading on a different instrument, with the same four mechanisms and the same ordering of their sizes.** `recency` has the largest upward span and lifts the window with it (window recall 0.933 → 0.970 across the grid) — the target is who a ticket was *assigned* to, and assignment follows current ownership of an area. `evidence_strength` has the cleanest downward span (only 2 of 14 steps move against it), because it saturates in the *count* of supporting contributions and so approximates the `most_active` baseline — the weakest of the benchmark's baselines, at v1 test MRR 0.175. `specialization_match` trades the head of the list for coverage: its Hit@1 marginal falls as its weight rises while its Hit@5 marginal climbs, and its window recall falls. `skill_overlap` is the mildest signal and the noisiest — 6 of its 14 steps move against its own span, so it is a direction this grid does not really establish. *(Measured; the mechanisms are the reasoning v2 recorded, restated here because this grid agrees with it.)*

These are means of means over a coarse grid, so they are read as directions and sizes, not as estimates of any particular vector's score. That is the point of reading them at all: the grid's own best row is a fit to 28 cases, while a direction that survives averaging over 216 vectors is a mechanism. *(Reasoned.)*

## Tier 0, step 2 — selection: one step, in the direction the marginals point

The rule is v2's, implemented in `select_candidate` rather than asserted: a component whose marginal points **down** gives up one step of 0.05; the component whose marginal points **up** hardest receives it. Where the marginals support more than one such move, each is measured and the stronger is adopted — so the verdict below is tested against the most favourable defensible retune rather than a convenient one. *(Reasoned; the moves themselves are measured.)*

| Move | Weights | Hit@1 | Δ vs current |
|---|---|---:|---:|
| `specialization_match` → `recency` **(adopted)** | evidence 0.05 / recency 0.45 / skill 0.30 / specialization 0.20 | 0.2143 | +0.0714 |
| `evidence_strength` → `recency` | evidence 0.00 / recency 0.45 / skill 0.30 / specialization 0.25 | 0.1786 | +0.0357 |

Adopted: **evidence 0.05 / recency 0.45 / skill 0.30 / specialization 0.20**, one step out of `specialization_match` into `recency` — which is the direction the G6 control pointed at, arrived at here from the marginals rather than from the control's fitted constant. *(Measured.)*

**What was deliberately not adopted.** The best rows of the fine grid put `specialization_match` at 0.00 and score higher still on Hit@1. A 28-case grid has more than enough freedom to fit noise, and a vector that zeroes a component the union retrieval depends on is a fit, not a mechanism. The adopted vector is the smallest move the marginals support. *(Reasoned — this is the v2 rule, and it is the rule that keeps this study honest when the numbers are tempting.)*

## Tier 0, step 3 — what the adopted vector does to the deterministic arm

| Arm | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Window hit rate | Window recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current weights (evidence 0.05 / recency 0.40 / skill 0.30 / specialization 0.25) | 28 | 0.143 | 0.500 | 0.679 | 0.244 | 0.449 | 0.319 | 0.977 | 1.000 | 0.969 |
| candidate (evidence 0.05 / recency 0.45 / skill 0.30 / specialization 0.20) | 28 | 0.214 | 0.464 | 0.679 | 0.208 | 0.462 | 0.355 | 0.977 | 1.000 | 0.969 |

Paired per case, candidate against current — the same 28 cases, the same pinned pools, no model call anywhere in either arm:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.214 | +0.071 | 2 | 0 | 26 | McNemar exact p = 0.500 |
| Hit@5 | 28 | 0.500 | 0.464 | -0.036 | 0 | 1 | 27 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.679 | 0.679 | +0.000 | 0 | 0 | 28 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.244 | 0.208 | -0.036 | 0 | 1 | 27 | 95% bootstrap CI [-0.107, +0.000] |
| Recall@10 | 28 | 0.449 | 0.462 | +0.013 | 1 | 1 | 26 | 95% bootstrap CI [-0.015, +0.054] |
| MRR | 28 | 0.319 | 0.355 | +0.036 | 4 | 3 | 21 | 95% bootstrap CI [-0.008, +0.098] |
| Candidate recall | 28 | 0.977 | 0.977 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window hit rate | 28 | 1.000 | 1.000 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window recall | 28 | 0.968 | 0.968 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |

**The deterministic ordering does improve.** Hit@1 0.143 → 0.214 (+0.0714, 2 cases, 2 wins and 0 losses) and MRR 0.319 → 0.355 (+0.0362). The Hit@1 movement is about twice the measured v4 floor of 0.0357; the Hit@5 and Recall@5 movements against it (-0.0357 and -0.0357) are inside their own floors (0.0714 and 0.0946). *(Measured.)*

Be exact about what that is worth: this arm has **no model variance at all** — one pinned parse set, recomputed deterministically — so the deltas are exact for these cases and the uncertainty in them is sampling over cases, not run-to-run noise. Two cases of Hit@1 is what it is. *(Measured; the caveat is the sweeps study's own, and it applies here unchanged.)*

## Tier 0, step 4 — is it a plateau or a spike?

Every one of the 81 weight vectors within ±0.05 of the current weighting on each component, scored the same way. An adopted vector should be surrounded by vectors that agree with it; a vector that is good alone is a fit.

| Metric | Points | Worst | Median | Best | Current | Beat current | Tie | Worse |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hit_at_1 | 81 | 0.1429 | 0.1786 | 0.2500 | 0.1429 | 70 | 11 | 0 |
| hit_at_5 | 81 | 0.4643 | 0.5000 | 0.5714 | 0.5000 | 11 | 30 | 40 |
| hit_at_10 | 81 | 0.6071 | 0.6429 | 0.6786 | 0.6786 | 0 | 39 | 42 |
| recall_at_5 | 81 | 0.1890 | 0.2086 | 0.2670 | 0.2437 | 16 | 1 | 64 |
| recall_at_10 | 81 | 0.3952 | 0.4443 | 0.4755 | 0.4494 | 27 | 4 | 50 |
| mrr | 81 | 0.3009 | 0.3339 | 0.3716 | 0.3188 | 69 | 2 | 10 |
| candidate_recall | 81 | 0.9774 | 0.9774 | 0.9774 | 0.9774 | 0 | 81 | 0 |
| window_hit | 81 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 | 81 | 0 |
| window_recall | 81 | 0.9685 | 0.9685 | 0.9685 | 0.9685 | 0 | 81 | 0 |

**On the target metric the neighbourhood is a plateau**: 70 of 81 vectors beat the current weighting, 11 tie, and 0 is worse — the worst vector in the neighbourhood scores 0.1429 against the current 0.1429. *(Measured.)*

**It is not a free plateau, and v2's own robustness test is not met.** Benchmark v2 adopted its vector only after every point in its neighbourhood beat the v1 weighting on each metric it checked (MRR, Hit@10, window recall). That does not hold here: this neighbourhood is below the current weighting on Hit@5 and Recall@5 at most of its points, and never above it on Hit@10. What the retune buys at rank 1 it pays for around rank 5. *(Measured; reading it as a head-of-list trade is reasoned.)*

## Tier 0, step 5 — the window: can any of this reach the full system?

This is the half of the gate that decides the money, and it rests on a finding already on the record: **the re-rank is order-robust on this instrument** (rerank-redesign acceptance; recalibrated by the sweeps study — feeding the same prompt worst-first moved Hit@1 -0.071 on two discordant cases, p = 0.500, which is between one and two times the measured floor rather than comfortably inside it). A re-weighting therefore reaches the shipped ranking mainly by changing **who is in the 32-card window**, not by reordering it.

The window is a real constraint, not a formality: 41 of 54 roles retrieve more people than the window holds (mean pool per role 40.5, largest 56), so there is always someone just outside it who a re-weighting could pull in. *(Measured.)*

And the reordering the retune does cause is nowhere near the reversal that effect was measured at: the first card changes on 7 of 54 roles, and a person who stays in the window moves a mean of 0.847 positions (worst 2.258). *(Measured.)*

### What the adopted vector actually moves

| Case | Project | Window before | after | Entered | Left | Truth entered | Truth left |
|---|---|---:|---:|---:|---:|---:|---:|
| `DRP S19-5` | DM | 49 | 49 | 2 | 2 | 0 | 0 |
| `AP F19-5 (October)` | DM | 51 | 51 | 2 | 2 | 0 | 0 |
| `TSSW Sprint - Oct 28 - Nov 10` | DM | 47 | 46 | 0 | 1 | 0 | 0 |
| `TSSW Sprint - Nov 25 - Dec 6` | DM | 46 | 46 | 2 | 2 | 0 | 0 |
| `TSSW Sprint - Jan 21 - Feb 01` | DM | 49 | 49 | 1 | 1 | 0 | 0 |
| `Resource Mgmt R9 Sprint 37` | MESOS | 32 | 32 | 1 | 1 | 0 | 0 |
| `2019 Sprint 4` | TIMOB | 39 | 40 | 1 | 0 | 0 | 0 |
| `2019 Sprint 5` | TIMOB | 37 | 36 | 0 | 1 | 0 | 0 |
| `2019 Sprint 13` | TIMOB | 39 | 40 | 1 | 0 | 0 | 0 |
| `2019 Sprint 16` | TIMOB | 41 | 41 | 1 | 1 | 0 | 0 |

**10 of 28 cases show a changed window population** — 11 people enter across the split and 11 leave. **0 of them are truth people, and 0 truth people leave.** The window hit rate (1.000) and window recall (0.9685) are identical under both weightings, to four decimals. *(Measured.)*

### The ceiling that is already reached

There are 95 truth people across the split, and the re-rank is already shown 90 of them. The 5 it is not shown sit on 3 cases, and they are the entire population a re-weighting could rescue:

| Case | Truth people | Outside the window |
|---|---:|---|
| `DRP S19-5` | 10 | 3 — `DM:145732`, `DM:145745`, `DM:145787` |
| `Mesos Foundations R9 Sprint 37` | 4 | 1 — `MESOS:3428` |
| `2020 Sprint 4` | 3 | 1 — `TIMOB:166019` |

Window hit rate is already 1.000 — every case has at least one truth person in front of the model — so the only ceiling a retune could raise is window *recall*, and only on these three cases. *(Measured.)*

### Asking the whole grid, not the adopted point

One vector moving no truth people could be luck. So the same two questions are put to every vector in the mechanism direction, and then to the whole simplex — the window is a set, so this is enumeration rather than estimation:

| Question | Mechanism direction | Whole simplex |
|---|---:|---:|
| Weight vectors swept | 270 | 13776 |
| Vectors that move ≥1 truth person **into** the window | 0 | 10355 |
| Most truth people any single vector moves in | 0 | 1 |
| Most of the baseline re-rank's **wrong** rank-1 choices any vector removes | 0 | 0 |
| Most of its **correct** rank-1 choices any vector removes | 0 | 0 |
| Best window recall reachable (now 0.9685) | 0.9685 | 0.9774 |

**In the entire mechanism direction — 270 vectors, every defensible retune of these four weights — not one moves a truth person into the window, and not one removes anybody the paid re-rank ranked first.** *(Measured.)*

**Across the whole simplex** (13776 vectors) the ceiling is barely different: at most 1 truth person can be moved in by any weighting at all, lifting window recall to 0.9774 from 0.9685 — a change of +0.0089, a tenth of the measured Recall@5 floor — and the vector that does it (evidence 0.50 / recency 0.50 / skill 0.00 / specialization 0.00) is not a retune anyone would defend. *(Measured.)*

### The other propagation channel, measured on the paid arm itself

A retune can also force the full system to change its answer by removing the person the model *chose*. That is checkable against the rerank-redesign baseline arm's own records, read-only:

| The baseline arm's rank-1 choice | Cases | Still in the window under the candidate |
|---|---:|---:|
| correct (the arm's Hit@1) | 11 | 11 |
| wrong | 17 | 17 |

**Every one of the 17 people the re-rank wrongly ranked first is still in front of it under the candidate weights, and so is every one of the 11 it got right.** The retune removes none of them. *(Measured.)*

## GATE 1 — to `weights_val` (~$2)

The order opens the paid validation arm only if the candidate **both** improves the deterministic ordering on a plateau **and** changes window membership in the truth-relevant direction on enough cases that propagation is arithmetically possible.

- deterministic Hit@1 0.1429 → 0.2143 (+0.0714) against the measured v4 floor of 0.0357 — clears it
- the plateau holds: of 81 neighbouring vectors, 70 beat the current weighting on hit_at_1, 11 tie and 0 are worse (worst 0.1429)
- the window population moves on 10/28 cases (11 in, 11 out) — but 0 truth people enter it and 0 leave
- across all 270 vectors in the mechanism direction, 0 truth people can be moved into the window and 0 of the baseline re-rank's wrong rank-1 choices can be removed from it
- across all 13776 vectors of the whole simplex the same two maxima are 1 and 0; the best window recall any weighting reaches is 0.9774 against 0.9685 now

**Gate: STOP — no paid arm, and none is needed.**

The first half passes and the second fails, which is the informative combination: the retune is real on the arm it acts on, and that arm is the one the re-rank replaces. *(Reasoned, from the measurements above.)*

## What this settles

**The G6 control's lead was real, and it was a real *deterministic* lead.** Moving one step of weight out of `specialization_match` into `recency` moves the offline arm's Hit@1 0.143 → 0.214 and MRR 0.319 → 0.355, on a plateau rather than a spike, in the direction two independent studies now point. The control was not an artifact. *(Measured.)*

**And it cannot reach the shipped system.** The re-rank sees a 32-card window; under the candidate weights it sees the same truth people on all 28 cases, and the same rank-1 choices it already made. Across all 270 vectors of the mechanism direction that stays true. What changes is which near-boundary non-truth candidates fill the cards, plus a presentation order that moves a card 0.85 positions on average — against a full reversal, which is the only order manipulation this instrument has measured, and which itself did not clear significance. *(Measured; the inference is reasoned.)*

**So a paid arm would measure the model answering the same question twice.** The v4 floor exists precisely because that was measured: one repeat of one arm on byte-identical retrieval moved Hit@1 -0.036 and MRR -0.034 while agreeing on the first-ranked person in 25 of 28 cases. A $2 arm here would return a number inside that band with no mechanism to attribute it to. Spending it would buy noise with a story attached. *(Reasoned.)*

**Recommendation: do not retune the weights, and conclude the research track on the existing v4 baseline.** The current weighting stays as it is: the candidate is better on the deterministic arm and provably neutral on the system that ships, and a config change that cannot be measured end to end is a change made on faith. The second and final planned v4 test exposure stays **unspent**. *(Reasoned.)*

**What would change this reading**, and it is not another weight sweep: the binding constraint is that the truth people are already in the window and the re-rank still ranks them below someone else on 17 of 28 cases. That is a re-rank problem — or a window-width problem on the three cases where truth sits outside — and neither is reachable from `scoring.weights`. *(Reasoned.)*

## Tiers 1 and 2 — not run

Gate 1 stopped the round, so no paid validation arm was run, gate 2 was never reached, and no freeze document was written. The v4 **test split was not read, at any point, by anything in this round** — tier 0 touches one validation-split checkpoint and the validation-split records of one earlier arm.

One implementation note for whoever does open a paid weights arm later, because it is not obvious and it costs money to discover: **a re-weighted arm cannot replay the existing pin.** `data/eval/rerank_redesign/pin/` stores whole candidate profiles only for the *window* — the top 32 under the weights in force when it was captured — and a re-weighted window can contain people outside that set. A weights arm needs the pinned parses replayed against the graph first (the free offline path the sweeps study already has), with a fresh pin written under the candidate weights. The intent parses themselves pin cleanly and cost nothing to reuse. *(Reasoned, from the pin's structure.)*

The order's tier-2 item that outlives this round is the **test-split intent-parse checkpoint**: its absence is what forced the redesign study to pay for a baseline it had assumed was free. Nothing here fixes that, because nothing here runs the test split. It should be carried into whichever order next touches it. *(Reasoned.)*

## Isolation — what this round touched

Tier 0 is arithmetic over files. It opens no Neo4j driver and makes no model call, and that is checkable rather than asserted: the whole round reproduces byte-identically with the graph URI pointed at a dead port and the API keys unset —

```
NEO4J_URI=bolt://127.0.0.1:9 ANTHROPIC_API_KEY= OPENROUTER_API_KEY= \
  uv run python -m capgraph.eval.weights_round --tier0
```

The production graph is therefore untouched by construction, not by restoration. Checkpoint namespaces, by when they were last written:

| Namespace | Last written |
|---|---|
| `data/eval/v1` | — |
| `data/eval/v2` | 2026-08-12T06:27:38+00:00 |
| `data/eval/v3` | 2026-08-12T20:50:10+00:00 |
| `data/eval/v4` | 2026-08-15T19:04:07+00:00 |
| `data/eval/rerank_redesign` | 2026-08-15T23:23:03+00:00 |
| `data/eval/sweeps` | 2026-08-16T01:58:41+00:00 |
| `data/eval/weights` | 2026-08-16T04:42:35+00:00 |

Everything this round produced is under `data/eval/weights/`; every other namespace above was read and not written. *(Measured.)*

## Spend

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `weights_test` | 0 | 0.0000 |
| `weights_val` | 0 | 0.0000 |
| **total** | | **0.0000** |

Reconciled against `data/llm_costs.jsonl` by stage name: **$0.0000** of the $10.00 the owner authorized on 2026-08-15. Both gated stages are empty; the authorization is returned unspent. Every table in this document is arithmetic over checkpoints that already existed. *(Measured.)*

## What this round cannot say

- **28 cases.** One case is 0.036 of Hit@1. The paired win/loss counts beside the tables are more informative than the aggregates.
- **The window arithmetic is exact; the inference from it is not a proof about the model.** What is measured is that the re-rank would be shown the same truth people and the same rank-1 choices. A paid arm could still return a different number, through card order, the printed score, or resampling — the argument is that none of those is a mechanism a weight retune controls, and all of them are inside the measured floor.
- **The scan is a grid, not a continuum.** 13,776 normalized vectors from a 12-level grid per component — 0.05 apart through the region around the current weighting, coarser out at the extremes. A vector between two grid points could in principle behave differently from both, though neither of its neighbours does.
- **The floor it is read against is one repeat of one arm** (sweeps work item 1), model-only variance with retrieval pinned. It is a lower bound on what a full pipeline re-run would move.
- **Validation only.** Nothing here has read the v4 test split.
- **The target is still assignee prediction.** Ranking the people who did the work first is evidence of relevance, not proof of optimal staffing.
