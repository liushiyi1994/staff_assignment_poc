# Direction decision — research track first

- Date: 2026-08-10
- Decided by: project owner, via the orchestrator session
- Status: active
- Resolves: the direction gate in `docs/agent-handoff.md`

## Decision

Complete the research track (TAWOS extraction/retrieval experiment) before any
product-MVP implementation. The product MVP described in the untracked
`prd (1).md` remains the ultimate goal and becomes its own separately scoped
phase after the research track reports benchmark results.

## Rationale

A product MVP built today cannot be meaningfully evaluated:

1. Its success metrics (accepted shortlists, staffing decisions influenced,
   time saved) require a real roster, real source material, and real ProdOps
   pilot users that do not exist yet.
2. A synthetic seed corpus verifies plumbing but is circular as a quality
   evaluation: LLM-generated evidence re-extracted by an LLM.
3. TAWOS cannot stand in for product data. Its terms are research-only, and its
   opaque project-local identities cannot exercise identity resolution or
   curator review anyway.

The research track evaluates exactly the highest-risk components the two designs
share — extraction quality on messy evidence, emergent skill normalization,
hybrid retrieval, deterministic scoring plus LLM re-rank — against the accepted,
leakage-safe temporal benchmark (150 briefs). Learnings, prompts, thresholds,
weights, cost data, and evaluation methodology transfer to the MVP; TAWOS data
never does. The MVP's workflow half (identity resolution, curator review,
consolidation, deletion, multi-source weighting) is not tested by the research
track and still requires its own pilot evaluation later.

## Objective and acceptance

Objective: finish stages 2–5 and the query engine per `docs/tech-design.md` and
`docs/implementation-plan.md`, then report overall and per-project Hit@1/5/10,
MRR, candidate recall, latency, and cost against the BM25, vector-only, and
most-active baselines.

Acceptance: each stage passes its implementation-plan gate; the eval report is
produced from the frozen benchmark manifest with zero leakage-guard failures.

## Storage decision

Neo4j + native vector index remains the plan of record for the research PoC
(`docs/tech-design.md` §7). Before Stage 5 implementation begins, the
orchestrator runs an explicit checkpoint on whether to substitute
Postgres+pgvector for MVP transferability; no stage before Stage 5 depends on
the choice.

## Data boundary

- TAWOS is research-only. No TAWOS-derived profile, bucket, manifest, or
  contribution may seed, test, or demo the product MVP.
- `prd (1).md` stays untracked and user-owned until the MVP phase starts; it
  then moves to a canonical tracked path with approval.
- MVP implementation, when authorized, starts in a separately scoped branch or
  repository — never by mutating research pipeline code in place.

## Immediate next milestone

Stage 2 pilot gate per `docs/work-orders/stage2-pilot-gate.md`: make the
extraction command pilot-safe without any API call. The first LLM call requires
separate, explicit approval of provider, model ID, credentials, and a dollar
ceiling.
