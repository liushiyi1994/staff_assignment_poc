# Work order: Stage 6 query engine

- Issued: 2026-08-11 by the orchestrator
- Status: **accepted 2026-08-11** (commits `0f7c06f`..`a38f123`, branch `agent/stage6-query`)

## Acceptance record (orchestrator, 2026-08-11)

Verified: 274 tests pass with Neo4j up (257+17 skips without), ruff clean;
stage6_pilot ledger reconciles at $0.3860 across 23 calls, all on the approved
model; no wall-clock date calls in the query path; smoke transcripts committed.
All eight deviations accepted — the two scoring refinements (evidence counted
as distinct contributions; vector-only candidates scored from the contributions
that surfaced them) are material improvements with disclosed measurements, and
the self-introduced truncation defect was caught, fixed, and cost-disclosed.
The re-rank evidence guard fired once in live use (foreign citations dropped),
proving the enforcement.

**Latency ruling:** the <15s criterion is waived for the research track. The
engine's own work is 0.12–0.29s; the 16.8–21.6s totals are one generation-bound
re-rank call producing the explanations that are the product's point. Latency
is a reported benchmark metric, not a gate; re-rank model or verbosity can be
A/B'd on the validation split if the reported number needs improving. Carried:
httpx remains an undeclared direct dependency (next lock refresh).
- Phase: research track; graph loaded and accepted
  (`docs/work-orders/stage5-graph.md`)
- Base branch: `agent/stage5-graph` (worker branch + acceptance commit)
- Suggested working branch: `agent/stage6-query`
- LLM authorization: **build and tests are offline.** The final smoke run is
  authorized: intent + re-rank calls for the 5 briefs below, under stage name
  `stage6_pilot` (draws the $1.00 `llm.pilot_budget_usd` ceiling; realistic
  cost ≈ $0.05). No other LLM calls.

## Objective

Implement Part B per `docs/tech-design.md` §6: natural-language brief →
parsed intent → candidate generation (vector arm ∪ structured arm) → subgraph
expansion → deterministic weighted score → LLM re-rank of top-15 with reasons
→ shortlist with evidence ticket keys. `python -m capgraph.query.engine
"<brief>"` end to end.

## Tasks

1. **Per-call provider routing** (carried from
   `docs/work-orders/openrouter-provider.md`): make the gateway resolve the
   provider per model instead of globally — an explicit
   `llm.model_providers` map in settings (model id → provider), refusing
   unmapped models the same way unpriced ones are refused. Keep `llm.provider`
   as the documented default for unmapped models or remove it cleanly —
   worker's choice, tested either way.
2. **Models/pricing:** set `llm.intent_model` and `llm.rerank_model` to
   `openai/gpt-5.6-terra`; add its pricing entry ($1.00 in / $6.00 out per
   MTok, OpenRouter models API, verified 2026-08-10). Orchestrator-approved
   deviation from the design's Claude-Sonnet default — record stands here.
3. **`retrieve.py`:** `generate_candidates(intent, brief)` — vector arm
   (embed the brief locally, `db.index.vector.queryNodes` top
   `retrieval.vector_top_k`, map Contributions → Persons) ∪ structured arm
   (parameterized Cypher over HAS_SKILL / HAS_SPECIALIZATION, alias-aware
   matching of intent terms against canonical names + aliases, top
   `retrieval.structured_top_k` by decayed evidence). Union with arm
   provenance kept (`found_by`). `expand(person_ids)` — contributions,
   capability edges, evidence keys for scoring and re-rank context.
4. **`rank.py`:** deterministic score per `scoring.weights`
   (specialization_match, skill_overlap, recency via stored `decay_score`,
   evidence_strength) — normalized, documented, unit-tested. Never
   `date.today()` anywhere in the query path.
5. **`intent.py` / re-rank:** `call_json` with the existing
   `prompts/intent_parsing.md` and `prompts/rerank.md` (adjust prompts if
   contracts require — flag any change); re-rank receives only the top
   `retrieval.rerank_top_k` with their subgraph context; output reasons must
   cite evidence ticket keys that exist in the candidate's own contributions
   (validate, reject otherwise).
6. **`engine.py`:** wire the steps; print a readable shortlist (rank, person,
   score, matched terms, reason, evidence keys); `--json` for machine output;
   graceful error if Neo4j is down.
7. **Tests (offline, no network, no Neo4j):** provider-map resolution and
   refusal; Cypher construction and alias matching; union semantics and arm
   provenance; scoring math incl. weight normalization and determinism;
   intent/re-rank flows with mocked `call_json`, including rejection of a
   re-rank reason citing foreign evidence. Suite green, ruff clean.
8. **Smoke run (authorized):** run these 5 briefs end-to-end and capture full
   transcripts:
   1. "Need a backend engineer with deep container orchestration and Docker
      integration experience"
   2. "Looking for someone who has built CI/CD and build-infrastructure
      tooling for a large test fleet"
   3. "Who has worked on distributed ledger transaction validation and
      privacy features?"
   4. "Need a mobile SDK engineer strong in iOS UI internals and event
      handling"
   5. "Someone with scientific data pipeline and astronomy image-processing
      background"

## Acceptance criteria

- End-to-end latency < 15s per brief; every re-rank reason cites evidence
  keys present in that person's own contributions (enforced + observed);
  at least one brief's shortlist contains a person found by the vector arm
  only (report which); smoke spend within the $1 ceiling under
  `stage6_pilot`.
- Suite green offline; ruff clean; no `date.today()` in query path.

## Out of scope

Eval harness / benchmark runs (Stage 7 order follows), demo notebook,
extraction-side changes.

## Report back

Branch/commits, design notes (provider map shape, Cypher approach), test/ruff
output, the 5 smoke transcripts with latency and cost, deviations; escalate
rather than improvise.
