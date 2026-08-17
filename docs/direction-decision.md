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
   time saved) require a real roster, real source material, and real operations-team
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

**Checkpoint resolved 2026-08-11:** the owner chose to keep **Neo4j**,
explicitly valuing learning the graph approach over the lighter
SQLite/pgvector alternatives the orchestrator tabled. Consequence accepted:
Docker/Colima becomes a runtime dependency for Stage 5 onward, and MVP
storage remains a separate decision for the product phase.

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

---

# Direction decision — extend the research track: benchmark v4 before MVP

- Date: 2026-08-14
- Decided by: project owner, via the orchestrator session
- Status: active (supplements, does not replace, the 2026-08-10 decision above)

## Decision

Build **benchmark v4** (`docs/improvement-backlog.md` G12: sprint/epic-grouped
work-package briefs, leakage-guarded cheap-model rewrite, multi-person ground
truth, freshly cut manifest) before starting the product-MVP phase. The MVP
remains the ultimate goal but is explicitly **deferred**: the owner judges a
more solid evidence base more valuable right now than an MVP start. The
rewrite step uses a cheap model per the owner's instruction.

## Rationale

1. The retired single-ticket test split cannot validate any further
   improvement (`docs/agent-handoff.md`); every measured claim from here
   requires a new instrument.
2. Single-ticket briefs distort what is measured (backlog G12: term-match
   flattery toward BM25, single-truth labelling, untestable headcount,
   roster survivorship). Work-package briefs with multi-person truth fix the
   first three by construction and keep the ground truth real.
3. Wave-1 levers (G3a, G5, G6, G11a) land behind flags that only a working
   benchmark can justify flipping.

## Consequences accepted

- The MVP phase moves later; `docs/direction-decision.md`'s data boundary is
  unchanged (TAWOS stays research-only; nothing here seeds the product).
- New LLM spend for v4 is authorized separately in
  `docs/work-orders/benchmark-v4.md` (ceiling recorded there).
- The v1–v3 single-ticket suite stays archival for comparability; its test
  split stays retired.

---

# Direction decision — research track concluded

- Date: 2026-08-16
- Decided by: the gates the owner set in `docs/work-orders/weights-round.md`,
  accepted by the orchestrator
- Status: active

## Decision

The research track's experimental program is **concluded**. The final
configuration of record is the v4 baseline (v3-frozen engine defaults), and
the final headline is its v4 test result: `capgraph_full` Hit@1 0.508 /
Hit@5 0.754 / Hit@10 0.803 / MRR 0.622, leading every baseline on all six
metrics (vs BM25: Hit@1 +0.205, McNemar p = 0.000). The v4 test split
retains one unspent planned exposure. Total experimental LLM spend across
the track: ≈ $49.86, every call ledgered.

## Why it concludes here

Every remaining measured lever closed on evidence: retrieval is solved to
0.975 candidate recall; the re-rank is order-robust and earns +0.250 Hit@1
over the deterministic arm on pinned pools; the deterministic retune is real
but provably cannot cross the re-rank window; vocabulary gating and
strength weighting fail their own gates. The instrument has a measured
noise floor, and no open lever projects a gain above it.

## What remains

1. Manager-pitch v4 addendum (order to be issued — $0, docs only).
2. The MVP phase decision per the 2026-08-10 and 2026-08-14 entries above.
   The MVP inherits: the evidence-guard extraction pattern, hybrid union
   retrieval with a lexical arm, cheap-deterministic-then-LLM ranking with
   citation enforcement, the pin-everything-but-the-lever evaluation rule,
   per-metric noise floors, and the scoring lead recorded in the
   weights-round acceptance (specialization 0.20 / recency 0.45 improves
   the cheap arm the cost finding says a product would lean on).
3. Unspent and preserved: the final v4 test exposure; parked leads (G4
   multi-vector, G9 graph proof, df-gating variants) — reopen only with a
   new instrument or the MVP pilot.
