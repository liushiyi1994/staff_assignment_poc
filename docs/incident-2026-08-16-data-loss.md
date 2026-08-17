# Incident record — 2026-08-16: merge clobbered local benchmark data

- Written by: the orchestrator, same day
- Severity: loss of local, untracked research artifacts; **no published result
  is affected** (all were independently verified from raw records before the
  loss and are recorded in docs and acceptance records)

## What happened

The rerank-probes worker, operating in a git worktree whose `data/` entries
were symlinks back to the primary checkout, accidentally **committed three
symlinks** — `data/buckets`, `data/contributions`, `data/eval` — pointing at
the primary's own absolute paths. The orchestrator merged the branch at
~21:05 without auditing tracked paths under `data/`. Git, which treats
ignored files as expendable, **replaced the primary's real data directories
with the symlinks**, deleting their contents and leaving self-referential
loops. The merge was pushed before the damage was noticed via suite failures
minutes later.

## Root-cause chain (three failures, in order)

1. `.gitignore` ignored `data/buckets/` **with a trailing slash** — a
   directory-only pattern. A *symlink* named `data/buckets` is a file, so it
   was not ignored, and the worker's bulk `git add` picked all three up.
2. The worker created symlinks inside a repo checkout's `data/` — convenient,
   but it put repo-shaped names on absolute paths into live data.
3. The orchestrator's merge review verified code, results, ledger, and docs —
   but had no step auditing **what paths a branch tracks**. That was the gap
   that let the payload through.

## Impact

**Lost (were local-only, now deleted):** all of `data/eval` — the frozen v1
and v4 benchmark manifests, briefs, the v4 rewrite checkpoints, every run
checkpoint from v1 through v4, and all study namespaces (redesign pins,
sweeps, weights, probes); `data/contributions` (Stage 2/3 output files);
`data/buckets`.

**Survived:** `data/llm_costs.jsonl` (the complete spend ledger),
`data/parquet` (plus the v4 sprint exports, recovered from the v4 worker's
worktree copy), `data/raw`, `data/wave1`, the production Neo4j graph (the
live system), and every document and acceptance record in git.

**Already restored:** `data/buckets` regenerated deterministically from
parquet — 2,668 buckets, exactly the recorded count.

**Real casualties:** (1) raw-checkpoint reproducibility of past runs — the
published numbers stand on the pre-loss independent verifications recorded
in the acceptance records, but can no longer be recomputed from disk;
(2) the reserved final v4 test exposure — the frozen manifest is gone, and a
rebuild (rewrites ≈ $0.06) would be a *sibling* manifest, not the frozen one.

## Remediation done immediately

Tracked symlinks removed from the index and pushed; `.gitignore` hardened to
`data/**` with explicit negations for the slice-report files; real
directories restored; sprint parquet recovered; buckets regenerated and
count-verified.

## Restoration menu (owner decision, none urgent)

a. **Stop here** — recommended. The research track is concluded; the docs,
   ledger, and graph are intact; the MVP uses none of this data.
b. Rebuild the v1 manifest offline ($0, deterministic) for reference.
c. Reconstruct `data/contributions` from the live graph ($0, worker order)
   if stage-3/4 artifacts are wanted on disk again.
d. Rebuild the v4 manifest (≈ $0.06 of rewrites, sibling-not-frozen) — only
   if a future test run is ever actually wanted.

## Standing prevention rules

1. `.gitignore` keeps the `data/**` blanket; exceptions are enumerated.
2. The orchestrator's merge review now includes `git ls-files data/` (and a
   symlink audit) on every worker branch before merging. No exceptions.
3. Workers must never create symlinks under a checkout's `data/`; scratch
   copies or external paths only. This goes into every future worker prompt.
