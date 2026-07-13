# Capability Graph PoC

Staffing recommendation PoC: extract evidence-backed capability profiles from public Jira data (TAWOS), store in Neo4j (graph + vector index), answer natural-language project briefs with ranked, explainable shortlists.

Read `docs/tech-design.md` (full design + trade-offs) and `docs/implementation-plan.md` (ordered tasks with acceptance criteria) before implementing anything.

## Architecture (1-minute version)

Part A — offline pipeline (`src/capgraph/pipeline/`), stages 0–5:
MySQL (TAWOS dump) → parquet → person×project×quarter buckets → LLM extraction (Contribution JSON) → skill normalization (embedding dedup) → projections with recency decay → Neo4j load + vector index.

Part B — query engine (`src/capgraph/query/`):
brief → LLM intent parse → candidate generation (vector search ∪ Cypher skill filter) → subgraph expansion → weighted score → LLM re-rank top-15 with reasons → shortlist with evidence ticket keys.

Eval (`src/capgraph/eval/`): deterministic temporal holdout — history and the eligible roster are frozen before each benchmark query time, later issues become "briefs", and ground truth is the historical assignee. Report Hit@1/5/10, MRR, candidate recall, per-project results, latency, and cost vs BM25 / vector-only / most-active baselines. Historical assignment is a prediction target, not proof of optimal qualification.

## Non-negotiable design decisions

Do not "improve" these without asking — they are deliberate trade-offs (rationale in docs/tech-design.md §7):

1. Extraction granularity is person × project × quarter buckets (split >30 tickets by component). Never per-ticket, never whole-history.
2. Raw tickets stay OUT of Neo4j. They live in parquet (`data/parquet/`); Contribution nodes carry `evidence_ticket_keys` as provenance pointers.
3. Candidate generation is the UNION of vector and structured retrieval, not intersection.
4. Deterministic weighted score first, LLM re-rank only on top-K. Never LLM-rank the full candidate pool.
5. Skills are emergent (free text, deduped by embedding clustering) — no fixed taxonomy.
6. Persistent graph, fixed schema, dynamic content. Never rebuild the graph per query.
7. All LLM calls go through `src/capgraph/llm.py` (retry, JSON parsing, cost accounting). Prompts live in `prompts/*.md`, never inline in code.

## Commands

- `make db-up` / `make db-down` — Neo4j + MySQL via docker compose
- `make restore-tawos` — load TAWOS dump into MySQL (one-time, slow)
- `make stage0` … `make stage5` — individual pipeline stages
- `make pipeline` — stages 0–5 in order (each stage is idempotent, reads/writes `data/`)
- `make eval` — run eval harness, writes `data/eval/results.md`
- `make test` — pytest
- `make demo` — convert + launch the demo notebook
- `uv run python -m capgraph.query.engine "Need two backend engineers with streaming experience"` — smoke-test a query

## Conventions

- Python 3.11+, `uv` for env/deps, `pydantic` models for every data shape crossing a stage boundary (`src/capgraph/models.py`).
- Every pipeline stage: CLI via `python -m capgraph.pipeline.stageN_*`, reads config from `config/settings.yaml`, checkpoints so re-runs skip completed work (`--force` to redo).
- LLM models and all thresholds/weights come from `config/settings.yaml` — no magic numbers in code.
- Cheap model (Haiku) for bulk extraction; stronger model (Sonnet) for intent parsing and re-rank only.
- Log per-stage token usage and cost to `data/llm_costs.jsonl`; abort a stage if projected cost exceeds `max_stage_cost_usd`.
- Keep functions small and testable; every stage gets at least a toy-data unit test in `tests/`.

## Gotchas

- TAWOS v1.1 has 458,232 issues, 39 projects, and 12 repositories. Its `User` table contains only `ID` and `Project_ID`: use `<project_key>:<user_id>` IDs and `Person <project_key>-<user_id>` pseudonyms, and never infer names or cross-project identity. The schema has no labels table, so Stage 0 emits an empty labels list. See `docs/data-provenance.md`.
- TAWOS quirks: descriptions contain Jira wiki markup/HTML (strip it); many empty descriptions can use comments only when those comments existed by the relevant as-of time; bot/CI-like IDs need conservative filtering; some projects have low assignee coverage — use the complete Stage 0 report before choosing the project slice.
- TAWOS table/column names must match the official v1.1 schema. Do not trust guessed names or obsolete v1.0 documentation.
- Benchmark leakage: query time comes from issue creation or a defensible recorded assignment event, never eventual resolution. Do not expose fields/comments created later, derive the roster and minimum-ticket eligibility only from earlier history, and calculate recency at the cutoff/query time rather than `date.today()`.
- Every benchmark build writes a deterministic, versioned manifest containing issue ID, query text, as-of time, project, eligible roster, truth IDs, split, and any exclusion reason. Use a fixed seed and deterministic project stratification.
- Neo4j vector index needs fixed dimensions — set by `embedding.model` in settings (default bge-small-en-v1.5, 384 dims). Changing the model requires dropping/recreating the index and re-embedding.
- Embedding model downloads ~130MB from HuggingFace on first run.

## Environment

The Stage 0 and benchmark-foundation work does not require an Anthropic key and must not call an LLM. Fixture tests also do not require Docker. A real dump restore requires a compatible MySQL service; Docker Compose is the repository's convenience path when Docker is available. Neo4j and `ANTHROPIC_API_KEY` are only needed for later, explicitly authorized stages.
