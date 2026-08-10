# Agent handoff

Verified 2026-08-10. This is the execution entry point for work after the accepted
benchmark/data foundation.

## Outcome

The TAWOS research foundation is complete and reproducible.

**Direction decided 2026-08-10:** the research track is active; the product MVP
is deferred to a later, separately scoped phase. Rationale, objective, storage
checkpoint, and data boundary are recorded in `docs/direction-decision.md`. The
project now runs under the orchestrator/lead/worker operating model documented
in `CLAUDE.md`; the first open work order is
`docs/work-orders/stage2-pilot-gate.md` (no API calls permitted).

Recommended disposition (unchanged): preserve PR #1 as the research/evaluation
foundation; product-MVP work starts as a separately scoped branch or PR after
the PRD is accepted and moved into a canonical repository path.

## Read first

1. `prd (1).md` — untracked, user-owned product MVP proposal. Preserve it in place
   until the orchestrator decides whether it is canonical and where it belongs.
2. `docs/real-data-validation.md` — authoritative accepted counts, artifact hashes,
   safety checks, and environment facts for the TAWOS run.
3. `docs/implementation-plan.md` — current research-pipeline task sequence.
4. `docs/tech-design.md` — current Neo4j-oriented research design.
5. `docs/data-provenance.md` — TAWOS license, research-only terms, and identity limits.

Completion criterion: the worker can state which document governs the selected
track and can explain how the other track remains isolated.

## Repository state

| Item | Current state |
|---|---|
| Repository | `liushiyi1994/staff_assignment_poc` |
| Branch | `agent/benchmark-foundation` |
| HEAD / remote head | `5dbfd8ca92866b09e80c618a0fd9ecec0d29f52e` |
| Base | `main` at `39eb4c1` |
| Pull request | Draft PR #1, `build benchmark data foundation` |
| PR URL | <https://github.com/liushiyi1994/staff_assignment_poc/pull/1> |
| PR status | Open, mergeable, no reviews or CI checks recorded |
| Existing user change | Untracked `prd (1).md`; keep outside any commit unless approved |
| Runtime | Colima stopped; local generated data remains present |
| Stage 2 outputs | None; `data/contributions/` has no generated contribution files |
| LLM state | No `.env`, no `data/llm_costs.jsonl`, and no recorded API call |

The working tree was otherwise clean before this handoff document and its
`CLAUDE.md` pointer were added.

## Accepted foundation

- TAWOS v1.1: 458,232 issues, 39 projects, 12 repositories.
- Configured slice: MESOS, FAB, TIMOB, DM, EVG.
- Stage 0: 82,703 tickets, 316 eligible project-qualified people, five projects.
- Stage 1: 2,668 deterministic buckets and 37,475 conserved evidence tickets.
- Privacy: 52 email and 691 mention replacements; zero residual protected patterns.
- Benchmark: 24,522 candidates, 3,320 eligible before sampling, 150 selected
  cases (30 validation / 120 test).
- Source audit: 10/10 deterministic categories passed without emitting row text or
  identifiers.
- Graph, retrieval, Neo4j, stages 2–5, and all LLM/API calls remain unrun.

Current verification on 2026-08-10:

```text
uv sync --all-extras --locked  -> 187 packages resolved, 162 checked
make test                      -> 57 passed, 14 SQLite fixture deprecation warnings
uv run ruff check .            -> clean
```

The six local generated artifacts still match every SHA-256 recorded in
`docs/real-data-validation.md`.

Completion criterion: `make test` and `uv run ruff check .` pass, and any rebuilt
foundation artifact matches the validation ledger unless an intentional versioned
contract change explains the new digest.

## Direction gate

The current implementation and the product PRD answer different questions:

| Concern | Current branch | Untracked product PRD |
|---|---|---|
| Goal | Research historical-assignee retrieval | Pilot evidence-backed staffing workflow |
| Data | Public TAWOS Jira history | Manually ingested Jira, GitHub, docs, and conversations |
| Identity | Project-qualified opaque pseudonyms | Canonical roster with name, email, optional GitHub username |
| Workflow | Offline deterministic pipeline | Curator ingest, resolve, review, commit, delete; ProdOps query/feedback |
| Storage | Planned Neo4j graph + vector index | Relational entity model + vector index is sufficient for MVP |
| Success | Temporal benchmark metrics | Repeated accepted shortlists and staffing decisions influenced |

Ask the orchestrator to select one branch:

1. **Research track:** finish the TAWOS extraction/retrieval experiment.
2. **Product MVP track:** rebaseline around the PRD, curator state machine,
   canonical roster, provenance/deletion, and relational/vector storage.
3. **Parallel tracks:** retain this repository/PR as the evaluation harness and
   begin the product MVP in a clearly separated branch or repository.

Record the choice in a tracked issue or design document. Completion criterion:
the selected track has an explicit objective, storage decision, data boundary,
and acceptance test; implementation work can be judged against that record.

**Resolved 2026-08-10:** option 1 (research track) selected, with the product
MVP as a planned successor phase rather than a parallel track. The record with
objective, storage decision, data boundary, and acceptance test is
`docs/direction-decision.md`.

## Research-track next task

The next research milestone is a deterministic Stage 2 pilot of roughly 30
buckets, followed by human review before any broader extraction. The current
`stage2_extract.py` is not a safe pilot command yet: it processes every unfinished
bucket, catches failures and continues, and has no deterministic pilot manifest or
result-quality summary.

Implement the pilot gate before making an API call:

1. Add a deterministic, project-stratified pilot selector (recommended: six
   buckets per configured project) with a versioned seed/manifest.
2. Add CLI controls for pilot manifest, limit, and prompt-only dry run. Preserve
   checkpoint behavior without mixing pilot and full-run outputs.
3. Validate each response against the `Contribution` contract and require every
   evidence key to belong to its source bucket. Report success, skip, invalid,
   and failed counts; fail the command when the acceptance threshold is missed.
4. Make cost control prospective: known model pricing, per-call estimate, hard
   pre-call ceiling, and an explicit pilot budget. The current gateway checks only
   already-spent cost and assigns zero cost to unknown model names.
5. Add fixture tests for selection determinism, dry-run behavior, checkpointing,
   evidence-key validation, failure exit status, and budget refusal.
6. Obtain explicit approval for the provider, current model ID, credentials, and
   dollar ceiling. Then run only the pilot.
7. Review all pilot contributions for grounding, specificity, sensitive data,
   supported capability terms, evidence-key accuracy, and skip quality. Record
   the review and prompt revision decision before expanding scope.

Completion criterion: 30 deterministic buckets are accounted for, no output
escapes its source evidence, actual cost stays within the approved pilot ceiling,
and the orchestrator accepts the qualitative review.

## Product-MVP next task

If the PRD governs, start with architecture and domain reconciliation rather than
Stage 2 execution:

1. With approval, move `prd (1).md` to a canonical tracked name such as
   `docs/product-mvp-prd.md`.
2. Define the minimum state model for `Person`, `SourceArtifact`,
   `PersonContribution`, capability terms, review decisions, deletion, derived
   projections, shortlist queries, and pilot feedback.
3. Record the relational/vector storage decision and keep TAWOS benchmark data
   physically separate from real roster/source data.
4. Define identity-resolution outcomes (exact, ambiguous, unresolved), curator
   review transitions, provenance requirements, and source-deletion effects.
5. Replace the current implementation plan with product-sized vertical slices:
   manual ingestion -> resolution/review -> committed memory -> hybrid shortlist
   -> evidence display -> feedback.
6. Specify retention, sensitive-signal exclusion, pilot access, and audit needs
   before ingesting real employee, client, or casting material.

Completion criterion: one approved vertical-slice plan traces a source through
review to a queryable, deletable contribution and has executable acceptance tests.

## Guardrails

- Keep TAWOS use research-only and preserve its raw archive outside Git.
- Keep generated Parquet, buckets, manifests, contributions, and cost logs ignored.
- Preserve temporal boundaries, resolution-time ownership, project-qualified
  identities, shared privacy sanitization, and fixed-seed manifests.
- Route every model call through `src/capgraph/llm.py` after the pilot gate is
  accepted and credentials/budget are authorized.
- Execute stages individually against their acceptance gates; reserve full-pipeline
  commands for a track whose preceding stages are already accepted.
- Preserve `prd (1).md` and any unrelated workspace changes when staging commits.

## Handoff completion

Before ending the next session, update this document's repository state, record
the chosen direction and completed criterion, link the relevant PR/issue, and
leave the working tree with every unrelated user file preserved.
