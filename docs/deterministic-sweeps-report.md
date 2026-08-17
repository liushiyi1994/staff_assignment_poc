# Deterministic-side sweeps — benchmark v4's noise floor, and the G3a / G6 levers

Generated 2026-08-16 on the benchmark v4 **validation** split (28 cases, rewritten briefs, `v3frozen` engine), manifest `tawos-v1.1-benchmark-v4`. Work order: `docs/work-orders/deterministic-sweeps.md`.

The re-rank redesign study left the headroom in a known place: on pinned pools the LLM re-rank turns a 0.143 Hit@1 pool into 0.393, so what limits this system now is the deterministic side — retrieval and scoring. This study measures the two wave-1 levers aimed there, and first gives benchmark v4 the noise floor it has never had, so every claim below is read against a gauge measured on *this* instrument rather than the 0.100 borrowed from v1.

Read the labels: **measured** means the sentence restates a number in this document; **reasoned** means it is a judgement about what to do with those numbers, and a different reader could land somewhere else.

Throughout, an *offline arm* is the `capgraph_score` system: the pinned parses, retrieval, and the deterministic weighted score, with no re-rank call. That is the arm these two levers act on, and the arm the rerank-redesign study measured at 0.143 Hit@1 on this split.

## What is pinned, and the control that licenses the rest

Every arm in this study — the noise-floor repeat, all three offline conditions, and any paid arm — replays the **same checkpointed intent parses**, read read-only from `data/eval/rerank_redesign/pin/validation.jsonl`. Intent is brief-level and vocabulary-independent, so it pins cleanly across both levers; what a lever may then move is retrieval and scoring, and nothing else.

The digest below is taken over the rebuilt `RoleSpec` objects rather than over the pin's bytes, so it would also notice a change in how the pin is read. It is recorded in each condition's sidecar at replay time:

| Condition | Graph | Flags | Parses digest |
|---|---|---|---|
| `base` | production | none | `449457e230310133` |
| `g3a_df3` | study | `min_document_frequency = 3` | `449457e230310133` |
| `g6_strength` | production | `enabled = True` | `449457e230310133` |

**All conditions replayed identical parses.**

### The control: does the offline replay reproduce the pin?

The `base` condition replays those parses against the production graph with every flag at its default. If it does not come back with exactly what the pinned run retrieved, no pool diff under a lever is readable — so this is measured rather than assumed:

| Check against the source pin | Cases |
|---|---:|
| Candidate pool identical, in the engine's own order | 28 / 28 |
| Deterministic ranking identical (engine scores) | 28 / 28 |
| Deterministic ranking identical (recombined from stored components) | 28 / 28 |
| Re-rank window population identical | 28 / 28 |

The third row deserves its own line. Every ordering in this study — including the control's — is re-derived from the score *components* the checkpoint stores, which the engine rounds to four decimals, rather than from the candidate's own score. That is deliberate: a transformed arm (the G6 control) and the arm it is read against then come out of the same arithmetic. The row reports what that costs, and on this instrument it costs nothing.

## Work item 1 — benchmark v4's own noise floor

One repeat of the rerank-redesign **baseline arm**, unchanged: the same prompt (`rerank_cards`), the same presentation order (`score`), the same pin, the same 54 re-rank calls over the same 28 cases, temperature 0. The arm is taken from `eval.rerank_redesign.arms` rather than restated, so "identical prompt, identical order" is structural rather than a copied string. Retrieval cannot vary — both runs replay one pin — so **everything in this section is the model answering the same question twice.**

### Per-case agreement

| Measure | Value |
|---|---:|
| Cases compared | 28 |
| Rankings identical end to end | 0 / 28 |
| Same person ranked first | 25 / 28 |
| Same five people in the top 5 (any order) | 10 / 28 |
| Mean top-10 overlap | 0.907 |

### Paired metric deltas — repeat against original

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.393 | 0.357 | -0.036 | 1 | 2 | 25 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.607 | 0.536 | -0.071 | 0 | 2 | 26 | McNemar exact p = 0.500 |
| Hit@10 | 28 | 0.750 | 0.714 | -0.036 | 0 | 1 | 27 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.362 | 0.268 | -0.095 | 1 | 4 | 23 | 95% bootstrap CI [-0.205, -0.005] |
| Recall@10 | 28 | 0.534 | 0.524 | -0.010 | 3 | 3 | 22 | 95% bootstrap CI [-0.062, +0.035] |
| MRR | 28 | 0.501 | 0.467 | -0.034 | 4 | 10 | 14 | 95% bootstrap CI [-0.119, +0.038] |
| Candidate recall | 28 | 0.977 | 0.977 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |

### The floor, stated as a number

| Metric | Movement on a repeat | Read as |
|---|---:|---|
| Hit@1 | -0.0357 | a change smaller than 0.036 on this instrument is not a change |
| Hit@5 | -0.0714 | a change smaller than 0.071 on this instrument is not a change |
| Hit@10 | -0.0357 | a change smaller than 0.036 on this instrument is not a change |
| Recall@5 | -0.0946 | a change smaller than 0.095 on this instrument is not a change |
| Recall@10 | -0.0098 | a change smaller than 0.010 on this instrument is not a change |
| MRR | -0.0341 | a change smaller than 0.034 on this instrument is not a change |

**The measured v4 floor is Hit@1 0.0357 and MRR 0.0341**, with the largest movement across all six metrics at 0.0946 (Recall@5). Every claim in the rest of this document is read against those numbers rather than against v1's 0.100. *(Measured.)*

**It is not one number, and that matters.** On Hit@1 this instrument is tighter than the borrowed gauge — 0.0357 against 0.100, which is 1 case of 28 — while Recall@5 moves 0.0946 on the same repeat. A single floor quoted across all metrics would have been too loose for Hit@1 and too tight for Recall@5, so each metric is compared against its own row above. *(Measured; the per-metric treatment is reasoned.)*

Note what the agreement table above says alongside this: **no case produced the same ranking twice**, yet 25 of 28 put the same person first. The instability is real but concentrated below the head of the list, which is why Hit@1 has the tightest floor of the three Hit metrics and Recall@5 the loosest. *(Measured.)*

### Rejection accounting — the citation guard, measured on both runs

The evidence validator in `query/rank.py` is untouched by this study, so this is the same accounting the rerank-redesign report published, and a rejected entry is still discarded rather than repaired:

| Run | Cases | Entries offered | Accepted | Rejected | Rate | Reason classes |
|---|---:|---:|---:|---:|---:|---|
| original baseline arm | 28 | 1687 | 1676 | 11 | 0.0065 | cites evidence not in this person's contributions, duplicate entry |
| the repeat | 28 | 1686 | 1676 | 10 | 0.0059 | cites evidence not in this person's contributions, duplicate entry |

Cost of the repeat: **$1.5555** under stage `noise_floor`.

### What the floor does to claims already on the record

A floor is only worth measuring if the claims it governs get restated against it. These are recomputed here from the rerank-redesign study's own checkpoints — same 28 cases, same pinned pools — rather than transcribed from its report, so a wrong transcription could not survive:

| Claim | Hit@1 | MRR | Against this floor |
|---|---:|---:|---|
| the LLM re-rank over the deterministic arm, same pinned pool | +0.250 | +0.182 | **survives** — more than twice the floor |
| presentation order: the same prompt fed worst-first | -0.071 | -0.051 | **marginal** — between one and two times the floor |

The re-rank's premium over the pool it ranks is comfortably outside a floor measured on this instrument, so the rerank-redesign study's central finding stands on a gauge of its own rather than a borrowed one. *(Measured.)*
The position effect it reported as "inside noise" against v1's borrowed 0.100 is **not** inside the measured floor — it sits between one and two times it. That does not overturn the finding (the paired test on it was p = 0.500, on two discordant cases) but it does mean the "inside noise" phrasing was luckier than it was rigorous, and a position control remains worth carrying on any future re-rank arm. *(Measured; the reading is reasoned.)*

**What this floor is, and what it is not.** It is *model-only* variance with retrieval held byte-identical: one re-sample of one arm. It is therefore a **lower bound** on what a whole pipeline re-run would move — the run-to-run floor quoted throughout v1-v3 also contained a fresh intent parse and a fresh retrieval draw, which this deliberately removes. A deterministic lever measured offline on pinned pools has no model variance at all, so for those arms this floor is a conservative gauge rather than the matching one; the paired win/loss counts beside every table are the more informative reading. *(Measured; how to read it across arm types is reasoned.)*

**Every metric moved down, and that is worth naming rather than averaging away.** Symmetric sampling noise would be expected to scatter the signs. These six metrics are not six independent draws — they are computed from the same rankings on the same 28 cases and are strongly correlated, so this is not the coin-flip coincidence it looks like at first. But it is more consistent with a small **systematic** shift between the two runs than with a symmetric interval around zero, which is a second reason to read the numbers above as "how far a re-run can land from here" rather than as a ± band. *(Measured; the interpretation is reasoned, and this study is not built to separate a systematic shift from one unlucky draw — that would need a third run, which was not authorized and is not worth $1.55 to settle.)*

**And it includes provider drift, deliberately.** The two runs are about 3.6 hours apart on a *routed* endpoint (OpenRouter), so anything that changed provider-side in that window is inside this number rather than excluded from it. For the question a floor is actually asked — "would this delta survive being re-run?" — that is the right thing to include, but it is named here rather than left implied, because it means this is a floor for *re-running the study later*, not a within-session sampling interval. *(Measured; the framing is reasoned.)*

The cases whose first-ranked person changed between the two runs: `Week ending 2019-Feb-22`, `Studio 4: RI-22 63`, `2020 Sprint 4`.

## Work item 2 — G3a, vocabulary frequency gating (df floor 3)

### Tier 1, offline, $0

The Stage 3 vocabulary was rebuilt with `improvements.vocabulary.min_document_frequency: 3` into a study namespace (`data/eval/sweeps/study_artifacts/`), projected through Stage 4, and loaded into an **isolated second Neo4j database** — the production graph is never written to by this study (see the isolation section below). The contribution embeddings are deliberately the production cache: Stage 3 rewrites term names and never contribution summaries, so the vector arm is identical across the two vocabularies and whatever G3a moves, it does not move through the vector arm.

| Vocabulary | Canonicals, floor off | Aliases | Canonicals, df floor 3 | Aliases |
|---|---:|---:|---:|---:|
| skill | 10630 | 7108 | 1755 | 15983 |
| specialization | 344 | 2147 | 173 | 2318 |

The gate demoted 8875 skill and 171 specialization canonicals to aliases of their nearest surviving canonical. Nothing is deleted: every raw term still resolves, so no evidence is lost — what changes is *which canonical* a brief's terms resolve onto, and therefore who the structured arm can reach.

| Study graph (isolated) | Count |
|---|---:|
| Person | 316 |
| Contribution | 2666 |
| Skill | 1755 |
| Specialization | 173 |
| HAS_SKILL | 14848 |
| HAS_SPECIALIZATION | 2296 |
| DEMONSTRATES | 25334 |

### What it did to retrieval

| Arm | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Window hit rate | Window recall | Mean pool | Mean window |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| flags off, production graph (the control) | 28 | 0.143 | 0.500 | 0.679 | 0.244 | 0.449 | 0.319 | 0.977 | 1.000 | 0.968 | 51.8 | 42.6 |
| G3a: vocabulary document-frequency floor 3 | 28 | 0.071 | 0.500 | 0.679 | 0.223 | 0.456 | 0.254 | 0.985 | 0.964 | 0.940 | 55.1 | 42.7 |

Both rows are the **deterministic arm** — the same pinned parses, scored and ordered with no LLM re-rank — so the difference is retrieval and scoring alone. `Window hit rate` is the share of cases where *any* truth person reaches the 32-card re-rank window (the ceiling on the full system's Hit@K); `Window recall` is the share of a case's truth people who reach it. On v1-v3 those were the same number because truth was one person. Here they are not, and conflating them is the easiest way to overstate a retrieval lever.

### The pool diff — this *is* the lever's retrieval effect

| Case | Project | Pool before | Pool after | Gained | Lost | Truth gained | Truth lost |
|---|---|---:|---:|---:|---:|---:|---:|
| `TSSW Sprint - Jan 21 - Feb 01` | DM | 58 | 67 | 16 | 7 | 0 | 0 |
| `TSSW Sprint - Jul 20 - Aug 3` | DM | 52 | 54 | 10 | 8 | 0 | 0 |
| `DRP S19-5` | DM | 57 | 63 | 12 | 6 | 2 | 0 |
| `TSSW Sprint - Apr 29 - May 11` | DM | 47 | 63 | 16 | 0 | 0 | 0 |
| `TSSW Sprint - Mar 04 - Mar 16` | DM | 48 | 60 | 13 | 1 | 0 | 0 |
| `TSSW Sprint - Sep 2 - Sep 14` | DM | 50 | 58 | 11 | 3 | 0 | 0 |
| `Arch 2019-01-07` | DM | 70 | 69 | 6 | 7 | 0 | 0 |
| `TSSW Sprint - Nov 25 - Dec 6` | DM | 67 | 67 | 6 | 6 | 0 | 0 |
| `Arch 2019-03-18` | DM | 71 | 70 | 5 | 6 | 0 | 0 |
| `TSSW Sprint - Dec 23 - Jan 3` | DM | 59 | 54 | 3 | 8 | 0 | 0 |
| `TSSW Sprint - Aug 5 - Aug 17` | DM | 55 | 65 | 10 | 0 | 0 | 0 |
| `AP F19-5 (October)` | DM | 62 | 58 | 3 | 7 | 0 | 0 |

(12 largest moves shown.) **27 of 28 cases have a different pool**; 174 candidate slots gained and 80 lost across the split, of which 2 gained and 0 lost were truth people.

28 of 28 cases show a **changed window population** (144 people enter the window across the split, 2 of them truth people; 1 truth person leaves it).

And the diff lands exactly where the mechanism says it should. Counting every (case, role, candidate) slot by which arm found it:

| Candidate slots | Flags off | df floor 3 | Δ |
|---|---:|---:|---:|
| found by the vector arm | 1060 | 1060 | +0 |
| found by the structured arm | 1640 | 2001 | +361 |
| found by the lexical arm | 540 | 540 | +0 |
| found by the structured arm *alone* | 893 | 1121 | +228 |
| candidate slots in total | 2187 | 2415 | +228 |

The vector and lexical columns are **identical**, which is the design working: the study graph shares the production embedding cache, and the lexical arm reads no graph at all. So the whole of G3a's retrieval effect is the structured arm reaching more people — a brief's term now resolves onto a canonical that absorbed several thinner ones, and everyone who held any of them now matches.

### Paired per-case statistics, G3a against the flags-off control

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.071 | -0.071 | 1 | 3 | 24 | McNemar exact p = 0.625 |
| Hit@5 | 28 | 0.500 | 0.500 | +0.000 | 2 | 2 | 24 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.679 | 0.679 | +0.000 | 2 | 2 | 24 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.244 | 0.223 | -0.020 | 2 | 6 | 20 | 95% bootstrap CI [-0.065, +0.026] |
| Recall@10 | 28 | 0.449 | 0.456 | +0.007 | 3 | 5 | 20 | 95% bootstrap CI [-0.083, +0.110] |
| MRR | 28 | 0.319 | 0.254 | -0.064 | 7 | 13 | 8 | 95% bootstrap CI [-0.171, +0.027] |
| Candidate recall | 28 | 0.977 | 0.985 | +0.007 | 1 | 0 | 27 | 95% bootstrap CI [+0.000, +0.021] |
| Window hit rate | 28 | 1.000 | 0.964 | -0.036 | 0 | 1 | 27 | 95% bootstrap CI [-0.107, +0.000] |
| Window recall | 28 | 0.968 | 0.940 | -0.029 | 1 | 1 | 26 | 95% bootstrap CI [-0.107, +0.021] |

### The tier-2 gate

The order opens the paid arm only if tier 1 shows **no recall regression** and either a deterministic-arm improvement past the measured floor or a materially changed window population.

- the recall guard FAILS: candidate recall +0.0071 and window hit rate -0.0357 — a pool that gains a truth person the re-rank is never shown is not a recall gain the full system can use
- deterministic arm Hit@1 -0.0714 against the measured v4 floor of 0.0357 — does not clear it
- 28/28 cases show a changed window population — materially changed

**Gate: STOP — no paid arm.**

## Work item 3 — G6, primary/secondary specialization strength

### Tier 1, offline, $0 — and judged against the control, not the current weights

G6 is scoring-only: a matched specialization earns credit in proportion to how much of its supporting evidence called the term *primary* rather than *secondary*. Retrieval cannot move, and the pool diff confirms it — 0 of 28 cases have a different pool, which is the arithmetic answer.

Wave 1's finding was that a **constant-scale control** — the same average credit for everyone — reproduced almost all of the person-varying arm's movement, so the gain was the component being down-weighted rather than the strength label separating anyone. That control is reproduced here on this instrument, and the constant is measured from the lever's own output rather than borrowed: across 1362 credited specialization matches the scale G6 applies runs 0.500 to 1.000 with a mean of **0.7163**, and the control gives every candidate exactly that.

| Arm | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Window hit rate | Window recall | Mean pool | Mean window |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| flags off, production graph (the control) | 28 | 0.143 | 0.500 | 0.679 | 0.244 | 0.449 | 0.319 | 0.977 | 1.000 | 0.968 | 51.8 | 42.6 |
| G6: strength-weighted specialization match | 28 | 0.179 | 0.429 | 0.679 | 0.194 | 0.447 | 0.318 | 0.977 | 1.000 | 0.968 | 51.8 | 42.8 |
| G6 control: constant scale 0.7163 for everyone | 28 | 0.214 | 0.464 | 0.679 | 0.208 | 0.462 | 0.356 | 0.977 | 1.000 | 0.968 | 51.8 | 42.6 |

### Paired per-case statistics

**G6 against the flags-off control** — this is the comparison that would read as "G6 improves the score":

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.179 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.500 | 0.429 | -0.071 | 0 | 2 | 26 | McNemar exact p = 0.500 |
| Hit@10 | 28 | 0.679 | 0.679 | +0.000 | 0 | 0 | 28 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.244 | 0.194 | -0.050 | 0 | 3 | 25 | 95% bootstrap CI [-0.130, +0.000] |
| Recall@10 | 28 | 0.449 | 0.447 | -0.002 | 2 | 2 | 24 | 95% bootstrap CI [-0.037, +0.030] |
| MRR | 28 | 0.319 | 0.318 | -0.001 | 6 | 10 | 12 | 95% bootstrap CI [-0.088, +0.077] |
| Candidate recall | 28 | 0.977 | 0.977 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window hit rate | 28 | 1.000 | 1.000 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window recall | 28 | 0.968 | 0.968 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |

**The constant-scale control against the same baseline** — the same down-weighting, with the label's information removed:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.143 | 0.214 | +0.071 | 2 | 0 | 26 | McNemar exact p = 0.500 |
| Hit@5 | 28 | 0.500 | 0.464 | -0.036 | 0 | 1 | 27 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.679 | 0.679 | +0.000 | 0 | 0 | 28 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.244 | 0.208 | -0.036 | 0 | 1 | 27 | 95% bootstrap CI [-0.107, +0.000] |
| Recall@10 | 28 | 0.449 | 0.462 | +0.013 | 1 | 1 | 26 | 95% bootstrap CI [-0.015, +0.054] |
| MRR | 28 | 0.319 | 0.356 | +0.037 | 5 | 3 | 20 | 95% bootstrap CI [-0.008, +0.098] |
| Candidate recall | 28 | 0.977 | 0.977 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window hit rate | 28 | 1.000 | 1.000 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window recall | 28 | 0.968 | 0.968 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |

**G6 against its control** — what the *label* adds once the down-weighting is held constant. This is the row the wave-1 acceptance said any G6 sweep had to be read from:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.214 | 0.179 | -0.036 | 1 | 2 | 25 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.464 | 0.429 | -0.036 | 0 | 1 | 27 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.679 | 0.679 | +0.000 | 0 | 0 | 28 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.208 | 0.194 | -0.014 | 0 | 2 | 26 | 95% bootstrap CI [-0.037, +0.000] |
| Recall@10 | 28 | 0.462 | 0.447 | -0.015 | 2 | 2 | 24 | 95% bootstrap CI [-0.065, +0.027] |
| MRR | 28 | 0.356 | 0.318 | -0.038 | 4 | 9 | 15 | 95% bootstrap CI [-0.129, +0.036] |
| Candidate recall | 28 | 0.977 | 0.977 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window hit rate | 28 | 1.000 | 1.000 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |
| Window recall | 28 | 0.968 | 0.968 | +0.000 | 0 | 0 | 28 | 95% bootstrap CI [+0.000, +0.000] |

### Does it change who reaches the window?

19 of 28 cases show a **changed window population** (21 people enter the window across the split, 0 of them truth people; 0 truth people leave it).

### The tier-2 gate

The order opens the paid arm only if tier 1 beats the constant-scale control **beyond the measured floor** *and* changes who reaches the window. Both halves matter: beating the current weights is not evidence for the label, and a lever that reorders the window without changing its population cannot change what a paid re-rank arm would be shown.

- against the constant-scale control (scale 0.7163): Hit@1 -0.0357, MRR -0.0380 — does not beat the control at all; both deltas are negative, and their magnitudes are inside the measured v4 floor either way
- 19/28 cases show a changed window population — the window moves, but truth-neutrally: no truth person enters or leaves it

**Gate: STOP — no paid arm; the offline result is the full result.**

## Recommendation per lever

| Lever | Tier-1 gate | Paid arm | Recommendation |
|---|---|---|---|
| G3a | stop | not run | **close** |
| G6 | stop | not run | **close** |

Both recommendations are stated against the measured v4 floor (Hit@1 0.0357, MRR 0.0341), measured in work item 1 of this study — not against the 0.100 borrowed from the v1 instrument.

### G3a — document-frequency floor 3: **close**

**It is not cosmetic, and that is the first thing to say.** The gate takes the skill vocabulary from 10630 canonicals to 1755 and moves the candidate pool on 27 of 28 cases and the re-rank window population on 28 of 28. The backlog's own success test — "a smaller vocabulary that does not improve retrieval is cosmetic" — is answered: it changes retrieval a great deal. *(Measured.)*

**Every directional signal it produces points the wrong way at the head of the list.** On the deterministic arm, Hit@1 falls 0.143 → 0.071 (-0.071) and MRR 0.319 → 0.254 (-0.064). Be exact about what that is worth: these are offline arms with **no model variance at all** — the same pinned parses, recomputed deterministically — so the movement is exact for these cases, but it is 2 cases of Hit@1 at a sampling grain of 0.036 (one case in 28). The claim these numbers support is "G3a does not improve the deterministic ranking", which is what the gate needed to know; the claim they do **not** support is a precise size for the harm. *(Measured; the distinction is reasoned.)*

**The mechanism is visible in the pool sizes, and it is the gate's own logic turned against it.** Folding a thin canonical into its nearest surviving canonical means a brief's term now resolves onto a term that many more people hold, so the structured arm matches more people less specifically: the mean pool grows 51.8 → 55.1. Candidate recall does rise (0.977 → 0.985; 2 truth people entered the pool, on 1 case), and Recall@10 rises with it — the gate genuinely finds people the ungated vocabulary missed. But it buys that in the tail and pays for it at rank 1. *(Measured.)*

**The gate stops it on the recall guard, and the guard is doing real work here, not tripping on a technicality.** Candidate recall improved, but *window* hit rate fell 1.000 → 0.964: on `TSSW Sprint - Feb 04 - Feb 16` a truth person who reached the 32-card window under the ungated vocabulary no longer does, because the enlarged pool pushed them out of it. A pool that contains the right person but no longer shows them to the re-rank is not a recall improvement the full system can use. *(Measured.)*

**Recommendation: close the df-floor-3 form as a ranking lever; do not spend a paid arm on it.** (Reasoned.) The flag stays in the codebase and stays off. What would change this reading is a different *motivation*: the vocabulary size is also a prompt-size and term-review cost, and if that cost is ever the reason to gate, the price is now known and quotable rather than hypothetical — -0.071 Hit@1 on the deterministic arm. What this study did **not** measure, and nobody should assume from it, is a lower floor (df 2), a specialization-only floor, or the same floor with a wider re-rank window; those are untested, not rejected. *(Reasoned.)*

### G6 — strength-weighted specialization match: **close**

**Read against the current weights, G6 looks like a small win.** Hit@1 0.143 → 0.179 (+0.036), MRR 0.319 → 0.318 (-0.001). Wave 1 predicted exactly this reading and warned against it. *(Measured.)*

**Read against its control, the label adds nothing — and on these cases takes a little back.** Giving *every* candidate the same average credit (0.7163, measured from G6's own output across 1362 credited matches) scores Hit@1 0.214 and MRR 0.356 — **better than the person-varying arm on both** (-0.036 Hit@1 and -0.038 MRR for G6 against the control). At this sample size 1 case of Hit@1 is not a measurement of how much worse the label is; what it is, is the absence of any evidence that the label helps — the arm that was supposed to earn its keep by separating specialists from dabblers cannot beat a constant. *(Measured; the reading is reasoned.)*

**This is wave 1's finding, confirmed and sharpened on a better instrument.** Wave 1 measured the control reproducing the person-varying arm *almost* exactly (MRR 0.293 against 0.296) on 30 v1-manifest cases with a person-level stand-in for the primary share. Here the share is the engine's own per-edge `primary_evidence_count`, the pools are pinned, and the two arms separate: the control is ahead. *(Measured.)*

**The window half of the gate passes, and it is weaker than it looks.** 19 of 28 cases show a changed window population, so G6 genuinely moves who the re-rank would be shown — but **no truth person enters or leaves the window on any case**. It reshuffles which non-truth candidates fill the 32 cards. That is movement a paid arm could in principle exploit (the re-rank might rank the survivors differently), but it is not the mechanism the lever was proposed on, which was surfacing specialists the flat match was burying. *(Measured; the reading is reasoned.)*

**Recommendation: close G6.** (Reasoned.) The mechanism is the one the PRD asks for and the label has genuine spread in the data — 58% of specialization edges were never once called primary — but two independent measurements now agree that the spread does not separate the right people. The flag stays off and the backlog item can be closed rather than carried.

**One thing worth carrying forward, and it is not G6.** The *control* is the arm that improved: scaling `specialization_match` by a constant 0.7163 for everyone moves the deterministic arm Hit@1 0.143 → 0.214 and MRR 0.319 → 0.356 (+0.037). That is close to — though not exactly — lowering the component's weight, since `combine_parts` renormalizes over the components present and scaling a *value* and scaling a *weight* have different denominators. Benchmark v2's sweep already moved this weight from 0.40 to 0.25 for the same reason, and this says it may want to go lower still. **That is a weight question for the freeze order to sweep properly on the full system, not a result to adopt from this table** — it is 2 cases of Hit@1 on 28, offline, and the deterministic arm is not the shipped ranking. *(The numbers are measured; treating them as a weight lead rather than a weight decision is reasoned.)*

### What this leaves for the freeze order

Neither lever should be bundled into the config freeze. (Reasoned.) The wave-1 deterministic-side shortlist is now empty: G5 was closed on the corpus having no confidence spread, G3a and G6 are closed here on measurement, and G7 was corrected by the rerank-redesign study. The one live lead this study produced is a *weight*, not a flag — see the last paragraph of G6 — and it belongs to a sweep, not to an adoption.

## Graph hygiene — the production graph was never in a study state

The work order's item 4 allows a cleaner isolation than rebuild-and-restore, and one was proposed and approved on 2026-08-15 (recorded in the order): the gated vocabulary lives in a **throwaway second Neo4j container** (`capgraph-sweeps-neo4j`, bolt `7688`, its own volume `capgraph_sweeps_neo4j_data`), and the study's driver is pointed at it by URI. The production graph at `bolt://localhost:7687` is therefore never written to at all — its counts below are a **no-change observation at both ends**, not a restoration check.

| When | Person | Contribution | Skill | HAS_SKILL | HAS_SPECIALIZATION | Matches the order |
|---|---:|---:|---:|---:|---:|---|
| before | 316 | 2666 | 10630 | 17589 | 2361 | yes |
| after | 316 | 2666 | 10630 | 17589 | 2361 | yes |
| after (study container removed) | 316 | 2666 | 10630 | 17589 | 2361 | yes |

The study graph, for contrast — same people and contributions, a smaller vocabulary: 316 Person, 2666 Contribution, 1755 Skill, 14848 HAS_SKILL, 2296 HAS_SPECIALIZATION.

Frozen namespaces (`data/eval/v1`–`v4`, `data/eval/rerank_redesign/`) were read and never written: everything this study produced is under `data/eval/sweeps/`. The study container and its volume are removed at study end.

## What this study cannot say

- **28 cases.** One case is 0.036 of Hit@1. The paired win/loss counts are more informative than the aggregates, and every table above carries them.
- **The floor is one repeat of one arm.** It measures model variance with retrieval pinned; it is not a full pipeline re-run, and it is not a distribution. A second repeat could land elsewhere.
- **The offline arms have no model variance at all.** They re-use one pinned parse set and recompute retrieval and scoring deterministically, so their deltas are exact for these cases — the uncertainty in them is sampling over cases, not run-to-run noise.
- **The G6 control's constant is measured on this split, not pre-registered.** It is the mean credit G6 itself hands out over these 28 cases, which is the right constant for isolating *this* arm's down-weighting but is not a number carried in from anywhere. A different split would give a slightly different one, and the control arm would move with it.
- **Deterministic-arm movement is not full-system movement.** A lever that improves the score-only ranking still has to survive the re-rank, which is why the order gates a paid arm on the offline result instead of inferring one.
- **Validation only.** Nothing here has touched the v4 test split, and this study never reads it. Flipping any default is the freeze order's decision.
- **The target is still assignee prediction.** Ranking the people who did the work first is evidence of relevance, not proof of optimal staffing.

## Spend

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `noise_floor` | 54 | 1.5555 |
| `sweep_val` | 0 | 0.0000 |
| **total** | | **1.5555** |

| Call type | Calls | Cost (USD) |
|---|---:|---:|
| `rerank` | 54 | 1.5555 |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included: **$1.5555** of the $8.00 the owner authorized on 2026-08-15 across the two stages. No in-session raise was requested or granted. Every offline measurement in this document — both tier-1 sweeps, the study vocabulary, the study graph, and every table computed from them — made no model call and cost $0.
