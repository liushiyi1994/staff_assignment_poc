# Work order: incident restoration — green suite, rebuilt artifacts

- Issued: 2026-08-16 by the orchestrator, per the incident record
  (`docs/incident-2026-08-16-data-loss.md`) and the owner's go-ahead
- Status: open
- Phase: incident remediation (base is `main` @ `85243cf`)
- Suggested working branch: `agent/incident-restoration`
- LLM authorization: **none.** $0 order. No model calls of any kind; the v4
  manifest rebuild (which would need ~$0.06 of rewrites) is explicitly OUT of
  scope per the owner's choice of the minimal menu.

## Objective

Return the repository to a self-consistent state after the 2026-08-16 data
loss: suite green, deterministic artifacts rebuilt, graph-derived artifacts
reconstructed, and every restored file labeled for what it is.

## Work items

1. **Skip-guards for artifact-dependent tests.** Four tests read local
   `data/eval` artifacts deleted in the incident
   (`tests/test_benchmark_v2.py` ×2, `tests/test_eval_harness.py` ×1,
   `tests/test_weights_round.py` ×1). Guard each with the repository's
   existing skip-when-artifact-absent convention (two such skips already
   exist — match them), so the suite is green without the artifacts and the
   tests run again wherever the artifacts exist. Do NOT delete or weaken any
   assertion; a skipped test must still run fully when its substrate is
   present.
2. **Rebuild the v1 benchmark manifest, offline.** The v1 manifest build is
   deterministic (fixed seed 20260713, project stratification, recorded
   exclusion rules) from the parquet slice. Rebuild
   `data/eval/benchmark_manifest.v1.jsonl` and `data/eval/briefs.jsonl` and
   **verify against the published record**: 150 selected cases, the recorded
   split sizes (30/120), and the exclusion-reason counts recorded in
   `docs/improvement-backlog.md` G12 (5,342 / 4,992 / 4,026 / 4,015 / 3,170 /
   1,456 / 1,365 / 150). Report the count comparison; escalate on any
   mismatch rather than shipping a manifest that differs from the record.
3. **Reconstruct `data/contributions` from the production graph.** The Neo4j
   graph (verified counts: Person 316, Contribution 2,666, Skill 10,630,
   HAS_SKILL 17,589, HAS_SPECIALIZATION 2,361) holds the accepted Stage 2/3
   content. Export it back into the stage-3/4 file shapes the pipeline
   expects (terms, capabilities, contribution records), read-only against
   the graph, as a tested feature (not a one-off script). Verify counts
   against the records above and the 19,950 projection edges. **Label every
   reconstructed file** with a `reconstructed_from_graph: 2026-08-16` marker
   in whatever metadata slot the format allows — these are faithful exports
   of the accepted content, not the original checkpoint files.
4. **Do not touch:** `data/parquet` (restored), `data/buckets` (regenerated,
   2,668 verified), `data/llm_costs.jsonl`, `data/wave1`, the production
   graph (read-only), anything under `docs/` except a short completion note
   in the incident record's restoration menu.

## Constraints

- $0: zero ledger delta, verified in the report.
- Suite green (`uv run python -m pytest -q`) with and — where artifacts are
  present — without skips firing; `uv run ruff check .` clean.
- **No symlinks under `data/` anywhere, in any checkout.** Scratch copies
  only. (`.gitignore` now blankets `data/**`; do not fight it.)
- Stage 0–1 outputs are already restored; do not re-run them.

## Deliverables

1. The guards, the rebuilt v1 manifest + briefs with the count comparison,
   the graph-export feature with its verification, and the completion note
   in the incident record.
2. Report back: per-item verification (counts vs records), test/ruff output,
   ledger delta (must be $0), deviations. Escalate rather than improvise.

## Acceptance criteria

1. Suite green on the branch; the four guarded tests still assert fully when
   their substrate exists.
2. v1 manifest counts match the published record exactly, or the mismatch is
   escalated unshipped.
3. Reconstructed contribution artifacts verified against the recorded graph
   counts and labeled as reconstructions.
4. Zero ledger delta; no symlinks under `data/`; graph untouched.
