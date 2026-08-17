# Work order: Stage 5 Neo4j load

- Issued: 2026-08-11 by the orchestrator
- Status: **accepted 2026-08-11** (commit `1228692`, branch `agent/stage5-graph`)

## Acceptance record (orchestrator, 2026-08-11)

Independently verified against the live graph: node counts (316 Person /
5 Project / 2,666 Contribution / 10,630 Skill / 344 Specialization) and edge
counts (HAS_* 19,950; DEMONSTRATES 25,959; MADE/ON 2,666 each;
COLLABORATED_WITH 8,793) all reconcile with the source artifacts; every
Contribution embedding is 384-dim; 187 tests pass with the service up, ruff
clean; vector probe returns topically coherent containerizer/Docker work from
three distinct people. All eight worker decisions accepted — notably the
basis="co_presence_same_project_period" honesty property, the dimension-drift
refusal, the MERGE-only write guard, and the ticket-payload allowlist test.
Optional follow-up parked: add Project name/repo properties from
projects.parquet if the demo wants them.
- Phase: research track; storage checkpoint resolved to Neo4j
  (`docs/direction-decision.md`)
- Base branch: `agent/stage4-project` (HEAD `bce8474` + acceptance commit)
- Suggested working branch: `agent/stage5-graph`
- Constraints: **no LLM/API calls.** Permitted network: Docker image pulls and
  the already-cached local embedding model. Raw tickets stay OUT of Neo4j
  (non-negotiable #2 in `CLAUDE.md`) — Contribution nodes carry
  `evidence_ticket_keys` as provenance pointers only. Do not modify accepted
  artifacts; `prd (1).md` stays untracked.

## Environment

Neo4j runs via `make db-up` (docker compose). Colima may be stopped — start it
(`colima start`) before compose. Vector index dimensions come from
`embedding.dims` (384); the index must be created per
`src/capgraph/graph/schema.cypher` (extend that file as needed).

## Objective

Implement and run `stage5_graph.load()`: `normalized.jsonl` + `terms.jsonl` +
`capabilities.jsonl` → Neo4j per `docs/tech-design.md` §4. Batched
UNWIND+MERGE upserts; embedding of all 2,666 contribution summaries into
`Contribution.embedding`; a working native vector index; idempotent re-runs.

## Tasks

1. **Nodes:** Person (id, pseudonym, project_key, active_from/to derived from
   their bucket periods), Project (key, domain from settings), Contribution
   (id, summary, period, confidence, evidence_ticket_keys, embedding),
   Skill and Specialization (canonical name + aliases from `terms.jsonl`).
2. **Edges:** `(Person)-[:MADE]->(Contribution)-[:ON]->(Project)`;
   `(Contribution)-[:DEMONSTRATES {strength}]->(Skill|Specialization)`
   (strength from the contribution's specialization records; skills have no
   strength — omit or null consistently);
   `(Person)-[:HAS_SKILL|HAS_SPECIALIZATION {evidence_count, last_used,
   decay_score}]->(...)` from `capabilities.jsonl`;
   `(Person)-[:COLLABORATED_WITH {periods_count}]->(Person)` from bucket
   co-occurrence (same project + same quarter, both pre-cutoff) — label it
   honestly: it is co-presence, not verified collaboration; nothing scores on
   it.
3. **Embeddings:** `embeddings.embed` over contribution summaries, batched;
   store as float arrays sized `embedding.dims`.
4. **Idempotency:** running `make stage5` twice produces identical node/edge
   counts (MERGE on stable keys); print counts per label/type after load.
5. **Tests:** unit tests for batch/statement construction and property mapping
   run offline without Neo4j; integration checks (counts, vector probe) gate
   on Neo4j availability and skip cleanly otherwise. Existing suite green;
   ruff clean.

## Acceptance criteria

- `make stage5` twice: counts identical both runs and plausible
  (316 Person, 5 Project, 2,666 Contribution, 10,630 Skill, 344
  Specialization; HAS_* edges 19,950 total).
- `CALL db.index.vector.queryNodes('contribution_embedding', 5, $vec)` with a
  hand-written probe (e.g. "Docker container storage integration") returns
  topically sensible contributions — include the probe and results in the
  report.
- Full test suite green offline (Neo4j-gated tests skipped without the
  service), ruff clean.

## Out of scope

Query engine (Stage 6), eval harness, any LLM call, demo notebook.

## Report back

Branch/commits, node/edge count table from both runs, the vector-probe
transcript, test/ruff output, deviations; escalate rather than improvise.
