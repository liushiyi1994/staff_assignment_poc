# Work order: Stage 3 skill normalization

- Issued: 2026-08-11 by the orchestrator
- Status: **accepted 2026-08-11** (commit `84ebd1e`, branch `agent/stage3-normalize`)

## Acceptance record (orchestrator, 2026-08-11)

Verified: 132 tests pass, ruff clean; 2,666 normalized records, exactly 9
confidence-clamped, zero high-confidence records with <5 evidence keys. All
seven worker deviations accepted, including primary-over-secondary strength
retention on canonical collapse and the three new settings keys.

**Term review:** specializations (344 canonicals) — top clusters coherent, no
bad merges found; accepted. Skills (10,623 canonicals) — top-100 ~98% clean;
two real defects (the 32-term "Mesos resource providers" grab-bag and the
Unified/External/Composing containerizer merge) fixed via `force_alias`
entries in `config/term_overrides.yaml` and a re-run — no threshold change.

**Threshold decision (worker deviation 1):** the configured 0.85/0.80 stands
and the high canonical count is accepted. The design's ~300–600 expectation
assumed short atomic terms; extraction legitimately produced compound phrases
(17,738 unique of 20,636 mentions). Aggressive merging would trade visible
precision damage for speculative recall; the union retrieval design and
frequency weighting absorb a large vocabulary, and the benchmark validation
split is the instrument for revisiting the threshold empirically.
- Phase: research track (`docs/direction-decision.md`); prior context in
  `docs/stage2-run.md`
- Base branch: `agent/openrouter-provider` (HEAD `6f6da78`)
- Suggested working branch: `agent/stage3-normalize`
- Constraints: **no LLM/API calls.** The only permitted pipeline network use is
  the one-time HuggingFace download of `BAAI/bge-small-en-v1.5` (~130MB) on the
  first real run. Tests must run offline with the embedder stubbed
  (`tests/conftest.py` already blocks sockets suite-wide). Do not modify
  `data/contributions/raw.jsonl` or any pilot/benchmark artifact; `prd (1).md`
  stays untracked.

## Objective

Harden the existing `src/capgraph/pipeline/stage3_normalize.py` for the real
2,666-record corpus, run it, and deliver term-review materials. Output:
`normalized.jsonl` (contributions with canonical skill/spec names) and
`terms.jsonl` (canonical vocabulary with aliases), per the existing skeleton's
contract.

## Tasks

1. **Confidence clamp.** When loading raw contributions, clamp `confidence` to
   at most `medium` where `len(evidence_ticket_keys) < 5` (extraction rubric;
   affects 9 records — see `docs/stage2-run.md`). Applied in the normalized
   output only; `raw.jsonl` is immutable extraction record.
2. **Deterministic outputs.** Same inputs + settings + overrides must produce
   byte-identical `normalized.jsonl` and `terms.jsonl`: sort terms before
   clustering, sort cluster members/aliases and output rows, and break
   canonical-name frequency ties deterministically (frequency, then
   lexicographic). No unseeded randomness.
3. **Scale check.** Report unique skill/spec term counts before clustering.
   sklearn agglomerative clustering is O(n²) memory in unique terms; if the
   pairwise matrix would exceed ~4GB, escalate with measurements rather than
   silently switching algorithms.
4. **Overrides location.** Move `term_overrides.yaml` from `data/contributions/`
   (git-ignored) to `config/term_overrides.yaml` (tracked, initially empty with
   a commented example of `never_merge` / `force_alias`), and update the
   module docstring. Curated overrides are configuration, not generated data.
5. **Tests (offline, embedder stubbed).** Cover: clustering merges
   near-duplicates and respects the threshold; `never_merge` splits;
   `force_alias` applies; canonical = most-frequent with deterministic
   tie-break; within-contribution dedup after mapping; confidence clamp;
   determinism (two runs, identical bytes); skips excluded. Existing suite
   stays green; ruff clean.
6. **Run Stage 3 on the real corpus** and report: canonical skill and
   specialization counts (design expectation ~300–600 skills, ~30–60
   specializations — deviation is a finding, not a failure; the extraction
   produced fine-grained terms, so raw counts will be high), cluster-size
   distribution, and the top-100 canonical skills by frequency with their
   aliases (for the orchestrator's bad-merge review).

## Acceptance criteria

- `uv run python -m pytest -q` green, `uv run ruff check .` clean.
- Re-running stage 3 with unchanged inputs is byte-identical.
- `normalized.jsonl` has 2,666 records (skips excluded), every skill/spec name
  maps into `terms.jsonl` canonicals, and exactly 9 records had confidence
  clamped.
- Top-100 report delivered in the report-back for term review.

## Out of scope

- Applying override judgments (the orchestrator's term review does that; a
  follow-up applies the resulting `term_overrides.yaml` and re-runs).
- Stage 4, Neo4j, grading, and any LLM call.

## Report back

Working branch and commits, term-count statistics, the top-100 skills report,
test/ruff output, deviations with justification; escalate rather than
improvise.
