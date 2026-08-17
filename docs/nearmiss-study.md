# Near-miss quality study — is a top-1 miss a plausible substitute?

- Generated 2026-08-17T19:00:22+00:00 by a worker session on `agent/nearmiss-study`
- Order: `docs/work-orders/nearmiss-study.md`
- Manifest: `data/eval/nearmiss/benchmark_manifest.v4-sibling-nearmiss.jsonl`, version `tawos-v1.1-benchmark-v4-sibling-nearmiss`
- Run: `data/eval/nearmiss/runs/v3frozen/rewritten`, configuration digest `a221958c8d132b49`
- Arm: validation split, `v3frozen`, rewritten briefs, one run

## The question, and what would answer it

The research report currently says something like *"where the system's first pick was not in the truth set, it was usually a plausible substitute"*. That is an interpretation of a handful of shortlists, not a measurement. This study measures it.

A **top-1 miss** is a case whose first-ranked person is not in the case's truth set — the people who actually resolved the sprint's issues. For every miss, and for every top-1 hit as a reference, three fixed similarity numbers are computed between the first-ranked person and the *nearest* member of the truth set, each against a control drawn from the same case's own frozen roster. If misses are no closer to truth than a random roster member, the plausible-substitute reading is wrong and this report says so.

**This is a descriptive study.** There are 28 cases, of which the misses are a subset; consecutive sprints inside a project share a mean truth-set Jaccard of 0.34, so the cases are not independent and the effective sample is smaller still. The intervals below are seeded bootstrap intervals over cases (10,000 resamples, seed 20260817) and are reported as descriptive spread. Nothing here is a hypothesis test, and no p-value appears in this document.

## Method

### The three similarity definitions

All three were fixed in `config/settings.yaml` (`eval.nearmiss.metrics`) before the run. Person profiles are read **read-only** from the production Neo4j graph — the same graph the system was queried against.

| # | Definition | How it is computed |
|---|---|---|
| a | (a) Jaccard over specialization sets | Jaccard (intersection over union) over the two people's complete `HAS_SPECIALIZATION` term sets. |
| b | (b) Jaccard over top-10 recency-weighted skills | Each person's `HAS_SKILL` edges ranked by `evidence_count × decay(last_used)`, with decay recomputed at **the case's own as-of time** at the configured half-life of 540 days; Jaccard over the top 10 of each. The graph's stored `decay_score` is frozen at the holdout cutoff and is never read, exactly as in the harness. |
| c | (c) Cosine between mean contribution embeddings | Cosine between the means of the two people's `Contribution.embedding` vectors (384-dim, all of their contributions). |

"Nearest truth person" is the maximum over the truth set, taken **per definition**: the three measure different things, so forcing one winner on all three would report a number no definition produced.

**One property of definition (b), stated because a reader would otherwise assume otherwise.** Recomputing decay at each case's as-of time is the right temporal discipline and is what the harness does — but it does not change which skills come top. The weight is `count × exp(−λ(as_of − last_used))`, so the ratio between any two skills is `(c₁/c₂) × exp(λ(t₁ − t₂))` and the as-of term cancels. The only thing that would break that is a `last_used` *after* the as-of time, where the decay clamps at 1.0, and that cannot happen here: the graph is frozen at the 2019-01-01 holdout cutoff and every case's as-of time is later. So (b) is in practice one fixed top-10 set per person across all 28 cases. It is still a recency-weighted set — recency decides which skills are in it — it just does not vary case to case. Tested: `test_the_top_skill_ranking_is_as_of_invariant_on_a_graph_frozen_before_every_case`.

### The control

For each case, 100 seeded draws (seed 20260817, derived per case) of one member of that case's own frozen eligible roster, with replacement; each drawn person gets the same three numbers against the same truth set, and the **median** of the draws is the case's control. The roster is used whole — the work order specifies "a random eligible roster member" with no carve-out, so a draw can land on a truth member, and the median of 100 is what keeps that from moving the reference.

### Adjacent-sprint truth membership

For each case, whether the first-ranked person appears in the truth set of the immediately previous or immediately next sprint of the same project. Adjacency runs over the **whole** rebuilt structure — every candidate sprint with a recorded start, selected or not — so "the next sprint" means the project's next sprint and not the next sampled case.

**This is post-as-of information.** The next sprint's truth set did not exist when the question was asked. It is legitimate as a post-hoc diagnostic — it asks whether the named person was working on this team's work around this time — and it is **never** available for tuning, was not used to choose anything here, and must not be quoted as a system metric.

## Verification: this is the same instrument, rebuilt

The frozen v4 manifest and every v4 run checkpoint were destroyed on 2026-08-16 (`docs/incident-2026-08-16-data-loss.md`), so the manifest here was rebuilt from the surviving Stage 0 parquet. Package selection, split assignment, rosters and truth sets are seed-deterministic, so the rebuild is checkable against the published record — and it was checked before anything was paid for. A mismatch on any row below is a hard failure in code, not a warning.

### The validation split (docs/benchmark-v4-manifest.md §6.2)

| Check | Rebuilt | Published record | |
|---|---|---|---|
| `cases` | 28 | 28 | match |
| `projects` | DM 15, FAB 1, MESOS 6, TIMOB 6 | DM 15, FAB 1, MESOS 6, TIMOB 6 | match |
| `mean_truth_set_size` | 3.39 | 3.39 | match |

### The whole structure (docs/benchmark-v4-manifest.md §6.1-6.3)

A drifted sample could still yield 28 validation cases holding different sprints, so the rest of the published accounting is verified too. Building the test split's *rows* is data construction, not exposure: no test brief was rewritten, run, scored or read.

| Check | Rebuilt | Published record | |
|---|---|---|---|
| `candidates` | 1061 | 1061 | match |
| `selected` | 150 | 150 | match |
| `exclusion_reasons` | no_truth_resolver 1, nothing_planned_before_start 115, sampled_out 7, sprint_start_missing 10, sprint_start_not_post_cutoff 760, too_few_brief_issues 18 | no_truth_resolver 1, nothing_planned_before_start 115, sampled_out 7, sprint_start_missing 10, sprint_start_not_post_cutoff 760, too_few_brief_issues 18 | match |
| `truth_people_total` | 631 | 631 | match |
| `truth_people_dropped_ineligible` | 502 | 502 | match |
| `briefs_hitting_a_cap` | 59 | 59 | match |
| `test_cases` | 122 | 122 | match |
| `test_projects` | DM 64, FAB 4, MESOS 26, TIMOB 28 | DM 64, FAB 4, MESOS 26, TIMOB 28 | match |
| `test_mean_truth_set_size` | 4.39 | 4.39 | match |

Every published number reproduces. What does **not** reproduce, and cannot, is the brief text: the rewrites were re-generated on the same model and prompt from the same sanitized pre-as-of package text, so they are new words describing the same work. That is why this manifest is labelled a **sibling** — `tawos-v1.1-benchmark-v4-sibling-nearmiss`, in both the file name and the version string — and why no number in this report is compared against a v4 checkpoint. Everything below was measured inside this study's own single run.

## The run

One run of the full graph system (`capgraph_full`) over the 28 validation cases; 28 scored, none failed.

| | Cases | Share |
|---|---:|---:|
| top-1 hit | 8 | 0.286 |
| top-1 miss | 20 | 0.714 |
| **total** | **28** | |

Hit@1 on this run is therefore 0.286. The v4 record's own validation Hit@1 for this arm is not quoted beside it: those run checkpoints are gone, the briefs here are re-generated, and the deterministic sweeps study measured a run-to-run noise floor on this instrument that a 28-case difference sits inside. The number above is this run's, and it is the only place the study's own denominators come from.

## Every case, one row each

n is small, so nothing is aggregated away. `sim` is the first-ranked person's similarity to the nearest truth person; `ctl` is that case's control (median of 100 roster draws). `adj` is adjacent-sprint truth membership (P = previous sprint, N = next, `-` = neither) — post-as-of, diagnostic only. `truth rank` is where the first truth person actually landed in the ranking.

### Top-1 misses

| Package | Project | As-of | Roster | Truth | Top-1 | Truth rank | a sim | a ctl | b sim | b ctl | c sim | c ctl | adj |
|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| `DM:sprint:841` | DM | 2019-01-07 | 105 | 4 | `DM:145773` | 2 | 0.333 | 0.200 | 0.053 | 0.000 | 0.935 | 0.899 | — |
| `DM:sprint:845` | DM | 2019-02-04 | 105 | 1 | `DM:145735` | 7 | 0.125 | 0.111 | 0.000 | 0.000 | 0.890 | 0.877 | — |
| `DM:sprint:847` | DM | 2019-03-04 | 105 | 1 | `DM:145735` | 8 | 0.125 | 0.115 | 0.000 | 0.000 | 0.890 | 0.869 | — |
| `DM:sprint:898` | DM | 2019-04-09 | 105 | 10 | `DM:145928` | 2 | 0.250 | 0.218 | 0.111 | 0.000 | 0.933 | 0.884 | — |
| `DM:sprint:881` | DM | 2019-04-29 | 105 | 1 | `DM:145735` | 14 | 0.125 | 0.136 | 0.000 | 0.000 | 0.890 | 0.876 | — |
| `DM:sprint:888` | DM | 2019-08-05 | 105 | 2 | `DM:145735` | 22 | 0.158 | 0.115 | 0.000 | 0.000 | 0.890 | 0.869 | — |
| `DM:sprint:890` | DM | 2019-09-02 | 105 | 2 | `DM:145735` | 8 | 0.158 | 0.143 | 0.000 | 0.000 | 0.890 | 0.869 | — |
| `DM:sprint:966` | DM | 2019-10-28 | 105 | 1 | `DM:145735` | 22 | 0.125 | 0.120 | 0.000 | 0.000 | 0.890 | 0.872 | — |
| `DM:sprint:975` | DM | 2019-11-25 | 105 | 1 | `DM:145735` | 19 | 0.125 | 0.125 | 0.000 | 0.000 | 0.890 | 0.870 | — |
| `DM:sprint:978` | DM | 2019-12-23 | 105 | 1 | `DM:145735` | 13 | 0.125 | 0.111 | 0.000 | 0.000 | 0.890 | 0.868 | — |
| `DM:sprint:993` | DM | 2020-01-20 | 105 | 1 | `DM:145874` | 35 | 0.000 | 0.136 | 0.000 | 0.000 | 0.776 | 0.876 | — |
| `DM:sprint:1009` | DM | 2020-03-30 | 105 | 1 | `DM:145735` | 32 | 0.125 | 0.113 | 0.000 | 0.000 | 0.890 | 0.863 | — |
| `DM:sprint:1034` | DM | 2020-07-20 | 105 | 1 | `DM:145735` | 23 | 0.125 | 0.106 | 0.000 | 0.000 | 0.890 | 0.865 | — |
| `FAB:sprint:678` | FAB | 2019-02-19 | 62 | 2 | `FAB:144595` | 2 | 0.600 | 0.273 | 0.111 | 0.053 | 0.965 | 0.894 | — |
| `MESOS:sprint:448` | MESOS | 2019-01-02 | 67 | 4 | `MESOS:3662` | 2 | 0.364 | 0.333 | 0.053 | 0.053 | 0.961 | 0.928 | — |
| `MESOS:sprint:478` | MESOS | 2019-02-14 | 67 | 4 | `MESOS:3389` | 2 | 0.538 | 0.333 | 0.000 | 0.000 | 0.952 | 0.923 | — |
| `MESOS:sprint:479` | MESOS | 2019-02-27 | 67 | 2 | `MESOS:3389` | 26 | 0.538 | 0.333 | 0.053 | 0.000 | 0.947 | 0.933 | — |
| `MESOS:sprint:546` | MESOS | 2019-07-18 | 67 | 2 | `MESOS:3406` | 7 | 0.333 | 0.270 | 0.053 | 0.000 | 0.963 | 0.919 | — |
| `TIMOB:sprint:1146` | TIMOB | 2019-06-18 | 61 | 7 | `TIMOB:166083` | 2 | 0.308 | 0.279 | 0.111 | 0.000 | 0.962 | 0.947 | — |
| `TIMOB:sprint:1180` | TIMOB | 2020-02-17 | 61 | 3 | `TIMOB:166009` | 2 | 0.346 | 0.222 | 0.053 | 0.000 | 0.965 | 0.943 | PN |

### Top-1 hits (reference)

| Package | Project | As-of | Roster | Truth | Top-1 | Truth rank | a sim | a ctl | b sim | b ctl | c sim | c ctl | adj |
|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| `DM:sprint:871` | DM | 2019-03-18 | 105 | 5 | `DM:145720` | 1 | 1.000 | 0.200 | 1.000 | 0.000 | 1.000 | 0.894 | P |
| `DM:sprint:957` | DM | 2019-10-14 | 105 | 6 | `DM:145772` | 1 | 1.000 | 0.231 | 1.000 | 0.000 | 1.000 | 0.882 | — |
| `MESOS:sprint:449` | MESOS | 2019-01-02 | 67 | 2 | `MESOS:3401` | 1 | 1.000 | 0.167 | 1.000 | 0.000 | 1.000 | 0.932 | — |
| `MESOS:sprint:590` | MESOS | 2020-01-06 | 67 | 3 | `MESOS:3389` | 1 | 1.000 | 0.286 | 1.000 | 0.000 | 1.000 | 0.936 | PN |
| `TIMOB:sprint:1112` | TIMOB | 2019-02-11 | 61 | 8 | `TIMOB:166083` | 1 | 1.000 | 0.286 | 1.000 | 0.000 | 1.000 | 0.944 | PN |
| `TIMOB:sprint:1118` | TIMOB | 2019-02-24 | 61 | 8 | `TIMOB:166083` | 1 | 1.000 | 0.292 | 1.000 | 0.000 | 1.000 | 0.949 | PN |
| `TIMOB:sprint:1157` | TIMOB | 2019-07-29 | 61 | 7 | `TIMOB:166083` | 1 | 1.000 | 0.250 | 1.000 | 0.000 | 1.000 | 0.940 | — |
| `TIMOB:sprint:1201` | TIMOB | 2020-07-06 | 61 | 5 | `TIMOB:166019` | 1 | 1.000 | 0.222 | 1.000 | 0.000 | 1.000 | 0.950 | PN |

### How many different people the misses actually are

The 20 miss cases name **10 distinct people** first; the most frequent, `DM:145735`, takes the top slot in 10 of them. Read the distribution table below with that in mind: where one profile dominates, a mean similarity over misses is substantially a fact about *that person's* profile rather than about the system's ability to tell roster members apart. A mean cannot show this, which is why the table above lists every case.

On a hit the first-ranked person *is* a truth person, so every `sim` in the hit table is 1.000 by construction. That is the arithmetic sanity check, and it is not the interesting half: the hit rows' **controls** are, because they show whether the control is systematically easier on the cases the system got right. If the hit controls and the miss controls sit at similar levels, the miss-minus-control gap cannot be explained away as "the misses were easier cases".

## Distributions against the control

Mean over cases with a seeded 95% bootstrap interval over cases. `Δ` is the paired per-case difference (similarity − that case's own control), which is the quantity the reading turns on: it is measured inside each case, so it is immune to a case being intrinsically easy or hard.

### Top-1 misses — n = 20

| Definition | Similarity to nearest truth | Control (random roster member) | Δ paired | Above ctl | Below ctl | Tied |
|---|---|---|---|---:|---:|---:|
| (a) Jaccard over specialization sets | 0.246 [0.177, 0.318] | 0.190 [0.154, 0.228] | +0.057 [+0.017, +0.101] | 17 | 3 | 0 |
| (b) Jaccard over top-10 recency-weighted skills | 0.030 [0.013, 0.049] | 0.005 [0.000, 0.013] | +0.025 [+0.011, +0.041] | 7 | 0 | 13 |
| (c) Cosine between mean contribution embeddings | 0.913 [0.893, 0.931] | 0.892 [0.880, 0.905] | +0.021 [+0.006, +0.032] | 19 | 1 | 0 |

### Top-1 hits (reference) — n = 8

| Definition | Similarity to nearest truth | Control (random roster member) | Δ paired | Above ctl | Below ctl | Tied |
|---|---|---|---|---:|---:|---:|
| (a) Jaccard over specialization sets | 1.000 [1.000, 1.000] | 0.242 [0.211, 0.270] | +0.758 [+0.730, +0.789] | 8 | 0 | 0 |
| (b) Jaccard over top-10 recency-weighted skills | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | +1.000 [+1.000, +1.000] | 8 | 0 | 0 |
| (c) Cosine between mean contribution embeddings | 1.000 [1.000, 1.000] | 0.928 [0.910, 0.943] | +0.072 [+0.057, +0.090] | 8 | 0 | 0 |

## Adjacent-sprint truth membership

A post-as-of diagnostic, stated again because it matters: the neighbouring sprints' truth sets were not knowable when the question was asked. This number is not a system metric and nothing in this study was tuned on it.

| Group | n | In previous or next | Share | In previous | In next |
|---|---:|---:|---:|---:|---:|
| top-1 misses | 20 | 1 | 0.050 | 1 | 1 |
| top-1 hits | 8 | 5 | 0.625 | 5 | 4 |

### How much chance this diagnostic had

A share near zero can mean two different things — the named person was not on this team's work, or the *neighbouring sprint was not this team's sprint*. On this corpus the second matters, because the larger projects run several sprint boards at once and their sprints interleave by start date, so "the project's next sprint by date" is frequently a different team's:

| Project | Post-cutoff sprints | Other sprints starting within ±7 days (median) | (max) |
|---|---:|---:|---:|
| DM | 150 | 3 | 7 |
| FAB | 10 | 0 | 0 |
| MESOS | 83 | 3 | 4 |
| TIMOB | 48 | 0 | 1 |

Measured from recorded start dates alone — no sprint-name parsing, which is the trap here, since one project's sprints are named by year ("2019 Sprint 4") and a name-based count reports every sprint as its own board. A median above zero means several boards are live at once, so the sprint that is *next by date* is routinely not the same team's next sprint; a median of zero means the project runs one board at a time and date-adjacency is meaningful there. The numbers below say how much continuity the diagnostic actually had to find.

| Measure | Miss cases | Hit cases |
|---|---|---|
| Median people in a case's two neighbouring truth sets | 7 | 8 |
| Cases whose neighbours contribute no truth at all | 0 of 20 | 0 of 8 |
| **Jaccard between a case's own truth set and its neighbours'** | 0.140 [0.023, 0.286] | 0.704 [0.408, 0.958] |

Read that last row first, and read it across the two columns. The v4 manifest record measured a mean truth-set Jaccard of 0.34 between *consecutive* packages in a project, which is where the "the same team runs consecutive sprints" caveat comes from. Where the figure here is far below that, date-adjacency is not picking out the same team at all, so a low adjacent-sprint share is evidence about the **definition** rather than about the person named.

The two columns are the thing to notice: the cases the system got right are largely cases whose neighbouring sprints *are* the same team's, and the cases it missed are largely cases whose neighbours are a different team's. The adjacent-sprint share therefore separates the two groups mostly by that property, not by whether the named person was on the team — which is why the adjacency number does not carry the study's claim. Stated rather than corrected: the metric was pre-specified and is computed exactly as specified.

## Supplementary, and **not** pre-specified

**The work order pre-specified the metrics above and told this study to add nothing post-hoc. The following is not one of them and the claim at the end of this report does not rest on it.** It is here because a reader will reasonably ask what the scale means — is a specialization Jaccard of 0.19 close or not? — and the natural yardstick is how alike the people who worked the *same* package are to each other. For each case with two or more truth members: each member's similarity to their own nearest teammate, averaged over the members. It is computed from the truth set alone and so does not depend on what the system answered. Single-person truth sets have no teammate and are dropped.

| Definition | Miss cases | Hit cases |
|---|---|---|
| (a) Jaccard over specialization sets | 0.321 [0.250, 0.424] (n=11) | 0.264 [0.236, 0.287] (n=8) |
| (b) Jaccard over top-10 recency-weighted skills | 0.036 [0.018, 0.053] (n=11) | 0.049 [0.031, 0.066] (n=8) |
| (c) Cosine between mean contribution embeddings | 0.946 [0.940, 0.952] (n=11) | 0.941 [0.933, 0.950] (n=8) |

Compare each row against the same definition's miss row in the distribution table above. If a miss scores about as high as a real teammate does, "same capability neighbourhood" is a fair description of it; if a real teammate scores far higher, the miss is measurably outside the team even where it beats the random control. Both readings are visible in the same two numbers, which is why this section exists even though nothing depends on it.

## What this study cannot say

- **It is descriptive.** n(misses) is 20, the cases are correlated within a project, and the intervals are bootstrap spread over those few cases. Read the per-case table before any interval.
- **Similar is not interchangeable.** Every number here is computed from the same extracted profiles the system ranks on. A person who looks close in that representation may be close *because the representation is coarse*, not because they could have done the work. This measures neighbourhood in the system's own feature space, and that is a weaker claim than substitutability.
- **Truth is who did the work, not who should have.** A miss whose profile is far from truth is not automatically wrong either — the truth set is one team's historical assignment, and the whole benchmark rests on that being a prediction target rather than a statement about optimal staffing.
- **The control is a roster draw, not a hard case.** It answers "better than anybody on this roster?", which is the weakest bar worth clearing. It is not a second system and beating it is not evidence of quality.
- **No result here licenses an employment decision.** Same as everywhere else in this PoC: project-qualified pseudonyms, public OSS Jira, research only.

## Report-ready statement

One paragraph, generated from the numbers above by a rule fixed before the run (a definition counts only if its paired bootstrap interval excludes zero). Use it as written or not at all.

> On the 28 rebuilt work-package validation cases, one run of the full system placed a truth-set member first in 8 cases and someone else first in 20. For those 20 top-1 misses we measured how close the named person's capability profile is to the nearest person who actually did the work, on three definitions fixed in advance — shared specializations, shared recency-weighted top skills, and cosine between mean contribution embeddings — each against the median of 100 random draws from the same case's own eligible roster. On all three pre-specified definitions the misses sit closer to the truth set than a random member of the same roster does, by intervals that do not reach zero. The measured values were: (a) Jaccard over specialization sets Δ +0.057 [+0.017, +0.101]; (b) Jaccard over top-10 recency-weighted skills Δ +0.025 [+0.011, +0.041]; (c) Cosine between mean contribution embeddings Δ +0.021 [+0.006, +0.032]. Two things qualify that. The margins are small in absolute terms and sit below the yardstick of how alike two people who worked the *same* package are (miss similarity against intra-team similarity, per definition: 0.246 against 0.321; 0.030 against 0.036; 0.913 against 0.946). And the 20 misses are only 10 distinct people: `DM:145735` is ranked first in 10 of them, so these means are partly a fact about one profile rather than about the system's discrimination across the roster. Separately, and as a post-hoc diagnostic that uses information from after the question was asked, 1 of the 20 first-ranked misses appears in the truth set of the immediately previous or next sprint of the same project (0.050); that figure should be read against the calendar — the median number of other sprints in the same project starting within 7 days is DM 3, FAB 0, MESOS 3, TIMOB 0, so several boards are live at once in DM and MESOS. A date-adjacent sprint is therefore frequently a different team's, and on the miss cases a case's own truth set overlaps its neighbours' by a Jaccard of only 0.140. All of this is descriptive, on a small and project-correlated sample, and measured inside the system's own profile representation: it is a statement about measured neighbourhood, and it does not establish that the person named could have done the work.

| Definition | Verdict under the pre-committed rule |
|---|---|
| (a) Jaccard over specialization sets | supports |
| (b) Jaccard over top-10 recency-weighted skills | supports |
| (c) Cosine between mean contribution embeddings | supports |

## Spend and reproduction

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `nearmiss_rewrite` | 29 | 0.0100 |
| `nearmiss_val` | 82 | 1.6878 |
| **total** | | **1.6978** |

Against the $4.00 the owner authorized on 2026-08-17, reconciled by stage name against `data/llm_costs.jsonl` with retries included. By call type:

| Call type | Calls | Cost (USD) |
|---|---:|---:|
| `brief_rewrite` | 29 | 0.0100 |
| `intent` | 28 | 0.1529 |
| `rerank` | 54 | 1.5349 |

```bash
make nearmiss-structure   # offline: rebuild the sibling manifest, verify it
make nearmiss-rewrite     # SPENDS under nearmiss_rewrite (validation only)
make nearmiss-run         # SPENDS under nearmiss_val, once
make nearmiss-report      # offline: recompute the metrics, rewrite this document
```

The manifest rebuild and the run are both idempotent: the structure step is deterministic and free, the rewrite step is a no-op once every validation brief has one, and the run resumes from its checkpoint. A drifted rebuild refuses to write rather than reporting a warning, so `make nearmiss-structure` is also the audit.

Graph state these numbers were measured against (read-only): the accepted Stage 5 load — Person 316, Contribution 2,666, Skill 10,630, `HAS_SKILL` 17,589, `HAS_SPECIALIZATION` 2,361 — unchanged from the state every v4 number was measured against.
