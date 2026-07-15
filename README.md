# Capability Graph Research PoC

Research and evaluation of evidence-backed capability retrieval using historical
public Jira data. See `docs/tech-design.md` for the full design and
`docs/implementation-plan.md` for the build order. This repository is an
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
uv run pytest
python scripts/tawos_slice_report.py  # streams the zip; no MySQL restore required
```

When a MySQL service is available, restore the verified archive and run Stage 0:

```bash
make db-up                  # convenience path when Docker is available
make restore-tawos          # one-time restore; slow
uv run python -m capgraph.pipeline.stage0_load --introspect
uv run python -m capgraph.pipeline.stage0_load --report
uv run python -m capgraph.pipeline.stage0_load
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
