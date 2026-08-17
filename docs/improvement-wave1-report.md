# Improvement wave 1 — measurements and recommendations

- Date: 2026-08-14
- Work order: `docs/work-orders/improvement-wave1.md` (backlog G1, G3a, G5, G6, G7, G8, G11a, G13)
- Branch: `agent/improvement-wave1`
- Spend: **$0.9454**, all of it the authorized G7 probe (ceiling $2.00, stage
  `probe_order`). Every other measurement here is offline and made no model call.

Reproduce every table below with:

```
make wave1              # offline, $0 — G1 needs MYSQL_URL set
make wave1-probe-report # offline, $0 — re-reads the probe's checkpoint
```

## How to read this

Each recommendation is labelled **measured** or **reasoned**. Measured means the
sentence is a restatement of a number in this document. Reasoned means it is a
judgement about what to do with those numbers, and a different reader could land
somewhere else.

Two yardsticks appear throughout, and they are not the same thing:

- **The 0.100 run-to-run floor.** What re-running the whole pipeline unchanged moves a
  metric by, measured twice in `docs/eval-results.md` — once by re-running one
  configuration, once from the deterministic arm across the v3 arms. A change smaller
  than this would not survive being re-run.
- **The 30-case sampling grain.** One validation case is 0.033 of Hit@K. An offline
  re-score re-uses one checkpoint and therefore has *no* run-to-run variance at all, so
  its deltas can be read down to this grain — but they are still 30 cases.

And the standing constraint: **the 120-case test split is retired** (`docs/agent-handoff.md`).
No proposed improvement in this document is validated, and every change with a ranking
effect is behind a flag that is off and stays off until benchmark v4 (backlog G12) can
measure it. The one result here that *is* a live measurement is G7 — and it is an
ablation of what the system already does, not a proposal, which is why the retired split
is not needed to read it.

## What shipped

| Backlog | Change | Default | Where |
|---|---|---|---|
| G1 | Sentence-boundary truncation in Stage 0 | **on** (correct by construction) | `pipeline/stage0_load.py: truncate_at_boundary` |
| G3a | Document-frequency floor on the Stage 3 vocabulary | off (`0`) | `pipeline/stage3_normalize.py: apply_frequency_gate` |
| G5 | `confidence` as a score component or an `evidence_strength` multiplier | off | `query/rank.py: _apply_improvement_components` |
| G6 | `primary_evidence_count` on the projection edge; strength-weighted specialization match | off | `pipeline/stage4_project.py`, `query/rank.py: _satisfied` |
| G7 | Reverse presentation order for the re-rank window | off | `query/rank.py: sample_orders` |
| G8 | Top-`count` marked as the proposed set, the rest as alternates | **on** (surfacing only) | `query/rank.py: split_by_count`, `models.ShortlistResult` |
| G11a | Activity-currency component from the person's last activity | off | `query/retrieve.py: PERSON_ACTIVITY`, `query/rank.py` |
| G13 | `httpx` declared as a direct dependency; stale pitch figure corrected | n/a | `pyproject.toml`, `uv.lock`, `docs/manager-pitch.md` |

Every flag lives in one `improvements` block in `config/settings.yaml`, is read in exactly
one place (`src/capgraph/improvements.py`), and names its backlog ID in a comment beside it.

Two defaults did change, both because the order allows a change that is correct by
construction. G1's truncation is strictly better than a mid-word slice and **extraction
was not re-run**, so no current artifact is affected by it. G8 surfaces a field the
intent parser has always produced and changes no score, no ordering, and no retrieval.

## Acceptance — with the flags off, nothing moved

The three deterministic baselines were **re-run**, not re-read, over the 30 validation
cases and diffed against the frozen v3 run element by element:

| Check | Result |
|---|---|
| Baselines re-run | `bm25`, `vector_only`, `most_active` on the validation split |
| Records compared (ranking *and* candidate pool) | 90 |
| Byte-identical to `data/eval/v3/runs/ab_window32/` | **yes** |
| Configuration digest now | `1b74f4a2022b5cd7` |
| Digest recorded in the frozen checkpoint | `1b74f4a2022b5cd7` |
| Improvement flags on | none — every flag is at its default |

The digest row is the load-bearing one. `run_config()` folds the improvement block in
**only when a flag is on**, so a default run hashes to exactly what it hashed to before
this branch existed, and every frozen v1/v2/v3 checkpoint stays readable and extendable.
A run made with a flag on hashes differently and is refused against them, which is what
the digest is for. `data/wave1/parity.json` holds the check; `tests/test_improvements.py`
pins it as well, so a future flag cannot quietly leak into the digest.

One artifact did change on disk: `data/contributions/capabilities.jsonl` gained
`primary_evidence_count` for G6. All 19,950 rows were compared field by field against the
previous file — one new key, **zero** pre-existing values changed — and Stage 5 was
re-loaded so the property reaches the graph (all 2,361 `HAS_SPECIALIZATION` edges carry
it, all 17,589 `HAS_SKILL` edges carry 0). Node and relationship counts are unchanged.

---

## G1 — description truncation

Measured against MySQL, not the parquet export: the export is already truncated and can
only tell you how many strings landed exactly on 1,200 characters. The source text is
markup-stripped exactly as Stage 0 strips it, with the budget lifted.

| Measure | Value |
|---|---:|
| Descriptions with text | 73,127 |
| Cleaned length (chars): p50 / p90 / p95 / p99 / max | 272 / 847 / 1,219 / 3,002 / 571,666 |
| Over the 1,200-char budget | **3,773 (5.2%)** |
| Share of those descriptions' characters kept | **35.6%** |
| Cuts the old blind slice made mid-word | 2,851 (75.6% of the truncated set) |
| Cuts the new rule takes at a sentence end | 2,655 |
| Cuts the new rule moves back to a word end | 1,001 |
| Cuts the new rule leaves where they were | 117 (13 of them still mid-word) |
| Median characters given up to reach a boundary | 50 |
| Majority log/boilerplate descriptions (>50% of characters) | 5,967 (8.2%) |
| ... of the over-budget set | **773 (20.5%)** |
| Majority non-prose once `{code}` bodies are counted too | 7,055 (9.7%); over budget **1,108 (29.4%)** |
| Mean share of a description that is log/boilerplate | 9.8% |

**A correction to the backlog's premise.** The backlog records that fenced code is
already excluded because Stage 0 prefers TAWOS's `Description_Text` over the raw
`Description`. That is true of the separate `Description_Code` column, but not of inline
blocks: **1,679 descriptions (2.3%) still carry a `{code}`/`{noformat}` block inside
`Description_Text`**, and `strip_markup` removes the macro *delimiters* while keeping the
body. So some code does reach extraction. (Measured.)

**A data-provenance note.** TAWOS stores `Description_Text` with the original line breaks
already replaced by runs of spaces — not one row in the configured slice contains a
newline. The noise measurement recovers line structure by splitting on runs of two or
more spaces, and classifies a recovered line as machine output if it matches a stack
frame / timestamped log / log level / table rule / shell prompt / `key: value` pattern,
or (for lines of 40+ characters) if fewer than 75% of its characters are letters or
spaces. That is a heuristic and it under-counts: source-code lines and logcat column
fragments read as prose to it. **The noise figures are a lower bound.** (Measured, with
the method stated so a reader can discount it.)

**Recommendation.**

1. The sentence-boundary fix has shipped and is on. It is strictly better than the old
   slice — it removes 2,838 of 2,851 mid-word cuts for a median of 50 characters — and
   costs nothing. Extraction was **not** re-run, so no current artifact changes.
   (Measured.)
2. **Do not close G1 as not-worth-fixing, and do not re-run extraction for it either.**
   5.2% sounds ignorable, but it is not the interesting number: inside that 5.2% the
   budget discards two thirds of the text, and 20–29% of those descriptions are majority
   machine output — so where the budget bites hardest, it is often spending itself on
   logs. That is the backlog's option (d), and it should be scoped for the MVP (where a
   source is a Statement of Work, not a 1.2 KB ticket) and bundled into the next
   extraction re-run rather than paying $1.84 for it alone. (Reasoned.)

---

## G3a — vocabulary frequency gating

| Vocabulary | Raw terms | Canonical (floor off) | df ≥ 2 | df ≥ 3 | df ≥ 5 | df ≥ 10 |
|---|---:|---:|---:|---:|---:|---:|
| skill | 17,738 | **10,630** | 3,565 | **1,752** | 669 | 185 |
| specialization | 2,491 | 344 | 235 | 173 | 129 | 72 |

Document frequency is distinct contributions, not mentions.

| Vocabulary | df = 1 | df = 2 | df = 3 | df = 4 | df 5–9 | df 10+ |
|---|---:|---:|---:|---:|---:|---:|
| skill | **7,065** | 1,813 | 687 | 396 | 484 | 185 |
| specialization | 109 | 62 | 34 | 10 | 57 | 72 |

**Two thirds of the skill vocabulary (66.5%) is supported by a single contribution.** A
floor of 3 lands the skill vocabulary at 1,752, which is within rounding of the backlog's
~1,500 estimate; the specialization vocabulary was never the problem (344 canonicals, and
only 31.7% at df = 1).

The gate does not delete anything. A sub-floor cluster attaches to its nearest surviving
canonical by cosine over the same embeddings the clustering used, so every raw term still
resolves and no evidence is lost. Terms an operator forced in `config/term_overrides.yaml`
are exempt — a human judgment outranks a frequency threshold.

**Recommendation. Keep the flag off; propose floor 3 as the first thing benchmark v4
sweeps.** (Reasoned.) This is not a cosmetic change and should not be treated as one: at
floor 3, canonicals below the floor carry 52% of all contribution-term support, so folding
them materially changes what the structured arm can match. The backlog's own success test
— "a smaller vocabulary that does not improve retrieval is cosmetic" — is a
structured-arm candidate-recall question, and structured-arm candidate recall is exactly
what the retired split can no longer answer.

---

## G5 — extraction confidence in the score

**The signal barely exists on this corpus.** (Measured.)

| Confidence | Raw extraction | After Stage 3's clamp |
|---|---:|---:|
| high | 2,204 | 2,195 |
| medium | 462 | 471 |
| low | **0** | **0** |

The rubric has three levels and the extractor used two, on 82.3% / 17.7% of contributions.
So the component's whole dynamic range is 1.0 versus 0.6, on one contribution in six.

### Offline re-score

The v3 validation score-component checkpoint (30 cases, 32 roles, 1,086 candidate rows)
re-scored through the engine's own `combine_parts()`. Rows are the deterministic
score-only ranking.

| Arm | Hit@1 | Hit@5 | Hit@10 | MRR | Cases moved |
|---|---:|---:|---:|---:|---:|
| baseline (all flags off) | 0.100 | 0.467 | 0.700 | 0.287 | — |
| G5 component, mean confidence | 0.100 | 0.533 | 0.700 | 0.289 | 12 |
| G5 component, best case per person | 0.100 | 0.467 | 0.700 | 0.287 | 0 |
| G5 component, worst case per person | 0.167 | 0.533 | 0.667 | 0.323 | 23 |
| G5 multiplier, mean confidence | 0.100 | 0.467 | 0.700 | 0.282 | 9 |

Paired, mean-confidence component against baseline: Hit@5 +0.067 (2 wins, 0 losses,
McNemar p = 0.500), Hit@1 and Hit@10 unmoved, MRR +0.002 with a 95% bootstrap CI of
[−0.013, +0.014]. The multiplier form moves MRR −0.005.

**Read the "best case" row.** Taking each person's *highest* confidence moves nothing at
all — because nearly every person has at least one "high" contribution, so the component
becomes a constant and a constant component cannot reorder anything. That is the clearest
statement of the problem: there is almost no variance to rank on.

### The stand-in, stated plainly

The checkpoint stores four aggregate components per candidate and nothing else, so the
per-match confidence the engine computes cannot be reconstructed from it. The re-score
joins a **person-level** confidence profile instead. The engine asks "how confident is the
evidence behind *this* match"; the re-score asks "how confident is this person's evidence
in general". The best/worst-case rows bound how much that stand-in could be hiding, and
the best-case bound is exactly zero movement.

**Recommendation. Keep the flag off. Do not propose a default flip, and do not spend a
v4 sweep on it.** (Reasoned.) The PRD gap the backlog identified is now closed in the
sense that mattered — the signal is implemented and can be switched on in one line — but
on this corpus it is nearly constant, and giving it weight means taking weight from four
components that *were* tuned on validation data. That is a strictly negative trade until
there is a corpus with real confidence spread.

**Escalation.** The extraction prompt has never produced a single "low" confidence record
in 2,668 attempts. That is either a rubric that cannot be triggered or a model that will
not use it, and it is worth a look during the next extraction prompt revision. Recorded,
not actioned — out of scope for this order.

---

## G6 — primary/secondary specialization strength

Unlike G5, this label has real spread. (Measured.)

| Level | Raw specialization references | Projection edges (2,361 total) |
|---|---:|---:|
| primary | 2,853 | 563 all-primary |
| secondary | 2,663 | **1,372 all-secondary** |
| mixed | — | 426 |

(The raw counts are the backlog's, reproduced. Stage 3's within-contribution dedupe —
which keeps primary over secondary when two raw terms merge — brings them to 2,852 /
2,635 in `normalized.jsonl`.)

**58% of specialization edges were never once called primary.** Under the current score
those people match a brief exactly as strongly as a specialist does.

### Offline re-score

| Arm | Hit@1 | Hit@5 | Hit@10 | MRR | Cases moved |
|---|---:|---:|---:|---:|---:|
| baseline (all flags off) | 0.100 | 0.467 | 0.700 | 0.287 | — |
| G6, person primary share | 0.100 | 0.433 | 0.733 | 0.296 | 11 |
| **G6 control, constant scale at the mean credit** | 0.100 | 0.433 | 0.733 | 0.293 | 10 |
| G6, sensitivity: every match counts secondary | 0.133 | 0.500 | 0.800 | 0.308 | 16 |

Paired, person-primary-share against baseline: MRR +0.009 (9 cases better, 2 worse,
bootstrap CI [−0.000, +0.022]), Hit@10 +0.033, Hit@5 −0.033.

**The control row is the finding.** Scaling `specialization_match` by a *constant* — the
average strength credit, identical for every person — reproduces the person-varying arm
almost exactly (MRR 0.293 against 0.296, identical Hit@K). So nearly all of the movement
is the component being down-weighted, not the strength label separating anyone. And that
is a result the project already has: benchmark v2's weight sweep moved
`specialization_match` from 0.40 to 0.25 for the same reason.

Without that control this table would read as "G6 improves MRR". It does not; it mostly
re-discovers a weight.

**Recommendation. Keep the flag off, and treat it as the strongest wave-1 candidate for a
v4 sweep anyway — but sweep it against the constant-scale control, not against the
current weights.** (Reasoned.) The mechanism is the one the PRD asks for (§7.2 makes
specializations the primary match targets), the label has genuine spread where G5's does
not, and the person-varying arm is consistently positive on MRR (9 better, 2 worse). What
is *not* established is that any of that comes from the label. Only a benchmark that can
separate the two should be allowed to flip this default.

---

## G7 — re-rank presentation-order probe

**This is the one result in wave 1 that is measured on the live system, and it is the
one that should change what happens next.**

One paid arm: the frozen v3 configuration on the same 30 validation cases, with the
re-rank window presented **worst-first** instead of best-first. Retrieval, weights,
prompt, window width and model are identical to `ab_window32`, which is that same
configuration presented best-first. Cost: **$0.9454** of the $2.00 authorized.

| Arm | System | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `ab_window32` (score order, frozen) | `capgraph_full` | 30 | **0.400** | 0.633 | 0.867 | **0.523** | 1.000 | $0.8832 |
| `probe_order` (reverse order) | `capgraph_full` | 30 | **0.200** | 0.567 | 0.833 | **0.391** | 1.000 | $0.9454 |
| `ab_window32` (score order, frozen) | `capgraph_score` | 30 | 0.100 | 0.567 | 0.800 | 0.312 | 1.000 | $0.1144 |
| `probe_order` (reverse order) | `capgraph_score` | 30 | 0.200 | 0.500 | 0.733 | 0.350 | 1.000 | $0.1136 |

Paired, case by case — `capgraph_full`, reverse against score order:

| Metric | N | Score order | Reverse order | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 30 | 0.400 | 0.200 | **−0.200** | 0 | 6 | 24 | McNemar exact p = **0.031** |
| Hit@5 | 30 | 0.633 | 0.567 | −0.067 | 2 | 4 | 24 | p = 0.688 |
| Hit@10 | 30 | 0.867 | 0.833 | −0.033 | 2 | 3 | 25 | p = 1.000 |
| MRR | 30 | 0.523 | 0.391 | **−0.132** | 5 | 15 | 10 | 95% bootstrap CI **[−0.234, −0.040]** |

And the in-study noise gauge — `capgraph_score`, which ranks the whole pool and never
sees a prompt or an ordering, so nothing this probe changes can reach it:

| Metric | N | Score order | Reverse order | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 30 | 0.100 | 0.200 | +0.100 | 4 | 1 | 25 | p = 0.375 |
| MRR | 30 | 0.312 | 0.350 | +0.038 | 9 | 13 | 8 | 95% CI [−0.050, +0.139] |

### The verdict, per the backlog's own success test

**Hit@1 moved −0.200 — twice the 0.100 run-to-run floor. Presentation order dominates.**
(Measured.)

Three things sharpen it:

1. **The gauge moved the other way.** The deterministic arm's pool drifted *better* this
   run (+0.100 Hit@1) while the full system's answer got *worse* (−0.200). So the full
   arm's loss is not the pool being unluckier; the re-rank did worse on a marginally
   better pool.
2. **The model had the score in front of it the whole time.** The v3 card view prints the
   deterministic weighted score on every card — that was one of the stated reasons for
   adopting the card. Reversing the visual order still halved Hit@1. The model is not
   short of the information needed to recover the intended order; it is not using it.
3. **It triangulates with a result already on file.** v3's rejected `ab_selfconsistency`
   arm — the same window under independently shuffled orders, Borda-aggregated — scored
   Hit@1 0.267. So: score order 0.400, shuffled 0.267, reversed 0.200. Every departure
   from best-first costs Hit@1, monotonically in how far it departs. That arm was
   rejected as "self-consistency does not help"; it now reads as a second measurement of
   the same position effect.

**And the limit of it.** The McNemar p of 0.031 rests on six discordant cases that all
fell the same way — with six discordant pairs, 0.031 is the smallest p the test can
produce. It is a real signal on 30 cases, not a settled quantity, and Hit@5 and Hit@10
barely moved, which says the effect is concentrated at the top of the list rather than in
whether the right person is retrieved and shown at all.

### What this overturns

`docs/eval-results.md` attributes the v2 finding — that a better input ordering produced
the same output ranking — to the re-rank being a bottleneck. The backlog flagged a strong
position effect as an alternative explanation that had never been excluded. **It is no
longer merely not excluded; it is the better-supported reading.** A re-rank that is
substantially re-expressing its input order will look insensitive to a better input
ordering for exactly the reason observed.

**Recommendation (reasoned, on a measured base).** Escalate two things to the
orchestrator:

1. **Stop paying for re-rank tuning until the prompt is rethought.** v2 and v3 spent
   $11.58 between them on re-rank levers for no aggregate gain. If the ordering the model
   returns is substantially the ordering it was handed, that is where the money went.
2. **Benchmark v4 should carry a position control.** One arm at a fixed non-score
   presentation order, run alongside the headline arm, so every future re-rank result can
   be read against how much of it is position. This probe cost $0.95; the control is the
   same order of magnitude and it makes every subsequent re-rank number interpretable.

The `improvements.rerank_presentation_order` flag stays at `score` — reverse order is
strictly worse, and the probe is an ablation, not a candidate.

---

## G8 — the parsed headcount

`RoleSpec.count` has been parsed since Stage 6 and read by nothing. The engine now
partitions each role's final ranking: the top `count` are `proposed_person_ids`, the rest
`alternate_person_ids`, and the CLI prints which is which above the shortlist table.

Nothing about the ranking changes — same people, same order, same scores. The partition
is computed after the re-rank and the finisher have both run.

**Not measurable, and structurally so.** (Measured, in the sense that the benchmark's own
manifest says it: every case has exactly one truth person, so `Recall@K` collapses onto
`Hit@K` and a proposed *set* has nothing to be right or wrong about.) Team composition —
preferring complementary coverage of a role's skills over `count` near-duplicates — is
the interesting version of this and is deliberately out of scope until G12 produces
multi-person ground truth.

**Recommendation. Keep it; it is presentation, it is free, and it stops the engine
answering "I need two backend engineers" with an undifferentiated list.** (Reasoned.)

---

## G11a — activity currency

Quarters since each person's last contribution of *any* kind, at the holdout cutoff —
the same snapshot the graph's stored decay is frozen at, so this is the distribution the
shipped graph ranks on.

| Quarters idle | People |
|---|---:|
| 0–1 | 183 |
| 2–3 | 21 |
| 4–7 | 33 |
| 8–11 | 22 |
| 12+ | **57** |

| Measure | Value |
|---|---:|
| People | 316 |
| Median quarters idle | 1 |
| 90th percentile | 18 |
| Longest gap | 30 quarters (7.5 years) |
| Idle ≥ 2 years | **79 (25.0%)** |
| Idle ≥ 3 years | **57 (18.0%)** |
| Activity decay at the cutoff: p10 / p50 / p90 | 0.108 / 0.888 / 0.999 |

**A quarter of the roster has not touched the codebase in two years and still carries a
positive score on every skill they ever demonstrated.** The backlog's concern is real and
now quantified. (Measured.)

### Offline re-score — and this one is exact

Activity currency is person-level in the engine too, so the re-score is not a stand-in:
it is what the flag computes, at each case's own as-of time.

| Arm | Hit@1 | Hit@5 | Hit@10 | MRR | Cases moved |
|---|---:|---:|---:|---:|---:|
| baseline (all flags off) | 0.100 | 0.467 | 0.700 | 0.287 | — |
| G11a activity currency | 0.100 | 0.467 | 0.733 | 0.288 | 2 |

Two cases move, Hit@10 +0.033, MRR +0.001 (2 better, 0 worse).

**That near-zero is the expected answer, not a disappointing one.** The benchmark freezes
a same-project eligible roster and every case's truth is guaranteed inside it, so a
departed person can never be the right answer and rarely competes for the top of a list.
The backlog says this gap is structurally invisible here; the measurement confirms it.

**Recommendation. Keep the flag off, and record that benchmark v4 will not validate it
either unless roster construction changes.** (Reasoned.) The honest place for this signal
is the MVP pilot, where the roster is real and a 2016 leaver appearing on a shortlist is
an immediately visible failure. Option (b) — a hard activity window — stays rejected: an
automated "this person is gone" inference about a real employee is exactly the judgement
this system must not make unsupervised.

---

## G13 — small items

- **`httpx` is now a declared direct dependency.** `src/capgraph/llm.py` builds the
  OpenRouter client on it and it had been arriving as an `anthropic` transitive.
  `uv lock` refreshed: a 2-line diff, no version changes anywhere else. (Measured.)
- **Pitch spend figures.** Two of the three were already corrected on `main`, as the
  order said. One was not: `docs/manager-pitch.md` line 366 still read "$25.13" and now
  reads "$25.20", matching the two figures beside it and `data/llm_costs.jsonl`.
- **A judgement call, flagged.** The G7 probe adds spend to the ledger, which would make
  the pitch's "$25.20 across 4,203 calls" false the moment the probe ran — and the
  document claims every number in it is copied from a repository file. Both figures are
  now scoped as the research-track total at the close of the track on 2026-08-14, with
  later improvement work counted separately. That is the smallest edit that keeps the
  claim literally true; rewording the headline further is the document owner's call.

---

## Recommendations at a glance

| Backlog | Verdict | Basis |
|---|---|---|
| G1 | Fix shipped and on. Keep the gap open for the MVP's option (d); do not re-run extraction for it now | measured + reasoned |
| G3a | Flag stays off. Propose floor 3 as v4's first vocabulary sweep | reasoned |
| G5 | Flag stays off. **Do not spend a v4 sweep on it** — the signal is near-constant on this corpus. Escalate the "no low-confidence records ever" observation | measured + reasoned |
| G6 | Flag stays off. Strongest wave-1 candidate for a v4 sweep, but only against the constant-scale control | measured + reasoned |
| G7 | **Presentation order dominates**: Hit@1 −0.200, twice the noise floor, p = 0.031. Escalate — pause re-rank tuning, add a position control to v4 | measured + reasoned |
| G8 | Keep. Presentation only; team composition waits for G12 | reasoned |
| G11a | Flag stays off. Not validatable on this benchmark or on v4; belongs to the MVP pilot | measured + reasoned |
| G13 | Done | measured |

## What this document cannot say

- **No flag here is validated.** Every G3a/G5/G6/G11a result is a re-score of one
  checkpoint over 30 cases, read as direction and nothing more.
- **The G5 and G6 re-scores use person-level stand-ins** for quantities the engine
  computes per match. Sensitivity bounds are reported for both; G11a needs no stand-in.
- **In those re-scores the score-only ranking is what moved**, not the full system. A
  re-score cannot say what the LLM would do with a differently populated window — that
  needs paid re-ranks.
- **G7 is the exception, and should be read differently.** It is a live paid run of the
  full system, paired case by case against a frozen arm that differs by one thing, with
  its own noise gauge in the same study. It is still 30 cases and still one run, and its
  headline p-value rests on six discordant cases — but it is a measurement of the
  shipped system, not a re-score of a checkpoint.
- **Nothing here touched the retired 120-case test split**, in any form.
