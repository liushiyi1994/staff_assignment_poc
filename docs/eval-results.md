# Temporal benchmark results

Generated 2026-08-11 from manifest `tawos-v1.1-benchmark-v1` (seed 20260713), configuration digest `01ac985a36e1bb63`.

Historical assignment is the *prediction target*, not proof that the assignee was the uniquely or optimally qualified person. The defensible claim is narrowly predictive: given only information available when a historical issue was created, the system ranks its eventual assignee in the top K this often.

## Run configuration

| Setting | Value |
|---|---|
| Cases | 150 selected (test 120, validation 30) |
| Holdout cutoff | 2019-01-01 (graph evidence strictly before) |
| Projects / roster size | DM 105, EVG 21, FAB 62, MESOS 67, TIMOB 61 |
| Intent model | `openai/gpt-5.6-terra` |
| Re-rank model | `openai/gpt-5.6-terra` |
| Embedding model | `BAAI/bge-small-en-v1.5` (local, 384 dims) |
| Retrieval | vector top-40 ∪ structured top-40, re-rank top-15 |
| Score weights | specialization_match 0.4, skill_overlap 0.25, recency 0.2, evidence_strength 0.15 |
| Recency | half-life 540 d, recomputed at each case's as-of time |
| Cost-log stage | `stage7_eval` |

## Systems

| System | What it is |
|---|---|
| `capgraph_full` | Full pipeline: intent parse, vector ∪ structured retrieval, weighted score, LLM re-rank of the top-K with cited evidence. |
| `capgraph_score` | Ablation: the same retrieval and weighted score, no re-rank. Still uses the intent parse, so it isolates the re-rank, not every LLM call. |
| `bm25` | BM25 over one concatenated pre-cutoff evidence document per person. |
| `vector_only` | Plain RAG: the same evidence text embedded per ticket, person scored by nearest ticket. |
| `most_active` | Pre-cutoff evidence-ticket count in the case's project; ignores the brief. |

Every system ranks only the case's frozen same-project eligible roster. The three baselines rank the whole roster, so their candidate recall is 1.0 by construction; the graph system ranks its retrieved union, so its candidate recall is a real measurement and bounds its Hit@K.

## Method and leakage guards

A benchmark case is a real issue treated as a brief at its **creation** time. Nothing later is allowed to influence any system:

1. **Query text** is the issue's creation-time summary and description, reconstructed from the change log, with identifiers, pseudonyms, mentions, and email addresses stripped. Comments and later edits are never substituted in.
2. **Truth** is the assignee reconstructed at the safe resolution boundary. The dump's final-assignee snapshot is audit-only, and a case whose truth is outside the frozen roster was excluded at manifest build time.
3. **Roster** is the same-project eligible set frozen at the holdout cutoff, and it travels into Cypher as a parameter — the structured arm matches inside it, the vector arm filters the index result to it, and the harness refuses (and records as a failure) any output naming someone outside it.
4. **Evidence** is the pre-cutoff Stage 1 view for every system: the graph was built from those buckets, and BM25 and the vector baseline read the same sanitized ticket text. "Pre-cutoff resolved tickets" therefore means retained evidence tickets — buckets too small to extract from were dropped upstream, and all four systems inherit that truncation equally.
5. **Recency** is recomputed for every capability edge from its stored `last_used` at the case's as-of time, through the same Stage 4 `decay()` the pipeline uses. The graph's stored decay is frozen at the cutoff, which is earlier than every query time here, and is never read during evaluation. Wall-clock time is not an input anywhere.
6. **Splits.** The 30 validation cases were run first and reviewed; the configuration was then frozen and the 120 test cases were run once, under the configuration digest recorded above. Both splits are reported separately.

Latency excludes one-time process startup (the local embedding model is loaded before the first case is timed). Cost is the spend the LLM gateway actually logged, retries included.

## Validation split

Case accounting — every manifest case is scored or listed as a failure:

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 30 | 30 | 0 |
| `capgraph_score` | 30 | 30 | 0 |
| `bm25` | 30 | 30 | 0 |
| `vector_only` | 30 | 30 | 0 |
| `most_active` | 30 | 30 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full — capgraph (score + LLM re-rank) | 30 | 0.367 | 0.733 | 0.833 | 0.733 | 0.833 | 0.513 | 0.967 | 20923.9 | 18598.5 | 36794.9 | 1.0857 |
| capgraph_score — capgraph (deterministic score only) | 30 | 0.133 | 0.500 | 0.600 | 0.500 | 0.600 | 0.300 | 0.967 | 2931.2 | 2720.9 | 4830.7 | 0.1139 |
| bm25 — BM25 over pre-cutoff ticket text | 30 | 0.367 | 0.567 | 0.600 | 0.567 | 0.600 | 0.470 | 1.000 | 12.9 | 1.4 | 86.5 | 0.0000 |
| vector_only — pure vector (plain RAG) | 30 | 0.133 | 0.367 | 0.600 | 0.367 | 0.600 | 0.282 | 1.000 | 49.0 | 41.1 | 110.8 | 0.0000 |
| most_active — most-active in project | 30 | 0.067 | 0.267 | 0.333 | 0.267 | 0.333 | 0.177 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

The graph system against the strongest baseline on each metric — the best baseline per column, not an average, and reported whichever way it falls:

| Metric | capgraph_full | Best baseline | Δ |
|---|---:|---|---:|
| Hit@1 | 0.367 | 0.367 (`bm25`) | +0.000 |
| Hit@5 | 0.733 | 0.567 (`bm25`) | +0.167 |
| Hit@10 | 0.833 | 0.600 (`vector_only`) | +0.233 |
| MRR | 0.513 | 0.470 (`bm25`) | +0.042 |

### Validation split by project

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full / DM (roster 105) | 6 | 0.500 | 0.667 | 0.833 | 0.667 | 0.833 | 0.615 | 1.000 | 20230.6 | 17645.7 | 29875.7 | 0.2323 |
| capgraph_full / EVG (roster 21) | 6 | 0.500 | 0.667 | 1.000 | 0.667 | 1.000 | 0.611 | 1.000 | 17719.3 | 16384.0 | 23573.8 | 0.1650 |
| capgraph_full / FAB (roster 62) | 6 | 0.500 | 0.833 | 0.833 | 0.833 | 0.833 | 0.618 | 1.000 | 22538.5 | 18488.6 | 36794.9 | 0.2338 |
| capgraph_full / MESOS (roster 67) | 6 | 0.000 | 0.667 | 0.667 | 0.667 | 0.667 | 0.220 | 1.000 | 23564.6 | 19773.5 | 45134.8 | 0.2386 |
| capgraph_full / TIMOB (roster 61) | 6 | 0.333 | 0.833 | 0.833 | 0.833 | 0.833 | 0.500 | 0.833 | 20566.4 | 20231.2 | 24183.0 | 0.2160 |
| capgraph_score / DM (roster 105) | 6 | 0.000 | 0.167 | 0.333 | 0.167 | 0.333 | 0.113 | 1.000 | 3070.5 | 3099.5 | 4521.9 | 0.0230 |
| capgraph_score / EVG (roster 21) | 6 | 0.167 | 1.000 | 1.000 | 1.000 | 1.000 | 0.444 | 1.000 | 2229.4 | 1917.4 | 3259.9 | 0.0211 |
| capgraph_score / FAB (roster 62) | 6 | 0.167 | 0.667 | 0.667 | 0.667 | 0.667 | 0.384 | 1.000 | 3317.4 | 3025.0 | 4830.7 | 0.0237 |
| capgraph_score / MESOS (roster 67) | 6 | 0.167 | 0.333 | 0.500 | 0.333 | 0.500 | 0.261 | 1.000 | 3223.1 | 3145.9 | 4867.3 | 0.0219 |
| capgraph_score / TIMOB (roster 61) | 6 | 0.167 | 0.333 | 0.500 | 0.333 | 0.500 | 0.299 | 0.833 | 2815.6 | 2645.8 | 3825.9 | 0.0242 |
| bm25 / DM (roster 105) | 6 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.529 | 1.000 | 16.1 | 2.2 | 86.5 | 0.0000 |
| bm25 / EVG (roster 21) | 6 | 0.333 | 0.667 | 0.667 | 0.667 | 0.667 | 0.497 | 1.000 | 4.1 | 0.8 | 21.0 | 0.0000 |
| bm25 / FAB (roster 62) | 6 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 | 0.700 | 1.000 | 9.1 | 1.1 | 47.8 | 0.0000 |
| bm25 / MESOS (roster 67) | 6 | 0.333 | 0.333 | 0.500 | 0.333 | 0.500 | 0.387 | 1.000 | 8.5 | 1.2 | 45.7 | 0.0000 |
| bm25 / TIMOB (roster 61) | 6 | 0.167 | 0.333 | 0.333 | 0.333 | 0.333 | 0.238 | 1.000 | 26.7 | 2.1 | 150.1 | 0.0000 |
| vector_only / DM (roster 105) | 6 | 0.167 | 0.333 | 0.500 | 0.333 | 0.500 | 0.304 | 1.000 | 51.2 | 40.1 | 113.0 | 0.0000 |
| vector_only / EVG (roster 21) | 6 | 0.000 | 0.667 | 0.833 | 0.667 | 0.833 | 0.271 | 1.000 | 45.0 | 36.0 | 96.9 | 0.0000 |
| vector_only / FAB (roster 62) | 6 | 0.333 | 0.667 | 0.833 | 0.667 | 0.833 | 0.533 | 1.000 | 48.0 | 43.3 | 81.5 | 0.0000 |
| vector_only / MESOS (roster 67) | 6 | 0.000 | 0.000 | 0.500 | 0.000 | 0.500 | 0.087 | 1.000 | 58.7 | 51.0 | 110.8 | 0.0000 |
| vector_only / TIMOB (roster 61) | 6 | 0.167 | 0.167 | 0.333 | 0.167 | 0.333 | 0.214 | 1.000 | 42.3 | 42.8 | 44.8 | 0.0000 |
| most_active / DM (roster 105) | 6 | 0.000 | 0.333 | 0.500 | 0.333 | 0.500 | 0.116 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / EVG (roster 21) | 6 | 0.167 | 0.667 | 0.667 | 0.667 | 0.667 | 0.354 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / FAB (roster 62) | 6 | 0.167 | 0.167 | 0.167 | 0.167 | 0.167 | 0.225 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / MESOS (roster 67) | 6 | 0.000 | 0.167 | 0.333 | 0.167 | 0.333 | 0.139 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / TIMOB (roster 61) | 6 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.048 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

### Validation run diagnostics (graph system)

| Measure | Value |
|---|---|
| cases | 30 |
| multi role cases | 3 |
| llm calls | 63 |
| rerank entries min | 11 |
| rerank entries median | 15 |
| cases below ten ranked | 0 |
| candidate pool min | 11 |
| candidate pool median | 38 |
| candidate pool max | 62 |
| rejected rerank entries | 2 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry |

## Test split

Case accounting — every manifest case is scored or listed as a failure:

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 120 | 120 | 0 |
| `capgraph_score` | 120 | 120 | 0 |
| `bm25` | 120 | 120 | 0 |
| `vector_only` | 120 | 120 | 0 |
| `most_active` | 120 | 120 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full — capgraph (score + LLM re-rank) | 120 | 0.325 | 0.567 | 0.767 | 0.567 | 0.767 | 0.449 | 0.925 | 20913.9 | 19055.1 | 36560.5 | 4.3746 |
| capgraph_score — capgraph (deterministic score only) | 120 | 0.158 | 0.483 | 0.708 | 0.483 | 0.708 | 0.319 | 0.925 | 2887.9 | 2514.1 | 5075.6 | 0.4514 |
| bm25 — BM25 over pre-cutoff ticket text | 120 | 0.258 | 0.592 | 0.708 | 0.592 | 0.708 | 0.404 | 1.000 | 4.3 | 1.3 | 4.2 | 0.0000 |
| vector_only — pure vector (plain RAG) | 120 | 0.175 | 0.467 | 0.658 | 0.467 | 0.658 | 0.340 | 1.000 | 46.0 | 43.3 | 62.2 | 0.0000 |
| most_active — most-active in project | 120 | 0.042 | 0.308 | 0.375 | 0.308 | 0.375 | 0.175 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

The graph system against the strongest baseline on each metric — the best baseline per column, not an average, and reported whichever way it falls:

| Metric | capgraph_full | Best baseline | Δ |
|---|---:|---|---:|
| Hit@1 | 0.325 | 0.258 (`bm25`) | +0.067 |
| Hit@5 | 0.567 | 0.592 (`bm25`) | -0.025 |
| Hit@10 | 0.767 | 0.708 (`bm25`) | +0.058 |
| MRR | 0.449 | 0.404 (`bm25`) | +0.045 |

### Test split by project

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full / DM (roster 105) | 24 | 0.500 | 0.542 | 0.667 | 0.542 | 0.667 | 0.534 | 0.833 | 23869.1 | 19524.6 | 39479.7 | 1.0141 |
| capgraph_full / EVG (roster 21) | 24 | 0.125 | 0.583 | 0.792 | 0.583 | 0.792 | 0.314 | 0.958 | 17328.3 | 15645.1 | 33122.8 | 0.7395 |
| capgraph_full / FAB (roster 62) | 24 | 0.375 | 0.583 | 0.750 | 0.583 | 0.750 | 0.480 | 0.958 | 19520.8 | 18691.4 | 24670.1 | 0.8167 |
| capgraph_full / MESOS (roster 67) | 24 | 0.292 | 0.583 | 0.833 | 0.583 | 0.833 | 0.460 | 0.917 | 21290.5 | 19354.7 | 35242.9 | 0.8683 |
| capgraph_full / TIMOB (roster 61) | 24 | 0.333 | 0.542 | 0.792 | 0.542 | 0.792 | 0.457 | 0.958 | 22560.7 | 21541.2 | 36020.7 | 0.9359 |
| capgraph_score / DM (roster 105) | 24 | 0.167 | 0.375 | 0.583 | 0.375 | 0.583 | 0.291 | 0.833 | 3322.7 | 3102.1 | 5732.5 | 0.0965 |
| capgraph_score / EVG (roster 21) | 24 | 0.167 | 0.708 | 0.958 | 0.708 | 0.958 | 0.397 | 0.958 | 2360.5 | 1812.7 | 4217.3 | 0.0868 |
| capgraph_score / FAB (roster 62) | 24 | 0.167 | 0.375 | 0.583 | 0.375 | 0.583 | 0.303 | 0.958 | 3080.2 | 2233.9 | 5729.7 | 0.0895 |
| capgraph_score / MESOS (roster 67) | 24 | 0.208 | 0.542 | 0.750 | 0.542 | 0.750 | 0.363 | 0.917 | 2834.8 | 2605.8 | 4490.9 | 0.0888 |
| capgraph_score / TIMOB (roster 61) | 24 | 0.083 | 0.417 | 0.667 | 0.417 | 0.667 | 0.242 | 0.958 | 2841.4 | 2619.6 | 5100.0 | 0.0898 |
| bm25 / DM (roster 105) | 24 | 0.333 | 0.667 | 0.750 | 0.667 | 0.750 | 0.445 | 1.000 | 5.9 | 1.9 | 4.5 | 0.0000 |
| bm25 / EVG (roster 21) | 24 | 0.167 | 0.667 | 0.750 | 0.667 | 0.750 | 0.361 | 1.000 | 1.3 | 0.6 | 1.9 | 0.0000 |
| bm25 / FAB (roster 62) | 24 | 0.375 | 0.750 | 0.833 | 0.750 | 0.833 | 0.547 | 1.000 | 3.1 | 1.1 | 2.8 | 0.0000 |
| bm25 / MESOS (roster 67) | 24 | 0.208 | 0.500 | 0.708 | 0.500 | 0.708 | 0.367 | 1.000 | 3.2 | 1.3 | 3.1 | 0.0000 |
| bm25 / TIMOB (roster 61) | 24 | 0.208 | 0.375 | 0.500 | 0.375 | 0.500 | 0.298 | 1.000 | 7.8 | 1.5 | 3.5 | 0.0000 |
| vector_only / DM (roster 105) | 24 | 0.167 | 0.542 | 0.625 | 0.542 | 0.625 | 0.363 | 1.000 | 50.5 | 43.0 | 90.7 | 0.0000 |
| vector_only / EVG (roster 21) | 24 | 0.083 | 0.500 | 0.792 | 0.500 | 0.792 | 0.318 | 1.000 | 43.7 | 42.1 | 58.1 | 0.0000 |
| vector_only / FAB (roster 62) | 24 | 0.333 | 0.583 | 0.750 | 0.583 | 0.750 | 0.481 | 1.000 | 45.7 | 43.5 | 53.9 | 0.0000 |
| vector_only / MESOS (roster 67) | 24 | 0.208 | 0.458 | 0.583 | 0.458 | 0.583 | 0.338 | 1.000 | 40.6 | 40.1 | 49.9 | 0.0000 |
| vector_only / TIMOB (roster 61) | 24 | 0.083 | 0.250 | 0.542 | 0.250 | 0.542 | 0.200 | 1.000 | 49.3 | 48.2 | 62.2 | 0.0000 |
| most_active / DM (roster 105) | 24 | 0.000 | 0.125 | 0.250 | 0.125 | 0.250 | 0.080 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / EVG (roster 21) | 24 | 0.208 | 0.750 | 0.750 | 0.750 | 0.750 | 0.425 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / FAB (roster 62) | 24 | 0.000 | 0.500 | 0.583 | 0.500 | 0.583 | 0.195 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / MESOS (roster 67) | 24 | 0.000 | 0.167 | 0.292 | 0.167 | 0.292 | 0.128 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / TIMOB (roster 61) | 24 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.045 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

### Test run diagnostics (graph system)

| Measure | Value |
|---|---|
| cases | 120 |
| multi role cases | 14 |
| llm calls | 255 |
| rerank entries min | 11 |
| rerank entries median | 15 |
| cases below ten ranked | 0 |
| candidate pool min | 11 |
| candidate pool median | 29 |
| candidate pool max | 56 |
| rejected rerank entries | 13 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry |

## Caveats

- **The target is assignee prediction.** Ranking the historical assignee first is evidence that the system finds relevant, recent, evidence-backed people — not that it found the *best* person. Several roster members may have been equally qualified; the dataset cannot say.
- **Hit@K must be read against roster size.** A 21-person roster (EVG) makes Hit@10 far easier than a 105-person one (DM), which is why every table is also broken down per project with its roster size.
- **Candidate recall is the graph system's ceiling.** Its Hit@K can never exceed the share of cases whose truth its retrieval union contained; the gap between the two is retrieval loss, not ranking loss, and the baselines have no such ceiling because they rank the entire roster.
- **A margin over BM25 is the honest bar.** BM25 over the same evidence is free and fast; a per-metric delta above is the only claim this benchmark supports, and it is reported in whichever direction it falls.
- **Public OSS Jira is not agency work.** Projects, vocabulary, and assignment practice all differ; the pipeline is domain-agnostic, the numbers are not.
- **Identities are project-qualified pseudonyms.** No cross-project identity is inferred, and no result here is usable for a real employment decision.
- **The re-rank is scored on the pool it was given.** Where it omitted or rejected a shortlisted person, that person is appended in deterministic score order, so the ablation compares ordering rather than coverage.

<!-- benchmark-v2 -->

# Benchmark v2

Generated 2026-08-12 against the same manifest `tawos-v1.1-benchmark-v1` and the same 150 cases as v1, under configuration digest `653bcac738e64feb`. The v1 tables above are unchanged; nothing in this section re-scores them.

Every lever was chosen on the 30 validation cases. The 120-case test split was run once, after the configuration below was frozen in `docs/benchmark-v2-config.md`. Its checkpoints live in `data/eval/v2/runs/`, separate from v1's.

The v1 column below is transcribed from the frozen v1 record (digest `01ac985a36e1bb63`). Because v2 changes engine defaults, regenerating the v1 half of this file with `make eval` would restate those tables' configuration against the current settings — the v1 half is a frozen artifact and should be left as written.

## v2 configuration

| Setting | v1 | v2 |
|---|---|---|
| Re-rank prompt | `rerank` | `rerank` (6865c7595caf) |
| Re-rank window | 15 | 15 |
| Score weights | specialization_match 0.4, skill_overlap 0.25, recency 0.2, evidence_strength 0.15 | specialization_match 0.25, skill_overlap 0.3, recency 0.4, evidence_strength 0.05 |
| Retrieval | vector top-40 ∪ structured top-40 | vector top-40 ∪ structured top-40 |
| Cost-log stages | `stage7_eval` | `stage7b_val` / `stage7b_test` |

## What this run showed

On the 120-case test split the adopted weighting **did not move the full system**: Hit@1 -0.017, Hit@5 +0.025, Hit@10 +0.008, MRR -0.004 — the largest of them 0.025, and all of them inside the run-to-run variance measured below.

It did move the deterministic arm, clearly and in the direction the offline sweep predicted: `capgraph_score` gained Hit@1 +0.017, Hit@5 +0.117, Hit@10 +0.067, MRR +0.047 on the same 120 cases, a 0.117 best-case gain that is several times the noise floor.

Both facts together are the finding, and it is not the one the work order expected. The weighting genuinely improved the ordering handed to the LLM re-rank, and the re-rank then produced the same end result it produced from the worse ordering. On this benchmark the re-rank, not the deterministic score, is what bounds the full system — so tuning the score buys little until the re-rank changes, and the one re-rank change tried here (an assignee-aligned prompt) scored below the prompt it replaced.

The practical consequence is the more useful result. `capgraph_score` now reaches Hit@5 0.600 and Hit@10 0.775 against the full system's 0.592 and 0.775, at $0.0038 per query against $0.0362 and 3.3s against 22.4s — 10x cheaper and 7x faster. The re-rank still earns its keep on Hit@1 and MRR, and it is what produces the cited reasons, but it is no longer what carries Hit@5 and Hit@10.

## v1 vs v2 — validation split

| System | Metric | v1 | v2 | Δ |
|---|---|---:|---:|---:|
| `capgraph_full` | Hit@1 | 0.367 | 0.433 | +0.067 |
| `capgraph_full` | Hit@5 | 0.733 | 0.767 | +0.033 |
| `capgraph_full` | Hit@10 | 0.833 | 0.833 | +0.000 |
| `capgraph_full` | MRR | 0.513 | 0.550 | +0.038 |
| `capgraph_full` | Candidate recall | 0.967 | 0.967 | +0.000 |
| `capgraph_score` | Hit@1 | 0.133 | 0.100 | -0.033 |
| `capgraph_score` | Hit@5 | 0.500 | 0.500 | +0.000 |
| `capgraph_score` | Hit@10 | 0.600 | 0.800 | +0.200 |
| `capgraph_score` | MRR | 0.300 | 0.327 | +0.027 |
| `capgraph_score` | Candidate recall | 0.967 | 0.967 | +0.000 |
| `bm25` | Hit@1 | 0.367 | 0.367 | +0.000 |
| `bm25` | Hit@5 | 0.567 | 0.567 | +0.000 |
| `bm25` | Hit@10 | 0.600 | 0.600 | +0.000 |
| `bm25` | MRR | 0.470 | 0.470 | +0.000 |
| `bm25` | Candidate recall | 1.000 | 1.000 | +0.000 |
| `vector_only` | Hit@1 | 0.133 | 0.133 | +0.000 |
| `vector_only` | Hit@5 | 0.367 | 0.367 | +0.000 |
| `vector_only` | Hit@10 | 0.600 | 0.600 | +0.000 |
| `vector_only` | MRR | 0.282 | 0.282 | +0.000 |
| `vector_only` | Candidate recall | 1.000 | 1.000 | +0.000 |
| `most_active` | Hit@1 | 0.067 | 0.067 | +0.000 |
| `most_active` | Hit@5 | 0.267 | 0.267 | +0.000 |
| `most_active` | Hit@10 | 0.333 | 0.333 | +0.000 |
| `most_active` | MRR | 0.177 | 0.177 | +0.000 |
| `most_active` | Candidate recall | 1.000 | 1.000 | +0.000 |

## v1 vs v2 — test split

| System | Metric | v1 | v2 | Δ |
|---|---|---:|---:|---:|
| `capgraph_full` | Hit@1 | 0.325 | 0.308 | -0.017 |
| `capgraph_full` | Hit@5 | 0.567 | 0.592 | +0.025 |
| `capgraph_full` | Hit@10 | 0.767 | 0.775 | +0.008 |
| `capgraph_full` | MRR | 0.449 | 0.445 | -0.004 |
| `capgraph_full` | Candidate recall | 0.925 | 0.925 | +0.000 |
| `capgraph_score` | Hit@1 | 0.158 | 0.175 | +0.017 |
| `capgraph_score` | Hit@5 | 0.483 | 0.600 | +0.117 |
| `capgraph_score` | Hit@10 | 0.708 | 0.775 | +0.067 |
| `capgraph_score` | MRR | 0.319 | 0.366 | +0.047 |
| `capgraph_score` | Candidate recall | 0.925 | 0.925 | +0.000 |
| `bm25` | Hit@1 | 0.258 | 0.258 | +0.000 |
| `bm25` | Hit@5 | 0.592 | 0.592 | +0.000 |
| `bm25` | Hit@10 | 0.708 | 0.708 | +0.000 |
| `bm25` | MRR | 0.404 | 0.404 | +0.000 |
| `bm25` | Candidate recall | 1.000 | 1.000 | +0.000 |
| `vector_only` | Hit@1 | 0.175 | 0.175 | +0.000 |
| `vector_only` | Hit@5 | 0.467 | 0.467 | +0.000 |
| `vector_only` | Hit@10 | 0.658 | 0.658 | +0.000 |
| `vector_only` | MRR | 0.340 | 0.340 | +0.000 |
| `vector_only` | Candidate recall | 1.000 | 1.000 | +0.000 |
| `most_active` | Hit@1 | 0.042 | 0.042 | +0.000 |
| `most_active` | Hit@5 | 0.308 | 0.308 | +0.000 |
| `most_active` | Hit@10 | 0.375 | 0.375 | +0.000 |
| `most_active` | MRR | 0.175 | 0.175 | +0.000 |
| `most_active` | Candidate recall | 1.000 | 1.000 | +0.000 |

### v2 validation split

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 30 | 30 | 0 |
| `capgraph_score` | 30 | 30 | 0 |
| `bm25` | 30 | 30 | 0 |
| `vector_only` | 30 | 30 | 0 |
| `most_active` | 30 | 30 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full | 30 | 0.433 | 0.767 | 0.833 | 0.767 | 0.833 | 0.550 | 0.967 | 18326.9 | 17063.8 | 34253.6 | 0.9960 |
| capgraph_score | 30 | 0.100 | 0.500 | 0.800 | 0.500 | 0.800 | 0.327 | 0.967 | 2743.9 | 2286.7 | 4707.8 | 0.1121 |
| bm25 | 30 | 0.367 | 0.567 | 0.600 | 0.567 | 0.600 | 0.470 | 1.000 | 12.2 | 1.3 | 83.3 | 0.0000 |
| vector_only | 30 | 0.133 | 0.367 | 0.600 | 0.367 | 0.600 | 0.282 | 1.000 | 50.2 | 50.5 | 66.7 | 0.0000 |
| most_active | 30 | 0.067 | 0.267 | 0.333 | 0.267 | 0.333 | 0.177 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

v2 validation run diagnostics (graph system):

| Measure | Value |
|---|---|
| cases | 30 |
| multi role cases | 1 |
| llm calls | 61 |
| rerank entries min | 10 |
| rerank entries median | 15 |
| cases below ten ranked | 0 |
| candidate pool min | 10 |
| candidate pool median | 41 |
| candidate pool max | 66 |
| rejected rerank entries | 1 |
| rejection reasons | not among the ranked candidates |

### v2 test split

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 120 | 120 | 0 |
| `capgraph_score` | 120 | 120 | 0 |
| `bm25` | 120 | 120 | 0 |
| `vector_only` | 120 | 120 | 0 |
| `most_active` | 120 | 120 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full | 120 | 0.308 | 0.592 | 0.775 | 0.592 | 0.775 | 0.445 | 0.925 | 22443.4 | 19026.6 | 41423.2 | 4.3484 |
| capgraph_score | 120 | 0.175 | 0.600 | 0.775 | 0.600 | 0.775 | 0.366 | 0.925 | 3291.5 | 2400.3 | 5226.5 | 0.4505 |
| bm25 | 120 | 0.258 | 0.592 | 0.708 | 0.592 | 0.708 | 0.404 | 1.000 | 5.0 | 1.6 | 5.6 | 0.0000 |
| vector_only | 120 | 0.175 | 0.467 | 0.658 | 0.467 | 0.658 | 0.340 | 1.000 | 59.1 | 55.6 | 90.5 | 0.0000 |
| most_active | 120 | 0.042 | 0.308 | 0.375 | 0.308 | 0.375 | 0.175 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

v2 test run diagnostics (graph system):

| Measure | Value |
|---|---|
| cases | 120 |
| multi role cases | 15 |
| llm calls | 255 |
| rerank entries min | 10 |
| rerank entries median | 15 |
| cases below ten ranked | 0 |
| candidate pool min | 10 |
| candidate pool median | 33 |
| candidate pool max | 57 |
| rejected rerank entries | 13 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry |

## Lever findings — rank-level (validation, offline from v1 checkpoints)

Every row below re-combines rankings the v1 run already produced, so the whole table cost nothing. It is reported whichever way it falls, and it falls against fusion.

| Variant | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall |
|---|---:|---:|---:|---:|---:|---:|
| capgraph_full (v1 reference) | 30 | 0.367 | 0.733 | 0.833 | 0.513 | 0.967 |
| rrf(capgraph_full, bm25) k=1 | 30 | 0.367 | 0.767 | 0.867 | 0.538 | 1.000 |
| rrf(capgraph_full, bm25) k=5 | 30 | 0.267 | 0.700 | 0.900 | 0.451 | 1.000 |
| rrf(capgraph_full, bm25) k=10 | 30 | 0.267 | 0.667 | 0.900 | 0.447 | 1.000 |
| rrf(capgraph_full, bm25) k=20 | 30 | 0.267 | 0.600 | 0.900 | 0.423 | 1.000 |
| rrf(capgraph_full, bm25) k=30 | 30 | 0.267 | 0.567 | 0.867 | 0.422 | 1.000 |
| rrf(capgraph_full, bm25) k=60 | 30 | 0.267 | 0.567 | 0.867 | 0.421 | 1.000 |
| rrf(capgraph_full, bm25) k=120 | 30 | 0.267 | 0.567 | 0.867 | 0.419 | 1.000 |
| rrf(capgraph_full, bm25) k=240 | 30 | 0.267 | 0.567 | 0.867 | 0.419 | 1.000 |
| rrf(capgraph_score, bm25) k=1 | 30 | 0.300 | 0.633 | 0.733 | 0.477 | 1.000 |
| rrf(capgraph_score, bm25) k=5 | 30 | 0.233 | 0.567 | 0.733 | 0.433 | 1.000 |
| rrf(capgraph_score, bm25) k=10 | 30 | 0.200 | 0.533 | 0.733 | 0.397 | 1.000 |
| rrf(capgraph_score, bm25) k=20 | 30 | 0.167 | 0.533 | 0.767 | 0.367 | 1.000 |
| rrf(capgraph_score, bm25) k=30 | 30 | 0.167 | 0.533 | 0.767 | 0.364 | 1.000 |
| rrf(capgraph_score, bm25) k=60 | 30 | 0.167 | 0.467 | 0.733 | 0.358 | 1.000 |
| rrf(capgraph_score, bm25) k=120 | 30 | 0.167 | 0.467 | 0.700 | 0.357 | 1.000 |
| rrf(capgraph_score, bm25) k=240 | 30 | 0.167 | 0.467 | 0.700 | 0.357 | 1.000 |
| rrf(capgraph_full x1, bm25) k=60 | 30 | 0.267 | 0.567 | 0.867 | 0.421 | 1.000 |
| rrf(capgraph_full x1.5, bm25) k=60 | 30 | 0.300 | 0.600 | 0.867 | 0.442 | 1.000 |
| rrf(capgraph_full x2, bm25) k=60 | 30 | 0.300 | 0.633 | 0.900 | 0.445 | 1.000 |
| rrf(capgraph_full x3, bm25) k=60 | 30 | 0.300 | 0.733 | 0.867 | 0.455 | 1.000 |
| rrf(capgraph_full, bm25, vector_only) k=60 | 30 | 0.233 | 0.533 | 0.800 | 0.395 | 1.000 |
| capgraph_full + roster backstop (person-id tail) | 30 | 0.367 | 0.733 | 0.833 | 0.514 | 1.000 |
| capgraph_full + roster backstop (bm25-ordered tail) | 30 | 0.367 | 0.733 | 0.833 | 0.514 | 1.000 |

## Lever findings — score weights (validation, offline from the component checkpoint)

Score components for all 30 validation cases are checkpointed in `data/eval/v2/scores/`, so every weight vector below was evaluated without a further model call. These are **score-only** metrics plus *window recall* — the share of cases whose truth reaches the re-rank window, which is the ceiling on the full system's Hit@K. A weight change cannot be credited with more than raising that ceiling until a paid run says otherwise.

The weighting was chosen from this table, not from the grid's best row: on 30 cases a 216-point grid has more than enough freedom to fit noise, but a component whose mean metric moves one way across the whole grid is a mechanism. Marginal effect of each component, averaged over every grid point that holds it at the given weight:

| Component | Weight | Mean MRR | Mean window recall |
|---|---:|---:|---:|
| `specialization_match` | 0.00 | 0.310 | 0.933 |
| `specialization_match` | 0.12 | 0.281 | 0.950 |
| `specialization_match` | 0.14 | 0.295 | 0.940 |
| `specialization_match` | 0.17 | 0.288 | 0.922 |
| `specialization_match` | 0.20 | 0.290 | 0.911 |
| `specialization_match` | 0.22 | 0.281 | 0.944 |
| `specialization_match` | 0.25 | 0.292 | 0.909 |
| `specialization_match` | 0.29 | 0.285 | 0.917 |
| `specialization_match` | 0.30 | 0.273 | 0.933 |
| `specialization_match` | 0.33 | 0.277 | 0.890 |
| `specialization_match` | 0.38 | 0.271 | 0.869 |
| `specialization_match` | 0.40 | 0.279 | 0.863 |
| `specialization_match` | 0.43 | 0.267 | 0.839 |
| `specialization_match` | 0.50 | 0.264 | 0.792 |
| `specialization_match` | 0.60 | 0.258 | 0.773 |
| `skill_overlap` | 0.00 | 0.287 | 0.895 |
| `skill_overlap` | 0.12 | 0.272 | 0.928 |
| `skill_overlap` | 0.14 | 0.274 | 0.913 |
| `skill_overlap` | 0.17 | 0.274 | 0.903 |
| `skill_overlap` | 0.20 | 0.277 | 0.896 |
| `skill_overlap` | 0.22 | 0.273 | 0.933 |
| `skill_overlap` | 0.25 | 0.282 | 0.893 |
| `skill_overlap` | 0.29 | 0.283 | 0.908 |
| `skill_overlap` | 0.30 | 0.278 | 0.933 |
| `skill_overlap` | 0.33 | 0.286 | 0.895 |
| `skill_overlap` | 0.38 | 0.285 | 0.903 |
| `skill_overlap` | 0.40 | 0.301 | 0.881 |
| `skill_overlap` | 0.43 | 0.290 | 0.881 |
| `skill_overlap` | 0.50 | 0.302 | 0.878 |
| `skill_overlap` | 0.60 | 0.319 | 0.867 |
| `recency` | 0.00 | 0.231 | 0.781 |
| `recency` | 0.12 | 0.249 | 0.850 |
| `recency` | 0.14 | 0.256 | 0.863 |
| `recency` | 0.17 | 0.263 | 0.875 |
| `recency` | 0.20 | 0.274 | 0.891 |
| `recency` | 0.22 | 0.273 | 0.928 |
| `recency` | 0.25 | 0.291 | 0.912 |
| `recency` | 0.29 | 0.289 | 0.956 |
| `recency` | 0.30 | 0.291 | 0.967 |
| `recency` | 0.33 | 0.304 | 0.945 |
| `recency` | 0.38 | 0.313 | 0.964 |
| `recency` | 0.40 | 0.333 | 0.950 |
| `recency` | 0.43 | 0.331 | 0.961 |
| `recency` | 0.50 | 0.343 | 0.959 |
| `recency` | 0.60 | 0.349 | 0.956 |
| `evidence_strength` | 0.00 | 0.346 | 0.909 |
| `evidence_strength` | 0.12 | 0.307 | 0.928 |
| `evidence_strength` | 0.14 | 0.302 | 0.913 |
| `evidence_strength` | 0.17 | 0.296 | 0.889 |
| `evidence_strength` | 0.20 | 0.291 | 0.889 |
| `evidence_strength` | 0.22 | 0.280 | 0.928 |
| `evidence_strength` | 0.25 | 0.282 | 0.888 |
| `evidence_strength` | 0.29 | 0.276 | 0.903 |
| `evidence_strength` | 0.30 | 0.269 | 0.933 |
| `evidence_strength` | 0.33 | 0.265 | 0.889 |
| `evidence_strength` | 0.38 | 0.257 | 0.903 |
| `evidence_strength` | 0.40 | 0.260 | 0.877 |
| `evidence_strength` | 0.43 | 0.251 | 0.894 |
| `evidence_strength` | 0.50 | 0.247 | 0.885 |
| `evidence_strength` | 0.60 | 0.242 | 0.872 |

The adopted vector against v1's, on identical retrieved pools:

| Weights | Hit@1 | Hit@5 | Hit@10 | MRR | Window recall |
|---|---:|---:|---:|---:|---:|
| v1: specialization_match 0.4, skill_overlap 0.25, recency 0.2, evidence_strength 0.15 | 0.133 | 0.400 | 0.667 | 0.280 | 0.900 |
| v2 (adopted): specialization_match 0.25, skill_overlap 0.3, recency 0.4, evidence_strength 0.05 | 0.167 | 0.467 | 0.833 | 0.319 | 0.967 |

## Lever findings — paid validation A/B (30 cases, `stage7b_val`)

Each arm changes exactly one thing against the arm above it: `ab_weights_only` changes only the score weights against the frozen v1 run, and `ab_weights_prompt` changes only the re-rank prompt against `ab_weights_only`. Every arm has its own checkpoint namespace, so no two configurations are ever scored together.

| Arm | System | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | Cost (USD) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| v1 (frozen) | `capgraph_full` | 30 | 0.367 | 0.733 | 0.833 | 0.513 | 0.967 | 1.0857 |
| v1 (frozen) | `capgraph_score` | 30 | 0.133 | 0.500 | 0.600 | 0.300 | 0.967 | 0.1139 |
| ab_weights_only | `capgraph_full` | 30 | 0.433 | 0.767 | 0.833 | 0.550 | 0.967 | 0.9960 |
| ab_weights_only | `capgraph_score` | 30 | 0.100 | 0.500 | 0.800 | 0.327 | 0.967 | 0.1121 |
| ab_weights_prompt | `capgraph_full` | 30 | 0.367 | 0.700 | 0.800 | 0.489 | 0.967 | 1.1244 |
| ab_weights_prompt | `capgraph_score` | 30 | 0.167 | 0.500 | 0.733 | 0.334 | 0.967 | 0.1169 |

Read these against the measured run-to-run variance below, not as exact quantities: 30 cases is 30 coin flips wide.

## Run-to-run variance on 30 cases (measured, not assumed)

The intent parse is a model call, so two runs of the *same* configuration do not retrieve the same candidates. Re-running retrieval reproduced 8 of the 30 v1 candidate pools exactly. The three baselines, which make no model call, reproduced every ranking byte for byte in the v2 namespace — so the variance below is the LLM path, not the harness.

`capgraph_score` under **v1's own weights**, run twice:

| Metric | v1 run | re-run | Δ |
|---|---:|---:|---:|
| Hit@1 | 0.133 | 0.133 | +0.000 |
| Hit@5 | 0.500 | 0.400 | -0.100 |
| Hit@10 | 0.600 | 0.667 | +0.067 |
| MRR | 0.300 | 0.280 | -0.020 |

The largest swing from changing nothing is **0.100**. Any lever above whose validation effect is smaller than that has not been shown to work — which is why each was adopted or rejected on a mechanism visible across a whole sweep rather than on one table's best row, and why the 120-case test split is the only number here worth quoting on its own.

## Spend

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `stage7b_val` | 153 | 2.2341 |
| `stage7b_test` | 255 | 4.3484 |
| **total** | | **6.5825** |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included.

<!-- benchmark-v3 -->

# Benchmark v3

Generated 2026-08-12 against the same manifest `tawos-v1.1-benchmark-v1` and the same 150 cases as v1 and v2, under configuration digest `0ac4b022ee72816a`. The v1 and v2 tables above are unchanged; nothing in this section re-scores them.

Every lever was chosen on the 30 validation cases. The 120-case test split was run once, after the configuration below was frozen in `docs/benchmark-v3-config.md`. Its checkpoints live in `data/eval/v3/runs/`, separate from v1's and v2's. This is the third and last exposure of this manifest's test split.

The v2 column is transcribed from the frozen v2 record (digest `653bcac738e64feb`) and re-scored from its checkpoints, not re-run.

## v3 configuration

| Setting | v2 | v3 |
|---|---|---|
| Re-rank prompt | `rerank` | `rerank_cards` (20aa66afc9d8) |
| Candidate view | `profile` | `card` |
| Re-rank window | 15 | 32 |
| Retrieval | vector top-40 ∪ structured top-40 | vector top-40 ∪ structured top-40 ∪ BM25 top-10 |
| Re-rank samples | 1 | 1 |
| Strong-model finisher | off | off |
| Score weights | unchanged | specialization_match 0.25, skill_overlap 0.3, recency 0.4, evidence_strength 0.05 |
| Cost-log stages | `stage7b_val` / `stage7b_test` | `stage7c_val` / `stage7c_test` |

## What this run showed

On the 120-case test split the adopted configuration **did not move the full system beyond the measured noise floor**: Hit@1 -0.083, Hit@5 +0.033, Hit@10 +0.058, MRR -0.033 — the largest of them 0.083, against the 0.100 run-to-run variance the v2 section measured from changing nothing.

The aggregate is not the whole story, and here it is the more forgiving half. Case by case, the sharpest movement is **Hit@1 -0.083**, from 6 cases v3 wins and 16 it loses (McNemar exact p = 0.052); that is the closest this study comes to a significant result, and it is a regression. Nothing here was tuned on these cases, so it is a measurement rather than a fit — but it is one paired comparison on one split, and the deltas around it are not distinguishable from noise.

Candidate recall moved 0.925 -> 0.975. That is the lexical arm doing exactly and only what it was adopted for: it raises the ceiling on what the graph system can reach, and the wider window is what lets the re-rank see the candidates underneath it. Whether the re-rank then ranks them well is a separate question, and it is the one the deltas above answer.

The deterministic arm moved Hit@1 -0.017, Hit@5 -0.042, Hit@10 +0.033, MRR -0.006. No v3 lever touches it — the score arm ranks the whole pool and never sees a prompt, a window, or a sample — so its movement is the lexical arm's extra candidates plus the run-to-run variance of re-retrieving, and it is the fairest available gauge of how much of the full system's movement is noise.

Cost per query: $0.0316 for the full system against $0.0037 for the deterministic arm, at 30.1s against 2.8s.

## v1 vs v2 vs v3 — validation split

| System | Metric | v1 | v2 | v3 | Δ v3−v2 |
|---|---|---:|---:|---:|---:|
| `capgraph_full` | Hit@1 | 0.367 | 0.433 | 0.400 | -0.033 |
| `capgraph_full` | Hit@5 | 0.733 | 0.767 | 0.633 | -0.133 |
| `capgraph_full` | Hit@10 | 0.833 | 0.833 | 0.867 | +0.033 |
| `capgraph_full` | MRR | 0.513 | 0.550 | 0.523 | -0.027 |
| `capgraph_full` | Candidate recall | 0.967 | 0.967 | 1.000 | +0.033 |
| `capgraph_score` | Hit@1 | 0.133 | 0.100 | 0.100 | +0.000 |
| `capgraph_score` | Hit@5 | 0.500 | 0.500 | 0.567 | +0.067 |
| `capgraph_score` | Hit@10 | 0.600 | 0.800 | 0.800 | +0.000 |
| `capgraph_score` | MRR | 0.300 | 0.327 | 0.312 | -0.015 |
| `capgraph_score` | Candidate recall | 0.967 | 0.967 | 1.000 | +0.033 |
| `bm25` | Hit@1 | 0.367 | 0.367 | 0.367 | +0.000 |
| `bm25` | Hit@5 | 0.567 | 0.567 | 0.567 | +0.000 |
| `bm25` | Hit@10 | 0.600 | 0.600 | 0.600 | +0.000 |
| `bm25` | MRR | 0.470 | 0.470 | 0.470 | +0.000 |
| `bm25` | Candidate recall | 1.000 | 1.000 | 1.000 | +0.000 |
| `vector_only` | Hit@1 | 0.133 | 0.133 | 0.133 | +0.000 |
| `vector_only` | Hit@5 | 0.367 | 0.367 | 0.367 | +0.000 |
| `vector_only` | Hit@10 | 0.600 | 0.600 | 0.600 | +0.000 |
| `vector_only` | MRR | 0.282 | 0.282 | 0.282 | +0.000 |
| `vector_only` | Candidate recall | 1.000 | 1.000 | 1.000 | +0.000 |
| `most_active` | Hit@1 | 0.067 | 0.067 | 0.067 | +0.000 |
| `most_active` | Hit@5 | 0.267 | 0.267 | 0.267 | +0.000 |
| `most_active` | Hit@10 | 0.333 | 0.333 | 0.333 | +0.000 |
| `most_active` | MRR | 0.177 | 0.177 | 0.177 | +0.000 |
| `most_active` | Candidate recall | 1.000 | 1.000 | 1.000 | +0.000 |

## v1 vs v2 vs v3 — test split

| System | Metric | v1 | v2 | v3 | Δ v3−v2 |
|---|---|---:|---:|---:|---:|
| `capgraph_full` | Hit@1 | 0.325 | 0.308 | 0.225 | -0.083 |
| `capgraph_full` | Hit@5 | 0.567 | 0.592 | 0.625 | +0.033 |
| `capgraph_full` | Hit@10 | 0.767 | 0.775 | 0.833 | +0.058 |
| `capgraph_full` | MRR | 0.449 | 0.445 | 0.413 | -0.033 |
| `capgraph_full` | Candidate recall | 0.925 | 0.925 | 0.975 | +0.050 |
| `capgraph_score` | Hit@1 | 0.158 | 0.175 | 0.158 | -0.017 |
| `capgraph_score` | Hit@5 | 0.483 | 0.600 | 0.558 | -0.042 |
| `capgraph_score` | Hit@10 | 0.708 | 0.775 | 0.808 | +0.033 |
| `capgraph_score` | MRR | 0.319 | 0.366 | 0.360 | -0.006 |
| `capgraph_score` | Candidate recall | 0.925 | 0.925 | 0.975 | +0.050 |
| `bm25` | Hit@1 | 0.258 | 0.258 | 0.258 | +0.000 |
| `bm25` | Hit@5 | 0.592 | 0.592 | 0.592 | +0.000 |
| `bm25` | Hit@10 | 0.708 | 0.708 | 0.708 | +0.000 |
| `bm25` | MRR | 0.404 | 0.404 | 0.404 | +0.000 |
| `bm25` | Candidate recall | 1.000 | 1.000 | 1.000 | +0.000 |
| `vector_only` | Hit@1 | 0.175 | 0.175 | 0.175 | +0.000 |
| `vector_only` | Hit@5 | 0.467 | 0.467 | 0.467 | +0.000 |
| `vector_only` | Hit@10 | 0.658 | 0.658 | 0.658 | +0.000 |
| `vector_only` | MRR | 0.340 | 0.340 | 0.340 | +0.000 |
| `vector_only` | Candidate recall | 1.000 | 1.000 | 1.000 | +0.000 |
| `most_active` | Hit@1 | 0.042 | 0.042 | 0.042 | +0.000 |
| `most_active` | Hit@5 | 0.308 | 0.308 | 0.308 | +0.000 |
| `most_active` | Hit@10 | 0.375 | 0.375 | 0.375 | +0.000 |
| `most_active` | MRR | 0.175 | 0.175 | 0.175 | +0.000 |
| `most_active` | Candidate recall | 1.000 | 1.000 | 1.000 | +0.000 |

### v3 validation split

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 30 | 30 | 0 |
| `capgraph_score` | 30 | 30 | 0 |
| `bm25` | 30 | 30 | 0 |
| `vector_only` | 30 | 30 | 0 |
| `most_active` | 30 | 30 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full | 30 | 0.400 | 0.633 | 0.867 | 0.633 | 0.867 | 0.523 | 1.000 | 32005.4 | 29309.2 | 67109.5 | 0.8832 |
| capgraph_score | 30 | 0.100 | 0.567 | 0.800 | 0.567 | 0.800 | 0.312 | 1.000 | 3687.8 | 3364.2 | 7888.7 | 0.1144 |
| bm25 | 30 | 0.367 | 0.567 | 0.600 | 0.567 | 0.600 | 0.470 | 1.000 | 11.5 | 1.0 | 81.0 | 0.0000 |
| vector_only | 30 | 0.133 | 0.367 | 0.600 | 0.367 | 0.600 | 0.282 | 1.000 | 172.1 | 16.0 | 32.3 | 0.0000 |
| most_active | 30 | 0.067 | 0.267 | 0.333 | 0.267 | 0.333 | 0.177 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

v3 validation run diagnostics (graph system):

| Measure | Value |
|---|---|
| cases | 30 |
| multi role cases | 1 |
| llm calls | 61 |
| rerank entries min | 12 |
| rerank entries median | 32 |
| cases below ten ranked | 0 |
| candidate pool min | 12 |
| candidate pool median | 35 |
| candidate pool max | 52 |
| rejected rerank entries | 13 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry |

### v3 test split

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 120 | 120 | 0 |
| `capgraph_score` | 120 | 120 | 0 |
| `bm25` | 120 | 120 | 0 |
| `vector_only` | 120 | 120 | 0 |
| `most_active` | 120 | 120 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full | 120 | 0.225 | 0.625 | 0.833 | 0.625 | 0.833 | 0.413 | 0.975 | 30118.4 | 27310.3 | 54142.8 | 3.7929 |
| capgraph_score | 120 | 0.158 | 0.558 | 0.808 | 0.558 | 0.808 | 0.360 | 0.975 | 2832.0 | 2361.7 | 5095.4 | 0.4499 |
| bm25 | 120 | 0.258 | 0.592 | 0.708 | 0.592 | 0.708 | 0.404 | 1.000 | 3.6 | 0.8 | 2.6 | 0.0000 |
| vector_only | 120 | 0.175 | 0.467 | 0.658 | 0.467 | 0.658 | 0.340 | 1.000 | 52.0 | 14.0 | 19.9 | 0.0000 |
| most_active | 120 | 0.042 | 0.308 | 0.375 | 0.308 | 0.375 | 0.175 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

v3 test run diagnostics (graph system):

| Measure | Value |
|---|---|
| cases | 120 |
| multi role cases | 16 |
| llm calls | 256 |
| rerank entries min | 12 |
| rerank entries median | 32 |
| cases below ten ranked | 0 |
| candidate pool min | 12 |
| candidate pool median | 33 |
| candidate pool max | 68 |
| rejected rerank entries | 46 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry, not among the ranked candidates |

## Paired per-query statistics — validation split

Aggregates hide the pairing: these runs answer the same cases, so what matters is which cases moved. **Wins** are cases v3 got right and v2 did not, **losses** the reverse. McNemar's exact test uses only those discordant cases; the MRR row uses a case-level bootstrap, which keeps the pairing because each resample draws the same case for both arms.

`capgraph_full`, v3 against v2 on the 30 cases both scored:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 30 | 0.433 | 0.400 | -0.033 | 4 | 5 | 21 | McNemar exact p = 1.000 |
| Hit@5 | 30 | 0.767 | 0.633 | -0.133 | 2 | 6 | 22 | McNemar exact p = 0.289 |
| Hit@10 | 30 | 0.833 | 0.867 | +0.033 | 4 | 3 | 23 | McNemar exact p = 1.000 |
| MRR | 30 | 0.550 | 0.523 | -0.027 | 12 | 10 | 8 | 95% bootstrap CI [-0.183, +0.126] |

`capgraph_score`, v3 against v2 on the 30 cases both scored:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 30 | 0.100 | 0.100 | +0.000 | 1 | 1 | 28 | McNemar exact p = 1.000 |
| Hit@5 | 30 | 0.500 | 0.567 | +0.067 | 4 | 2 | 24 | McNemar exact p = 0.688 |
| Hit@10 | 30 | 0.800 | 0.800 | +0.000 | 4 | 4 | 22 | McNemar exact p = 1.000 |
| MRR | 30 | 0.327 | 0.312 | -0.015 | 11 | 9 | 10 | 95% bootstrap CI [-0.094, +0.059] |

## Paired per-query statistics — test split

Aggregates hide the pairing: these runs answer the same cases, so what matters is which cases moved. **Wins** are cases v3 got right and v2 did not, **losses** the reverse. McNemar's exact test uses only those discordant cases; the MRR row uses a case-level bootstrap, which keeps the pairing because each resample draws the same case for both arms.

`capgraph_full`, v3 against v2 on the 120 cases both scored:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 120 | 0.308 | 0.225 | -0.083 | 6 | 16 | 98 | McNemar exact p = 0.052 |
| Hit@5 | 120 | 0.592 | 0.625 | +0.033 | 18 | 14 | 88 | McNemar exact p = 0.597 |
| Hit@10 | 120 | 0.775 | 0.833 | +0.058 | 12 | 5 | 103 | McNemar exact p = 0.143 |
| MRR | 120 | 0.445 | 0.413 | -0.033 | 50 | 37 | 33 | 95% bootstrap CI [-0.094, +0.023] |

`capgraph_score`, v3 against v2 on the 120 cases both scored:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 120 | 0.175 | 0.158 | -0.017 | 2 | 4 | 114 | McNemar exact p = 0.688 |
| Hit@5 | 120 | 0.600 | 0.558 | -0.042 | 10 | 15 | 95 | McNemar exact p = 0.424 |
| Hit@10 | 120 | 0.775 | 0.808 | +0.033 | 12 | 8 | 100 | McNemar exact p = 0.503 |
| MRR | 120 | 0.366 | 0.360 | -0.006 | 26 | 39 | 55 | 95% bootstrap CI [-0.041, +0.028] |

## Lever findings — paid validation A/B (30 cases, `stage7c_val`)

Each arm changes exactly one thing against the arm above it, and each has its own checkpoint namespace, so no two configurations are ever scored together. Read every delta against the 0.100 run-to-run noise floor measured in the v2 section and re-measured below: on 30 cases none of these rows is individually significant, which is why adoption rests on the construction-level mechanisms recorded in `docs/benchmark-v3-config.md` — candidate recall, window recall, and citation validity — rather than on this table.

| Arm | Verdict | System | N | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall | Cost (USD) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| v2 (frozen) | baseline | `capgraph_full` | 30 | 0.433 | 0.767 | 0.833 | 0.550 | 0.967 | 0.9960 |
| v2 (frozen) | baseline | `capgraph_score` | 30 | 0.100 | 0.500 | 0.800 | 0.327 | 0.967 | 0.1121 |
| ab_lexical | in the adopted configuration | `capgraph_full` | 30 | 0.400 | 0.667 | 0.800 | 0.519 | 1.000 | 1.0248 |
| ab_lexical | in the adopted configuration | `capgraph_score` | 30 | 0.133 | 0.600 | 0.733 | 0.329 | 1.000 | 0.1143 |
| ab_cards | in the adopted configuration | `capgraph_full` | 30 | 0.300 | 0.733 | 0.767 | 0.452 | 1.000 | 0.6310 |
| ab_cards | in the adopted configuration | `capgraph_score` | 30 | 0.067 | 0.533 | 0.700 | 0.279 | 1.000 | 0.1118 |
| ab_window32 | adopted | `capgraph_full` | 30 | 0.400 | 0.633 | 0.867 | 0.523 | 1.000 | 0.8832 |
| ab_window32 | adopted | `capgraph_score` | 30 | 0.100 | 0.567 | 0.800 | 0.312 | 1.000 | 0.1144 |
| ab_selfconsistency | measured, not adopted | `capgraph_full` | 30 | 0.267 | 0.633 | 0.833 | 0.443 | 1.000 | 2.7174 |
| ab_selfconsistency | measured, not adopted | `capgraph_score` | 30 | 0.133 | 0.533 | 0.733 | 0.318 | 1.000 | 0.1152 |
| ab_finisher | measured, not adopted | `capgraph_full` | 30 | 0.333 | 0.767 | 0.867 | 0.498 | 1.000 | 1.4718 |
| ab_finisher | measured, not adopted | `capgraph_score` | 30 | 0.133 | 0.533 | 0.800 | 0.325 | 1.000 | 0.1141 |

## The noise gauge, measured inside this study

Every arm re-runs the whole pipeline and the intent parse is a model call, so no two arms retrieve the same candidate pools: an arm-to-arm delta is a lever plus a fresh draw of run-to-run variance. The deterministic `capgraph_score` arm ranks the entire pool and never sees a prompt, a window, or a sample, so across the arms below — which change only those three things — whatever it moves by is noise and nothing else.

| Comparison (nothing in it can move the score arm) | Hit@1 | Hit@5 | Hit@10 | MRR |
|---|---:|---:|---:|---:|
| `ab_lexical` → `ab_cards` | -0.067 | -0.067 | -0.033 | -0.050 |
| `ab_cards` → `ab_window32` | +0.033 | +0.033 | +0.100 | +0.034 |
| `ab_window32` → `ab_selfconsistency` | +0.033 | -0.033 | -0.067 | +0.006 |
| `ab_window32` → `ab_finisher` | +0.033 | -0.033 | +0.000 | +0.013 |

The largest such swing is **0.100**, reproducing from a different direction the 0.100 run-to-run floor the v2 section measured by re-running one configuration twice. No v3 lever was adopted or rejected on a delta smaller than that.

## Re-rank citation validity — a correction to the frozen record

`docs/benchmark-v3-config.md` adopted the card view partly because citation rejections fell from 8 to 0 between `ab_lexical` and `ab_cards`. That measurement is real and is reproduced below — and it did not survive the wider window. Rejections are counted per entry the re-rank was actually offered, because a window of 32 gives the model twice as many chances to mis-cite as a window of 15 and the raw counts are not comparable.

| Run | Split | Rejected entries | Entries offered | Rate |
|---|---|---:|---:|---:|
| v2 (window 15, profile view) | validation | 1 | 448 | 0.22% |
| v2 (window 15, profile view) | test | 13 | 1997 | 0.65% |
| `ab_lexical` (window 15, profile view) | validation | 8 | 468 | 1.71% |
| `ab_cards` (window 15, card view) | validation | 0 | 471 | 0.00% |
| v3 frozen (window 32, card view) | validation | 13 | 865 | 1.50% |
| v3 frozen (window 32, card view) | test | 46 | 3677 | 1.25% |

On the test split the adopted configuration rejects 1.25% of the entries it is offered, above v2's 0.65%. The card removed mis-citation at a window of 15 and did not hold it at 32, so the honest reading is that the card's validity benefit is a window-15 effect and the frozen configuration does not inherit it. What the card did deliver, and what the test split confirms, is cost: the frozen run spent $3.7929 against v2's $4.3484 while showing the model twice as many candidates. Every rejected entry is still discarded rather than repaired, and the person is re-appended in deterministic score order, so no unevidenced claim reaches a shortlist in either configuration.

## Label-noise audit (test split, frozen run — reporting, not a lever)

The triage literature reports the recorded assignee differing from the person who did the work in roughly a fifth of issues, and label cleaning moving MRR by a comparable amount. This benchmark already reconstructs its truth at the safe resolution boundary rather than taking the dump's final assignee snapshot, so the question here is what is *left*: does the audit-only snapshot name someone else, and does the system miss disproportionately on the cases whose label is weaker?

| Measure | Value |
|---|---:|
| Test cases scored | 120 |
| Truth later reassigned (final snapshot names someone else) | 0 |
| Truth corroborated by a recorded assignment event at resolution | 58 |
| Truth from the final snapshot, assignment time unknown | 62 |
| Cases with no audit row | 0 |
| Cases where "truth OR final-snapshot assignee" widens the accepted set | 0 |

`capgraph_full` by label provenance:

| Truth provenance | N | Hit@1 | Hit@5 | Hit@10 | MRR | Hit@5 misses |
|---|---:|---:|---:|---:|---:|---:|
| assignment event recorded at resolution | 58 | 0.207 | 0.638 | 0.828 | 0.414 | 21 |
| no assignment event recorded (final snapshot, timing unknown) | 62 | 0.242 | 0.613 | 0.839 | 0.412 | 24 |

## Spend

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `stage7c_val` | 447 | 6.9926 |
| `stage7c_test` | 256 | 3.7929 |
| **total** | | **10.7855** |

| Call type | Calls | Cost (USD) |
|---|---:|---:|
| `finish` | 35 | 0.6777 |
| `intent` | 303 | 1.1440 |
| `rerank` | 365 | 8.9638 |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included, against the $25.00 ceiling the owner authorized on 2026-08-12.

<!-- benchmark-v4 -->

# Benchmark v4 — work packages, multi-person truth

Generated 2026-08-15 against manifest `tawos-v1.1-benchmark-v4`, configuration digest `5fa012ecb5a76aef`.

**This is a different instrument, not a fourth tuning round. Nothing below is comparable to a v1-v3 row.** A v1 case was one issue, asked at its creation time, whose truth was the one person who resolved it. A v4 case is one **work package** — a sprint — asked at its recorded start date, whose brief is a cheap-model rewrite of the issues planned into it before it started, and whose truth is **everyone** who resolved any of its issues from that moment on. Different briefs, different labels, different cases, different projects in the mix. The v1-v3 sections above are untouched and stay quotable on their own terms.

Grouping-unit verification, leakage guards, and the full exclusion accounting are in `docs/benchmark-v4-manifest.md`.

## Configuration

| Setting | Value |
|---|---|
| Grouping unit | sprint (as-of = recorded sprint start) |
| Manifest | `tawos-v1.1-benchmark-v4`, seed 20260814 |
| Brief rewrite | `openai/gpt-5.6-luna` (896160431cd9), frozen in the manifest |
| Engine configurations | `v2frozen`, `v3frozen` |
| Intent / re-rank model | `openai/gpt-5.6-terra` / `openai/gpt-5.6-terra` |
| Embedding model | `BAAI/bge-small-en-v1.5` |
| Cost-log stages | `bench4_rewrite`, `bench4_val`, `bench4_test` |

## The manifest

`tawos-v1.1-benchmark-v4`, seed 20260814, 150 packages selected from 1061 sprint candidates: test 122, validation 28. Per project: DM 79, FAB 5, MESOS 32, TIMOB 34.

| Exclusion reason | Packages |
|---|---:|
| `no_truth_resolver` | 1 |
| `nothing_planned_before_start` | 115 |
| `sampled_out` | 7 |
| `sprint_start_missing` | 10 |
| `sprint_start_not_post_cutoff` | 760 |
| `too_few_brief_issues` | 18 |

Truth sets hold 631 person-slots across 150 cases (median 4 people per package, range 1-11). 502 further people who resolved package issues were dropped because they were not eligible in the roster frozen at the cutoff — in v1 an ineligible truth person discarded the whole case (4,992 of them); here it narrows the truth set and is counted.

| Truth-set size | Packages |
|---|---:|
| 1 | 33 |
| 2 | 27 |
| 3 | 11 |
| 4 | 8 |
| 5 | 14 |
| 6 | 24 |
| 7 | 12 |
| 8 | 12 |
| 9 | 6 |
| 10 | 2 |
| 11 | 1 |

## Recall@K against Hit@K — validation split, `v3frozen`

In v1-v3 each case had one truth person, so Recall@K *was* Hit@K by construction. Here a package has several, and the two answer different questions: Hit@K asks whether the shortlist found **anyone** who worked the package, Recall@K asks **what share** of them it found. The gap is the whole point of the rebuild.

| System | N | Hit@5 | Recall@5 | Δ | Hit@10 | Recall@10 | Δ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `capgraph_full` | 28 | 0.571 | 0.313 | -0.259 | 0.714 | 0.549 | -0.165 |
| `capgraph_score` | 28 | 0.536 | 0.254 | -0.281 | 0.714 | 0.502 | -0.213 |
| `bm25` | 28 | 0.500 | 0.178 | -0.322 | 0.571 | 0.246 | -0.325 |
| `vector_only` | 28 | 0.321 | 0.111 | -0.210 | 0.464 | 0.236 | -0.228 |
| `most_active` | 28 | 0.571 | 0.402 | -0.170 | 0.679 | 0.447 | -0.231 |

## The graph system against BM25 — validation split, `v3frozen`

The v1 benchmark's single-ticket briefs were jargon-dense and narrow, which is the shape BM25 is best at; the backlog's hypothesis (G12) was that broader, staffing-shaped briefs would change that comparison. On this split **the graph system leads BM25 on 6 of 6 metrics**. Reported in whichever direction it falls — a negative result here is a finding about the instrument, not a failure of it.

| Metric | capgraph_full | bm25 | Δ |
|---|---:|---:|---:|
| Hit@1 | 0.393 | 0.179 | +0.214 |
| Hit@5 | 0.571 | 0.500 | +0.071 |
| Hit@10 | 0.714 | 0.571 | +0.143 |
| Recall@5 | 0.313 | 0.178 | +0.134 |
| Recall@10 | 0.549 | 0.246 | +0.303 |
| MRR | 0.481 | 0.312 | +0.169 |

BM25 is not automatically the bar to clear, though, and on multi-person truth it stops being the strongest baseline. Against the **best baseline on each metric** — chosen per column, not fixed in advance, so a weak result cannot hide behind a baseline it happens to beat:

| Metric | capgraph_full | Best baseline | Δ |
|---|---:|---|---:|
| Hit@1 | 0.393 | 0.179 (`bm25`) | +0.214 |
| Hit@5 | 0.571 | 0.571 (`most_active`) | +0.000 |
| Hit@10 | 0.714 | 0.679 (`most_active`) | +0.036 |
| Recall@5 | 0.313 | 0.402 (`most_active`) | -0.089 |
| Recall@10 | 0.549 | 0.447 (`most_active`) | +0.102 |
| MRR | 0.481 | 0.312 (`bm25`) | +0.169 |

Both arms answered the same cases, so the aggregate is not the whole evidence. Case by case, against BM25:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.179 | 0.393 | +0.214 | 7 | 1 | 20 | McNemar exact p = 0.070 |
| Hit@5 | 28 | 0.500 | 0.571 | +0.071 | 3 | 1 | 24 | McNemar exact p = 0.625 |
| Hit@10 | 28 | 0.571 | 0.714 | +0.143 | 4 | 0 | 24 | McNemar exact p = 0.125 |
| Recall@5 | 28 | 0.178 | 0.313 | +0.134 | 10 | 2 | 16 | 95% bootstrap CI [+0.031, +0.245] |
| Recall@10 | 28 | 0.246 | 0.549 | +0.303 | 15 | 2 | 11 | 95% bootstrap CI [+0.167, +0.440] |
| MRR | 28 | 0.312 | 0.481 | +0.169 | 13 | 7 | 8 | 95% bootstrap CI [+0.042, +0.304] |

## Recall@K against Hit@K — test split, `v3frozen`

In v1-v3 each case had one truth person, so Recall@K *was* Hit@K by construction. Here a package has several, and the two answer different questions: Hit@K asks whether the shortlist found **anyone** who worked the package, Recall@K asks **what share** of them it found. The gap is the whole point of the rebuild.

| System | N | Hit@5 | Recall@5 | Δ | Hit@10 | Recall@10 | Δ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `capgraph_full` | 122 | 0.754 | 0.396 | -0.358 | 0.803 | 0.597 | -0.207 |
| `capgraph_score` | 122 | 0.705 | 0.323 | -0.382 | 0.820 | 0.539 | -0.281 |
| `bm25` | 122 | 0.631 | 0.254 | -0.377 | 0.721 | 0.411 | -0.310 |
| `vector_only` | 122 | 0.516 | 0.177 | -0.340 | 0.689 | 0.327 | -0.362 |
| `most_active` | 122 | 0.566 | 0.284 | -0.282 | 0.697 | 0.343 | -0.353 |

## The graph system against BM25 — test split, `v3frozen`

The v1 benchmark's single-ticket briefs were jargon-dense and narrow, which is the shape BM25 is best at; the backlog's hypothesis (G12) was that broader, staffing-shaped briefs would change that comparison. On this split **the graph system leads BM25 on 6 of 6 metrics**. Reported in whichever direction it falls — a negative result here is a finding about the instrument, not a failure of it.

| Metric | capgraph_full | bm25 | Δ |
|---|---:|---:|---:|
| Hit@1 | 0.508 | 0.303 | +0.205 |
| Hit@5 | 0.754 | 0.631 | +0.123 |
| Hit@10 | 0.803 | 0.721 | +0.082 |
| Recall@5 | 0.396 | 0.254 | +0.142 |
| Recall@10 | 0.597 | 0.411 | +0.185 |
| MRR | 0.622 | 0.459 | +0.163 |

BM25 is not automatically the bar to clear, though, and on multi-person truth it stops being the strongest baseline. Against the **best baseline on each metric** — chosen per column, not fixed in advance, so a weak result cannot hide behind a baseline it happens to beat:

| Metric | capgraph_full | Best baseline | Δ |
|---|---:|---|---:|
| Hit@1 | 0.508 | 0.303 (`bm25`) | +0.205 |
| Hit@5 | 0.754 | 0.631 (`bm25`) | +0.123 |
| Hit@10 | 0.803 | 0.721 (`bm25`) | +0.082 |
| Recall@5 | 0.396 | 0.284 (`most_active`) | +0.112 |
| Recall@10 | 0.597 | 0.411 (`bm25`) | +0.185 |
| MRR | 0.622 | 0.459 (`bm25`) | +0.163 |

Both arms answered the same cases, so the aggregate is not the whole evidence. Case by case, against BM25:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 122 | 0.303 | 0.508 | +0.205 | 31 | 6 | 85 | McNemar exact p = 0.000 |
| Hit@5 | 122 | 0.631 | 0.754 | +0.123 | 18 | 3 | 101 | McNemar exact p = 0.001 |
| Hit@10 | 122 | 0.721 | 0.803 | +0.082 | 14 | 4 | 104 | McNemar exact p = 0.031 |
| Recall@5 | 122 | 0.254 | 0.396 | +0.142 | 57 | 17 | 48 | 95% bootstrap CI [+0.088, +0.197] |
| Recall@10 | 122 | 0.411 | 0.597 | +0.185 | 63 | 19 | 40 | 95% bootstrap CI [+0.116, +0.256] |
| MRR | 122 | 0.459 | 0.622 | +0.163 | 64 | 23 | 35 | 95% bootstrap CI [+0.104, +0.223] |

### validation split — `v2frozen`, rewritten briefs

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 28 | 28 | 0 |
| `capgraph_score` | 28 | 28 | 0 |
| `bm25` | 28 | 28 | 0 |
| `vector_only` | 28 | 28 | 0 |
| `most_active` | 28 | 28 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `capgraph_full` | 28 | 0.357 | 0.571 | 0.643 | 0.309 | 0.472 | 0.478 | 0.964 | 39825.8 | 35292.2 | 60023.6 | 1.8889 |
| `capgraph_score` | 28 | 0.250 | 0.536 | 0.750 | 0.235 | 0.563 | 0.398 | 0.964 | 5091.7 | 4785.9 | 7701.5 | 0.1525 |
| `bm25` | 28 | 0.179 | 0.500 | 0.571 | 0.178 | 0.246 | 0.312 | 1.000 | 12.1 | 1.9 | 79.6 | 0.0000 |
| `vector_only` | 28 | 0.107 | 0.321 | 0.464 | 0.111 | 0.236 | 0.228 | 1.000 | 219.7 | 14.9 | 22.4 | 0.0000 |
| `most_active` | 28 | 0.036 | 0.571 | 0.679 | 0.402 | 0.447 | 0.271 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

validation run diagnostics (`v2frozen`, rewritten):

| Measure | Value |
|---|---|
| cases | 28 |
| multi role cases | 28 |
| llm calls | 84 |
| rerank entries min | 28 |
| rerank entries median | 30 |
| cases below ten ranked | 0 |
| candidate pool min | 36 |
| candidate pool median | 49 |
| candidate pool max | 70 |
| rejected rerank entries | 9 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry |

### validation split — `v3frozen`, rewritten briefs

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 28 | 28 | 0 |
| `capgraph_score` | 28 | 28 | 0 |
| `bm25` | 28 | 28 | 0 |
| `vector_only` | 28 | 28 | 0 |
| `most_active` | 28 | 28 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `capgraph_full` | 28 | 0.393 | 0.571 | 0.714 | 0.313 | 0.549 | 0.481 | 0.996 | 47312.7 | 49216.3 | 68595.4 | 1.5944 |
| `capgraph_score` | 28 | 0.250 | 0.536 | 0.714 | 0.254 | 0.502 | 0.386 | 0.996 | 5022.0 | 4800.8 | 7196.6 | 0.1540 |
| `bm25` | 28 | 0.179 | 0.500 | 0.571 | 0.178 | 0.246 | 0.312 | 1.000 | 13.0 | 1.9 | 97.1 | 0.0000 |
| `vector_only` | 28 | 0.107 | 0.321 | 0.464 | 0.111 | 0.236 | 0.228 | 1.000 | 237.9 | 14.8 | 132.4 | 0.0000 |
| `most_active` | 28 | 0.036 | 0.571 | 0.679 | 0.402 | 0.447 | 0.271 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

validation run diagnostics (`v3frozen`, rewritten):

| Measure | Value |
|---|---|
| cases | 28 |
| multi role cases | 23 |
| llm calls | 79 |
| rerank entries min | 32 |
| rerank entries median | 63 |
| cases below ten ranked | 0 |
| candidate pool min | 34 |
| candidate pool median | 48 |
| candidate pool max | 73 |
| rejected rerank entries | 14 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry, not among the ranked candidates |

### test split — `v2frozen`, rewritten briefs

The two graph systems were **not run** in this arm — only the three offline baselines, which are free and identical across engine configurations. The unspent exposure of this split is reserved for the escalated `v2frozen` run (`docs/benchmark-v4-manifest.md` §6.4).

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `bm25` | 122 | 122 | 0 |
| `vector_only` | 122 | 122 | 0 |
| `most_active` | 122 | 122 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `bm25` | 122 | 0.303 | 0.631 | 0.721 | 0.254 | 0.411 | 0.459 | 1.000 | 4.1 | 1.9 | 2.5 | 0.0000 |
| `vector_only` | 122 | 0.180 | 0.516 | 0.689 | 0.177 | 0.327 | 0.333 | 1.000 | 11.7 | 10.3 | 18.2 | 0.0000 |
| `most_active` | 122 | 0.090 | 0.566 | 0.697 | 0.284 | 0.343 | 0.329 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

### test split — `v3frozen`, rewritten briefs

| System | Cases in split | Scored | Failed |
|---|---:|---:|---:|
| `capgraph_full` | 122 | 122 | 0 |
| `capgraph_score` | 122 | 122 | 0 |
| `bm25` | 122 | 122 | 0 |
| `vector_only` | 122 | 122 | 0 |
| `most_active` | 122 | 122 | 0 |

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `capgraph_full` | 122 | 0.508 | 0.754 | 0.803 | 0.396 | 0.597 | 0.622 | 0.974 | 49307.3 | 50355.8 | 60335.9 | 7.3159 |
| `capgraph_score` | 122 | 0.311 | 0.705 | 0.820 | 0.323 | 0.539 | 0.485 | 0.974 | 5189.0 | 4807.5 | 7278.9 | 0.6847 |
| `bm25` | 122 | 0.303 | 0.631 | 0.721 | 0.254 | 0.411 | 0.459 | 1.000 | 4.1 | 1.8 | 2.3 | 0.0000 |
| `vector_only` | 122 | 0.180 | 0.516 | 0.689 | 0.177 | 0.327 | 0.333 | 1.000 | 11.8 | 9.6 | 16.9 | 0.0000 |
| `most_active` | 122 | 0.090 | 0.566 | 0.697 | 0.284 | 0.343 | 0.329 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

test run diagnostics (`v3frozen`, rewritten):

| Measure | Value |
|---|---|
| cases | 122 |
| multi role cases | 111 |
| llm calls | 357 |
| rerank entries min | 32 |
| rerank entries median | 63 |
| cases below ten ranked | 0 |
| candidate pool min | 32 |
| candidate pool median | 50 |
| candidate pool max | 75 |
| rejected rerank entries | 97 |
| rejection reasons | cites evidence not in this person's contributions, duplicate entry, not among the ranked candidates |

## v2-frozen against v3-frozen — validation split

The open question the v3 report left behind: v3's retrieval and card view raised candidate recall but lost Hit@1 on single-ticket briefs. Broader briefs are exactly the case where the wider window and the lexical arm should pay off, so both frozen configurations were run over this manifest. Only the graph systems appear below: the three baselines never read the engine configuration, and their rows across the two arms are verified identical.

| System | Metric | v2frozen | v3frozen | Δ |
|---|---|---:|---:|---:|
| `capgraph_full` | Hit@1 | 0.357 | 0.393 | +0.036 |
| `capgraph_full` | Hit@5 | 0.571 | 0.571 | +0.000 |
| `capgraph_full` | Hit@10 | 0.643 | 0.714 | +0.071 |
| `capgraph_full` | Recall@5 | 0.309 | 0.313 | +0.004 |
| `capgraph_full` | Recall@10 | 0.472 | 0.549 | +0.077 |
| `capgraph_full` | MRR | 0.478 | 0.481 | +0.003 |
| `capgraph_full` | Candidate recall | 0.964 | 0.996 | +0.032 |
| `capgraph_score` | Hit@1 | 0.250 | 0.250 | +0.000 |
| `capgraph_score` | Hit@5 | 0.536 | 0.536 | +0.000 |
| `capgraph_score` | Hit@10 | 0.750 | 0.714 | -0.036 |
| `capgraph_score` | Recall@5 | 0.235 | 0.254 | +0.019 |
| `capgraph_score` | Recall@10 | 0.563 | 0.502 | -0.061 |
| `capgraph_score` | MRR | 0.398 | 0.386 | -0.013 |
| `capgraph_score` | Candidate recall | 0.964 | 0.996 | +0.032 |

`capgraph_full`, v3frozen against v2frozen on the 28 cases both scored:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.357 | 0.393 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.571 | 0.571 | +0.000 | 0 | 0 | 28 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.643 | 0.714 | +0.071 | 3 | 1 | 24 | McNemar exact p = 0.625 |
| Recall@5 | 28 | 0.309 | 0.313 | +0.004 | 3 | 4 | 21 | 95% bootstrap CI [-0.051, +0.069] |
| Recall@10 | 28 | 0.472 | 0.549 | +0.077 | 9 | 6 | 13 | 95% bootstrap CI [-0.081, +0.237] |
| MRR | 28 | 0.478 | 0.481 | +0.003 | 7 | 10 | 11 | 95% bootstrap CI [-0.074, +0.075] |

`capgraph_score`, v3frozen against v2frozen on the 28 cases both scored:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.250 | 0.250 | +0.000 | 1 | 1 | 26 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.536 | 0.536 | +0.000 | 3 | 3 | 22 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.750 | 0.714 | -0.036 | 1 | 2 | 25 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.235 | 0.254 | +0.019 | 4 | 4 | 20 | 95% bootstrap CI [-0.070, +0.121] |
| Recall@10 | 28 | 0.563 | 0.502 | -0.061 | 4 | 6 | 18 | 95% bootstrap CI [-0.179, +0.050] |
| MRR | 28 | 0.398 | 0.386 | -0.013 | 7 | 14 | 7 | 95% bootstrap CI [-0.107, +0.081] |

### validation split by project — `v3frozen`

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full / DM (roster 105) | 15 | 0.267 | 0.267 | 0.467 | 0.083 | 0.324 | 0.315 | 0.993 | 46377.8 | 46731.3 | 68595.4 | 0.8386 |
| capgraph_full / FAB (roster 62) | 1 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.500 | 1.000 | 47074.7 | 47074.7 | 47074.7 | 0.0596 |
| capgraph_full / MESOS (roster 67) | 6 | 0.333 | 0.833 | 1.000 | 0.431 | 0.653 | 0.458 | 1.000 | 40003.2 | 39261.3 | 56206.1 | 0.2976 |
| capgraph_full / TIMOB (roster 61) | 6 | 0.833 | 1.000 | 1.000 | 0.654 | 0.932 | 0.917 | 1.000 | 56999.0 | 56035.5 | 68907.2 | 0.3987 |
| capgraph_score / DM (roster 105) | 15 | 0.133 | 0.267 | 0.467 | 0.116 | 0.278 | 0.214 | 0.993 | 5101.5 | 4654.5 | 7196.6 | 0.0826 |
| capgraph_score / FAB (roster 62) | 1 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.333 | 1.000 | 4298.9 | 4298.9 | 4298.9 | 0.0054 |
| capgraph_score / MESOS (roster 67) | 6 | 0.167 | 0.667 | 1.000 | 0.264 | 0.528 | 0.403 | 1.000 | 4674.1 | 4755.7 | 5590.9 | 0.0311 |
| capgraph_score / TIMOB (roster 61) | 6 | 0.667 | 1.000 | 1.000 | 0.468 | 0.952 | 0.806 | 1.000 | 5291.8 | 5119.4 | 7301.1 | 0.0350 |
| bm25 / DM (roster 105) | 15 | 0.133 | 0.200 | 0.267 | 0.078 | 0.126 | 0.198 | 1.000 | 8.3 | 1.9 | 97.1 | 0.0000 |
| bm25 / FAB (roster 62) | 1 | 0.000 | 1.000 | 1.000 | 0.500 | 0.500 | 0.500 | 1.000 | 40.8 | 40.8 | 40.8 | 0.0000 |
| bm25 / MESOS (roster 67) | 6 | 0.000 | 0.667 | 0.833 | 0.264 | 0.361 | 0.194 | 1.000 | 9.1 | 1.4 | 47.3 | 0.0000 |
| bm25 / TIMOB (roster 61) | 6 | 0.500 | 1.000 | 1.000 | 0.291 | 0.390 | 0.681 | 1.000 | 23.8 | 1.4 | 135.7 | 0.0000 |
| vector_only / DM (roster 105) | 15 | 0.000 | 0.133 | 0.267 | 0.028 | 0.077 | 0.095 | 1.000 | 431.5 | 15.4 | 6145.1 | 0.0000 |
| vector_only / FAB (roster 62) | 1 | 1.000 | 1.000 | 1.000 | 0.500 | 1.000 | 1.000 | 1.000 | 18.2 | 18.2 | 18.2 | 0.0000 |
| vector_only / MESOS (roster 67) | 6 | 0.000 | 0.167 | 0.500 | 0.083 | 0.292 | 0.140 | 1.000 | 13.9 | 14.1 | 18.3 | 0.0000 |
| vector_only / TIMOB (roster 61) | 6 | 0.333 | 0.833 | 0.833 | 0.282 | 0.450 | 0.519 | 1.000 | 14.7 | 12.1 | 30.6 | 0.0000 |
| most_active / DM (roster 105) | 15 | 0.000 | 0.867 | 1.000 | 0.684 | 0.714 | 0.313 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / FAB (roster 62) | 1 | 0.000 | 0.000 | 1.000 | 0.000 | 0.500 | 0.167 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / MESOS (roster 67) | 6 | 0.000 | 0.333 | 0.333 | 0.139 | 0.194 | 0.213 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / TIMOB (roster 61) | 6 | 0.167 | 0.167 | 0.167 | 0.024 | 0.024 | 0.242 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

### test split by project — `v3frozen`

| System | N | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean latency (ms) | Median latency (ms) | P95 latency (ms) | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capgraph_full / DM (roster 105) | 64 | 0.453 | 0.594 | 0.656 | 0.266 | 0.437 | 0.530 | 0.976 | 50666.6 | 49875.9 | 59821.5 | 3.9161 |
| capgraph_full / FAB (roster 62) | 4 | 0.500 | 1.000 | 1.000 | 0.500 | 0.625 | 0.750 | 0.958 | 49626.3 | 48757.1 | 56513.6 | 0.2436 |
| capgraph_full / MESOS (roster 67) | 26 | 0.423 | 0.846 | 0.923 | 0.513 | 0.692 | 0.593 | 0.987 | 43879.3 | 48651.4 | 60335.9 | 1.4161 |
| capgraph_full / TIMOB (roster 61) | 28 | 0.714 | 1.000 | 1.000 | 0.570 | 0.867 | 0.839 | 0.962 | 51195.3 | 51741.3 | 61733.9 | 1.7402 |
| capgraph_score / DM (roster 105) | 64 | 0.203 | 0.562 | 0.688 | 0.219 | 0.413 | 0.372 | 0.976 | 5506.5 | 4988.1 | 7282.4 | 0.3659 |
| capgraph_score / FAB (roster 62) | 4 | 0.250 | 1.000 | 1.000 | 0.479 | 0.562 | 0.500 | 0.958 | 5331.4 | 4994.7 | 6607.9 | 0.0224 |
| capgraph_score / MESOS (roster 67) | 26 | 0.346 | 0.692 | 0.923 | 0.333 | 0.520 | 0.495 | 0.987 | 4467.1 | 4362.9 | 5897.1 | 0.1339 |
| capgraph_score / TIMOB (roster 61) | 28 | 0.536 | 1.000 | 1.000 | 0.531 | 0.840 | 0.732 | 0.962 | 5113.3 | 4824.7 | 7278.9 | 0.1626 |
| bm25 / DM (roster 105) | 64 | 0.266 | 0.531 | 0.562 | 0.217 | 0.347 | 0.399 | 1.000 | 3.1 | 1.9 | 2.3 | 0.0000 |
| bm25 / FAB (roster 62) | 4 | 0.500 | 1.000 | 1.000 | 0.438 | 0.646 | 0.646 | 1.000 | 11.5 | 1.3 | 42.2 | 0.0000 |
| bm25 / MESOS (roster 67) | 26 | 0.231 | 0.500 | 0.808 | 0.294 | 0.545 | 0.379 | 1.000 | 3.1 | 1.3 | 1.7 | 0.0000 |
| bm25 / TIMOB (roster 61) | 28 | 0.429 | 0.929 | 0.964 | 0.276 | 0.402 | 0.643 | 1.000 | 6.0 | 1.4 | 1.5 | 0.0000 |
| vector_only / DM (roster 105) | 64 | 0.172 | 0.453 | 0.516 | 0.137 | 0.203 | 0.294 | 1.000 | 13.2 | 10.7 | 17.0 | 0.0000 |
| vector_only / FAB (roster 62) | 4 | 0.500 | 0.750 | 1.000 | 0.354 | 0.562 | 0.667 | 1.000 | 12.5 | 12.0 | 15.2 | 0.0000 |
| vector_only / MESOS (roster 67) | 26 | 0.077 | 0.385 | 0.846 | 0.195 | 0.491 | 0.239 | 1.000 | 10.4 | 8.9 | 14.9 | 0.0000 |
| vector_only / TIMOB (roster 61) | 28 | 0.250 | 0.750 | 0.893 | 0.224 | 0.423 | 0.462 | 1.000 | 9.7 | 9.1 | 15.9 | 0.0000 |
| most_active / DM (roster 105) | 64 | 0.000 | 0.734 | 0.859 | 0.455 | 0.482 | 0.316 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / FAB (roster 62) | 4 | 0.250 | 0.500 | 0.750 | 0.083 | 0.292 | 0.398 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / MESOS (roster 67) | 26 | 0.000 | 0.346 | 0.615 | 0.137 | 0.319 | 0.241 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |
| most_active / TIMOB (roster 61) | 28 | 0.357 | 0.393 | 0.393 | 0.057 | 0.057 | 0.429 | 1.000 | 0.0 | 0.0 | 0.0 | 0.0000 |

## What the rewrite did — validation split, `v3frozen`

Every case here has two briefs over the same as-of time, the same roster and the same truth: the raw package text (median 4,143 characters over 12 issues) and the cheap-model rewrite of it (median 1,085 characters). The rewrite is what the benchmark uses, so its effect is measured here rather than assumed. A system that gains from the raw text is matching source vocabulary; one that holds up on the rewrite is matching the *description of the work*, which is the only thing a real staffing brief supplies.

`most_active` is the control: it never reads the brief, and every one of its numbers is identical across the two variants. Anything that moves, moved because the words changed.

| System | Metric | raw | rewritten | Δ |
|---|---|---:|---:|---:|
| `capgraph_full` | Hit@1 | 0.250 | 0.393 | +0.143 |
| `capgraph_full` | Hit@5 | 0.536 | 0.571 | +0.036 |
| `capgraph_full` | Hit@10 | 0.750 | 0.714 | -0.036 |
| `capgraph_full` | Recall@5 | 0.304 | 0.313 | +0.008 |
| `capgraph_full` | MRR | 0.392 | 0.481 | +0.089 |
| `capgraph_score` | Hit@1 | 0.250 | 0.250 | +0.000 |
| `capgraph_score` | Hit@5 | 0.429 | 0.536 | +0.107 |
| `capgraph_score` | Hit@10 | 0.607 | 0.714 | +0.107 |
| `capgraph_score` | Recall@5 | 0.199 | 0.254 | +0.055 |
| `capgraph_score` | MRR | 0.351 | 0.386 | +0.034 |
| `bm25` | Hit@1 | 0.286 | 0.179 | -0.107 |
| `bm25` | Hit@5 | 0.679 | 0.500 | -0.179 |
| `bm25` | Hit@10 | 0.857 | 0.571 | -0.286 |
| `bm25` | Recall@5 | 0.334 | 0.178 | -0.156 |
| `bm25` | MRR | 0.458 | 0.312 | -0.146 |
| `vector_only` | Hit@1 | 0.214 | 0.107 | -0.107 |
| `vector_only` | Hit@5 | 0.357 | 0.321 | -0.036 |
| `vector_only` | Hit@10 | 0.571 | 0.464 | -0.107 |
| `vector_only` | Recall@5 | 0.103 | 0.111 | +0.008 |
| `vector_only` | MRR | 0.311 | 0.228 | -0.083 |
| `most_active` | Hit@1 | 0.036 | 0.036 | +0.000 |
| `most_active` | Hit@5 | 0.571 | 0.571 | +0.000 |
| `most_active` | Hit@10 | 0.679 | 0.679 | +0.000 |
| `most_active` | Recall@5 | 0.402 | 0.402 | +0.000 |
| `most_active` | MRR | 0.271 | 0.271 | +0.000 |

`capgraph_full`, rewritten against raw, case by case:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.250 | 0.393 | +0.143 | 4 | 0 | 24 | McNemar exact p = 0.125 |
| Hit@5 | 28 | 0.536 | 0.571 | +0.036 | 2 | 1 | 25 | McNemar exact p = 1.000 |
| Hit@10 | 28 | 0.750 | 0.714 | -0.036 | 1 | 2 | 25 | McNemar exact p = 1.000 |
| Recall@5 | 28 | 0.304 | 0.313 | +0.008 | 5 | 4 | 19 | 95% bootstrap CI [-0.095, +0.102] |
| Recall@10 | 28 | 0.539 | 0.549 | +0.010 | 7 | 3 | 18 | 95% bootstrap CI [-0.126, +0.132] |
| MRR | 28 | 0.392 | 0.481 | +0.089 | 11 | 6 | 11 | 95% bootstrap CI [+0.005, +0.186] |

`capgraph_score`, rewritten against raw, case by case:

| Metric | N | Baseline | Variant | Δ | Wins | Losses | Ties | Test |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Hit@1 | 28 | 0.250 | 0.250 | +0.000 | 4 | 4 | 20 | McNemar exact p = 1.000 |
| Hit@5 | 28 | 0.429 | 0.536 | +0.107 | 4 | 1 | 23 | McNemar exact p = 0.375 |
| Hit@10 | 28 | 0.607 | 0.714 | +0.107 | 5 | 2 | 21 | McNemar exact p = 0.453 |
| Recall@5 | 28 | 0.199 | 0.254 | +0.055 | 6 | 7 | 15 | 95% bootstrap CI [-0.052, +0.182] |
| Recall@10 | 28 | 0.396 | 0.502 | +0.105 | 11 | 5 | 12 | 95% bootstrap CI [-0.068, +0.272] |
| MRR | 28 | 0.351 | 0.386 | +0.034 | 13 | 11 | 4 | 95% bootstrap CI [-0.111, +0.175] |

**The obvious objection, stated rather than buried.** The rewrite is written by a language model, and two of the five systems here contain language models, so a sceptic can ask whether the rewrite simply produces text that suits them. Two things bear on that. The deterministic `capgraph_score` arm never sees the brief inside a prompt after the intent parse — it is embedding similarity, term overlap and recency arithmetic — and it gains on the rewrite too, which is not what pure model-affinity would predict. But it does still depend on that one LLM intent parse, so the objection is narrowed rather than closed. What is not in question is the direction the raw variant flatters: on un-rewritten package text BM25 is the strongest system in this study, and that is precisely the artefact `G12` predicted a realistic brief would remove.

## Caveats specific to this instrument

- **The target is still assignee prediction.** A package's truth set is the people who *did* the work, not the people who *should* have. Multi-person truth makes the label less arbitrary than v1's single name; it does not make it a statement about optimal staffing.
- **Cases are correlated.** Consecutive packages inside a project share a mean Jaccard overlap of 0.34 in their truth sets (n=146), because the same team runs consecutive sprints. The effective sample size is smaller than the case count, which is why every comparison here is paired.
- **`most_active` is structurally strong here**, in a way it was not on v1. When truth is a whole team, ranking people by raw volume captures a large share of it without reading the brief at all. Any claim about the graph system has to clear that baseline on Recall@K, not just BM25 on Hit@K.
- **Four projects, not five.** EVG has no sprints in TAWOS, so it is absent. It had the smallest roster (21) and therefore the easiest Hit@10 in v1-v3.
- **59 of 150 briefs are capped** at 30 issues / 8,000 characters, so the brief is a sample of a large package rather than its whole content. The omitted issues still count toward truth, which makes those cases harder than they look.
- **Roster survivorship persists.** 502 people who resolved package issues are not roster-eligible and were dropped from truth (631 remain). The system is never asked to name someone it has never seen, which reality does not guarantee.
- **No v4-specific noise floor was measured.** The 0.100 run-to-run floor quoted in the v2 section was measured on the v1 instrument by re-running one configuration twice; nothing here re-establishes it for packages. Read the 28-case validation deltas as directional only, and prefer the paired win/loss counts to the aggregates.

## Spend

| Stage | Calls | Cost (USD) |
|---|---:|---:|
| `bench4_rewrite` | 164 | 0.0614 |
| `bench4_val` | 302 | 6.8280 |
| `bench4_test` | 357 | 7.3159 |
| **total** | | **14.2053** |

| Call type | Calls | Cost (USD) |
|---|---:|---:|
| `brief_rewrite` | 164 | 0.0614 |
| `intent` | 206 | 1.1940 |
| `rerank` | 453 | 12.9499 |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included, against the $15.00 ceiling the owner authorized on 2026-08-14.
