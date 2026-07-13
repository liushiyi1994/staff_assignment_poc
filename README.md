# Capability Graph PoC — Starter Pack

Evidence-backed staffing recommendations from Jira exhaust. See `docs/tech-design.md` for the full design and `docs/implementation-plan.md` for the build order. This repo is a scaffold meant to be completed with Claude Code: interfaces, prompts, config and contracts are in place; stages marked TODO are specced with acceptance criteria.

## Dataset and provenance

The benchmark foundation uses the official [TAWOS v1.1 UCL
record](https://rdr.ucl.ac.uk/articles/dataset/The_TAWOS_dataset/21308124):
458,232 issues, 39 projects, and 12 Jira repositories. The older
508,963/44/13 figures are for v1.0. Download `TAWOS.sql.zip` into
`data/raw/`, verify it as described in [`docs/data-provenance.md`](docs/data-provenance.md),
and keep the 637,550,449-byte archive local and out of version control.

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
```

When a MySQL service is available, restore the verified archive and run Stage 0:

```bash
make db-up                  # convenience path when Docker is available
make restore-tawos          # one-time restore; slow
uv run python -m capgraph.pipeline.stage0_load --introspect
uv run python -m capgraph.pipeline.stage0_load --report
uv run python -m capgraph.pipeline.stage0_load
```

Use the complete slice report before replacing the provisional project keys in
`config/settings.yaml`. The later graph, retrieval, and LLM stages remain outside
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
- Every build produces a deterministic, versioned manifest with a fixed seed.
- A single-assignee outcome is reported as Hit@K (plus MRR), not mislabeled
  binary Recall@K. Historical assignee is a prediction target, not proof of best fit.
- Synthetic profiles are never mixed into the quantitative benchmark.
