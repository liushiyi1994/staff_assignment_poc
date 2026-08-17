# Capability Graph Research PoC

Research and evaluation of evidence-backed capability retrieval using historical
public Jira data. See `docs/tech-design.md` for the full design and
`docs/implementation-plan.md` for the build order and
[`docs/real-data-validation.md`](docs/real-data-validation.md) for the accepted
TAWOS restore/export/benchmark run. This repository is an
implementation scaffold: interfaces, prompts, config, and contracts are in
place; stages marked TODO have acceptance criteria.

This is not production employment decision support. Do not use it to make or
recommend real hiring, staffing, promotion, performance, or other employment
decisions.

## Dataset and provenance

The benchmark foundation uses the official [TAWOS v1.1 UCL
record](https://rdr.ucl.ac.uk/articles/dataset/The_TAWOS_dataset/21308124):
458,232 issues, 39 projects, and 12 Jira repositories. The older
508,963/44/13 figures are for v1.0. Download `TAWOS.sql.zip` into
`data/raw/`, verify it as described in [`docs/data-provenance.md`](docs/data-provenance.md),
and keep the 637,550,449-byte archive local and out of version control.

TAWOS is Apache-2.0 licensed, and its official [Terms of
Use](https://github.com/SOLAR-group/TAWOS#terms-of-use) additionally restrict the
dataset to researchers using it for research purposes and require users to avoid
harmful analysis and re-identification attempts. This repository uses it only
for research and evaluation.

TAWOS v1.1 does not contain person names or reliable cross-project identities;
users are project-scoped opaque IDs. It also has no labels table. This benchmark
uses project-qualified IDs, explicit pseudonyms, and empty label lists rather
than fabricating any of those fields.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- ~10 GB free disk for the local archive and derived data; 8 GB RAM recommended
- MySQL 8-compatible access for a real dump restore. Docker Compose is the
  convenience path, but fixture-based Stage 0 and benchmark tests do not require Docker.
- Neo4j and an Anthropic API key are later-pipeline requirements, not requirements
  for the benchmark/data foundation. Do not invoke stages 2–5 for foundation work.

## Setup

```bash
uv sync
uv run python -m pytest
python scripts/tawos_slice_report.py  # streams the zip; no MySQL restore required
```

When a MySQL service is available, restore the verified archive and run Stage 0:

```bash
make db-up                  # convenience path when Docker is available
make restore-tawos          # one-time restore; slow
uv run python -m capgraph.pipeline.stage0_load --introspect
uv run python -m capgraph.pipeline.stage0_load --report
uv run python -m capgraph.pipeline.stage0_load
uv run python -m capgraph.pipeline.stage1_bucket
uv run python -m capgraph.eval.holdout
uv run python scripts/validate_tawos_source_audit.py
```

The configured five-project slice—MESOS, FAB, TIMOB, DM, and EVG—was selected
from `data/parquet/slice_report.{md,csv}`. The adjacent
`slice_report.metadata.json` pins the archive digest and effective parameters.
The slice contains 82,703 source issues, 62,554 created before cutoff, 316
project-qualified people meeting the pre-cutoff ticket threshold, and 3,594
upper-bound plausible held-out briefs before retained-profile, creation-text, and
other manifest exclusions; the manifest cap is 150.
Reproduce the report before changing the slice or cutoff. When MySQL is available,
`stage0 --report` writes the separate
`slice_report_mysql.{md,csv}` cross-check so it cannot overwrite the canonical
archive-stream report. The later graph, retrieval, and LLM stages remain outside
the current foundation scope.

## Stage 2 pilot gate

Stage 2 is gated behind a deterministic ~30-bucket pilot. Both gate commands are
offline and make no API call:

```bash
make stage2-pilot-manifest   # 6 buckets x 5 projects, seeded; rebuilds byte-identically
make stage2-pilot-dry-run    # renders/validates prompts and projects the cost
```

The pilot itself spends money and therefore stays an explicit command, permitted
only once the orchestrator has approved provider, model ID, credentials, and a
dollar ceiling:

```bash
uv run python -m capgraph.pipeline.stage2_extract --pilot data/contributions/pilot_manifest.v1.jsonl
```

Pilot output (`pilot_raw.jsonl`, cost stage `stage2_pilot`, budget
`llm.pilot_budget_usd`) never mixes with a full run (`raw.jsonl`, cost stage
`stage2`, budget `llm.max_stage_cost_usd`). Every response is validated against
the `Contribution` contract with evidence keys confined to its source bucket, and
the command exits nonzero when `extraction.min_valid_rate` is missed.

The accepted real-data run produced 2,668 deterministic Stage 1 buckets and a
24,522-row manifest with 150 selected cases (30 validation, 120 test). All source,
temporal, roster, conservation, and leakage invariants passed; generated datasets
remain local and ignored.

## Benchmark run

Results live in `docs/eval-results.md` (tracked) and `data/eval/results.{md,json}`.
Two of the four targets spend money; the other two make no model call:

```bash
make eval-baselines   # offline: BM25, pure vector, most-active over both splits
make eval-validation  # SPENDS: graph system over the 30 validation cases
make eval-test        # SPENDS: the frozen 120-case test run, once
make eval             # offline: rebuild the report from the checkpointed runs
```

Each case and system is checkpointed to `data/eval/runs/<split>.jsonl` with the
configuration digest, so an interrupted run resumes without paying twice and a
configuration change is refused rather than silently mixed into the metrics.

### Benchmark v2

A second pass tuned on the validation split and re-ran the test split once, under
`docs/benchmark-v2-config.md` (the frozen configuration) and reported below the
`<!-- benchmark-v2 -->` marker in `docs/eval-results.md`. Its checkpoints live in
`data/eval/v2/`, separate from v1's, and its spend is logged under `stage7b_val` /
`stage7b_test`.

```bash
make eval-v2-levers      # offline: RRF fusion and roster backstop, from v1 checkpoints
make eval-v2-scores      # SPENDS (intent parse only): checkpoints the score components
make eval-v2-sweep       # offline: weight sweep over that checkpoint
make eval-v2-validation  # SPENDS: the graph system over the 30 validation cases
make eval-v2-test        # SPENDS: the frozen 120-case test run, once
make eval-v2-report      # offline: rebuild the v2 section only
```

The v1 half of `docs/eval-results.md` is a frozen artifact. `make eval` regenerates it
from the v1 checkpoints against *current* settings, which v2 has changed — so it should
not be re-run without intending to restate v1's configuration table. `make eval-v2-report`
touches only the v2 half, and `make eval` preserves the v2 half in turn.

## Layout

```
CLAUDE.md                    Claude Code project instructions (read first)
docs/                        tech design, implementation plan, data provenance
config/settings.yaml         project slice, models, thresholds, score weights
prompts/                     extraction / intent / re-rank prompts (markdown, templated)
src/capgraph/
  models.py                  pydantic contracts between all stages
  llm.py                     Claude wrapper: retry, JSON mode, cost log
  embeddings.py              local sentence-transformers wrapper
  pipeline/stage0..5_*.py    ingestion pipeline
  query/                     intent → retrieve → rank → engine
  eval/                      holdout builder, baselines, metrics
  graph/schema.cypher        constraints + vector index
notebooks/demo.py            demo notebook (jupytext percent format)
tests/                       toy-data unit tests
data/                        local/derived data: raw/ parquet/ buckets/ contributions/ eval/
```

## Benchmark invariants

- Query time comes from issue creation or a recorded assignment event, never
  eventual resolution time.
- Evidence, candidate eligibility, and recency are all computed as of the cutoff
  or query time; later information is forbidden.
- Ground truth is the same-project assignee reconstructed at the safe resolution
  boundary. Final project/key/assignee and raw resolution-time snapshots are
  retained for audit only and redacted from reusable history views.
- Every build produces a deterministic, versioned manifest with a fixed seed.
- A single-assignee outcome is reported as Hit@K (plus MRR), not mislabeled
  binary Recall@K. The reconstructed resolution-time assignee is a prediction
  target, not proof of best fit.
- Synthetic profiles are never mixed into the quantitative benchmark.
