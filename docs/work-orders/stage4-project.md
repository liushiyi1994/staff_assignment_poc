# Work order: Stage 4 projections

- Issued: 2026-08-11 by the orchestrator
- Status: **accepted 2026-08-11** (commit `bce8474`, branch `agent/stage4-project`)

## Acceptance record (orchestrator, 2026-08-11)

Independently verified: 148 tests pass, ruff clean; 19,950 edges covering all
316 people; zero rows with last_used on/after the 2019-01-01 cutoff; decay
recomputation matches on a 500-row sample; evidence_count == len(contribution_ids)
throughout. All four worker deviations accepted (per-contribution evidence sets,
strict period parsing, helper/IO structure, preserved build_capabilities
signature). Noted for the benchmark phase: 86% of skill edges rest on a single
contribution — structured retrieval will lean on the dense specialization edges
and the vector arm, as anticipated in the Stage 3 threshold decision.
- Phase: research track; prior context `docs/work-orders/stage3-normalize.md`
- Base branch: `agent/stage3-normalize` (worker branch, includes accepted
  Stage 3 + term overrides)
- Suggested working branch: `agent/stage4-project`
- Constraints: no LLM/API calls, no network. Do not modify `raw.jsonl`,
  `normalized.jsonl`, `terms.jsonl`, or any accepted artifact.

## Objective

Harden and run `src/capgraph/pipeline/stage4_project.py`:
`normalized.jsonl` → `capabilities.jsonl` (per-person HAS_SKILL /
HAS_SPECIALIZATION payloads with evidence counts, last-used dates, and
recency decay at the frozen cutoff). The existing skeleton is close to
correct — keep its semantics: decay anchored to `dataset.holdout_cutoff`
(never wall-clock), and hard rejection of any contribution period not wholly
before the snapshot.

## Tasks

1. **Determinism.** Sort output rows (person_id, kind, term) and each row's
   `contribution_ids`; two runs over the same input must be byte-identical.
2. **Tests (offline).** Cover: `period_end` quarter arithmetic (incl. Q4 and
   leap-year Q1); decay halves at exactly one half-life and is 1.0 at the
   snapshot day; `as_of` anchoring (result independent of wall-clock date);
   rejection of a period ending on/after the snapshot; aggregation counts,
   last-used max, and contribution-id collection across multiple
   contributions; determinism.
3. **Run on the real corpus** and report: edge counts by kind, people covered
   (expect 316), decay-score distribution summary, and sanity examples
   (one person's top skills by evidence count).

## Acceptance criteria

- `uv run python -m pytest -q` green, ruff clean; re-run byte-identical.
- `capabilities.jsonl` validates against `models.PersonCapability`; every
  `person_id` is one of the 316 Stage 0 people; every term maps into
  `terms.jsonl` canonicals; no `last_used` on/after 2019-01-01.

## Out of scope

Stage 5 / Neo4j (gated on the storage checkpoint in
`docs/direction-decision.md`), grading, query engine.

## Report back

Branch/commits, the run statistics above, test/ruff output, deviations;
escalate rather than improvise.
