# Capability Graph PoC

Research/evaluation PoC: extract evidence-backed capability profiles from public
Jira data (TAWOS), store them in Neo4j (graph + vector index), and evaluate
ranked, explainable retrieval against historical assignments. It is not
production employment decision support and must not be used for real hiring,
staffing, promotion, performance, or other employment decisions.

Read `docs/tech-design.md` (full design + trade-offs) and `docs/implementation-plan.md` (ordered tasks with acceptance criteria) before implementing anything.
When resuming after benchmark-foundation acceptance or choosing the next implementation track, read `docs/agent-handoff.md` first.
Direction: the research track is active (`docs/direction-decision.md`); the product MVP is a later, separately scoped phase.

## Operating model

This project runs as a session hierarchy; identify which role your session plays before doing anything:

- **Orchestrator** (single session, Fable 5): owns direction, decisions, work orders, review, and acceptance. Does not implement.
- **Phase leads** (optional, Fable 5): own one stage or phase, may split its work order into smaller ones, review worker output, and report acceptance evidence to the orchestrator.
- **Workers** (separate sessions, e.g. Codex or Opus 5): implement exactly one work order from `docs/work-orders/`, on the branch it names, and report back in its requested format.

Rules:

- Direction decisions live in `docs/direction-decision.md`; active tasking lives in `docs/work-orders/` (one file per order, with status, scope, and acceptance criteria).
- Workers implement only what their work order scopes. Scope changes, blockers, and discovered design problems are escalated to the orchestrator, not improvised.
- Work is accepted by the orchestrator (or delegated lead) against the order's acceptance criteria before follow-on work orders are issued.
- LLM/API calls, credentials, and spend ceilings require explicit orchestrator-recorded approval per order; work orders state whether any call is permitted.

## Architecture (1-minute version)

Part A — offline pipeline (`src/capgraph/pipeline/`), stages 0–5:
MySQL (TAWOS dump) → parquet → person×project×quarter buckets → LLM extraction (Contribution JSON) → skill normalization (embedding dedup) → projections with recency decay → Neo4j load + vector index.

Part B — query engine (`src/capgraph/query/`):
brief → LLM intent parse → candidate generation (vector search ∪ Cypher skill filter) → subgraph expansion → weighted score → LLM re-rank top-15 with reasons → shortlist with evidence ticket keys.

Eval (`src/capgraph/eval/`): deterministic temporal holdout — history and the same-project eligible roster are frozen before each benchmark query time, later issues become "briefs", and ground truth is the assignee reconstructed at the safe resolution boundary. Report Hit@1/5/10, MRR, candidate recall, per-project results, latency, and cost vs BM25 / vector-only / most-active baselines. Historical assignment is a prediction target, not proof of optimal qualification.

## Non-negotiable design decisions

Do not "improve" these without asking — they are deliberate trade-offs (rationale in docs/tech-design.md §7):

1. Extraction granularity is person × project × quarter buckets (deterministically chunk >30 tickets). Never per-ticket, never whole-history.
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
- TAWOS quirks: descriptions contain Jira wiki markup/HTML (strip it); empty descriptions stay empty rather than using later comments; final project/key snapshots can reflect moves and resolution metadata can be edited or internally inconsistent, so Stage 0 keeps those rows for audit but explicitly excludes them from temporal evidence. Unversioned component names and final assignment/status fields are redacted from Stage 1. Opaque user IDs do not support name-based bot filtering; use the complete report as the source of the configured slice.
- TAWOS table/column names must match the official v1.1 schema. Do not trust guessed names or obsolete v1.0 documentation.
- Benchmark leakage: query time comes from issue creation or a defensible recorded assignment event, never eventual resolution. Do not expose fields/comments created later, derive the roster and minimum-ticket eligibility only from earlier history, require every roster/truth ID to have a retained Stage 1 profile bucket, and calculate recency at the cutoff/query time rather than `date.today()`. Benchmark truth is the project-qualified assignee reconstructed at the safe resolution boundary; the final assignee snapshot is audit-only.
- Every benchmark build writes a deterministic, versioned manifest containing stable TAWOS issue ID, final Jira key for audit, query text, as-of time, project, eligible roster, truth IDs, split, and any exclusion reason. Use a fixed seed and deterministic project stratification.
- Neo4j vector index needs fixed dimensions — set by `embedding.model` in settings (default bge-small-en-v1.5, 384 dims). Changing the model requires dropping/recreating the index and re-embedding.
- Embedding model downloads ~130MB from HuggingFace on first run.

## Environment

The Stage 0 and benchmark-foundation work does not require an Anthropic key and must not call an LLM. Fixture tests also do not require Docker. A real dump restore requires a compatible MySQL service; Docker Compose is the repository's convenience path when Docker is available. Neo4j and `ANTHROPIC_API_KEY` are only needed for later, explicitly authorized stages.
