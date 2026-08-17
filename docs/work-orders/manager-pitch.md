# Work order: manager-pitch package

- Issued: 2026-08-12 by the orchestrator
- Status: closed — doc accepted 2026-08-13, demo accepted 2026-08-14
- Phase: research-track wrap-up (base is `main` after the benchmark v3 merge)
- Suggested working branch: `agent/manager-pitch`
- LLM authorization: **none for the document.** The optional demo notebook's
  live queries need separate owner approval (estimate ≤ $0.50 under stage name
  `demo`); do not run them until the order records that approval.
  - **Demo approval recorded 2026-08-14 by the owner: ≤ $0.50 under stage
    name `demo`**, pinned to the v2-frozen configuration (window 15, profile
    view, no BM25 arm, `rerank` prompt — the strongest measured Hit@1/MRR
    setting), with the configuration stated in the notebook output. Note:
    the engine's default query stage label is `stage6_pilot`
    (`llm.query_stage`); the demo must log under `demo` instead.

## Objective

An owner-presentable package that tells the research track's story to a
non-technical manager, honestly and in plain language: what was built, how it
was evaluated, what the numbers mean, what it costs, and what a product MVP
would take. The audience has not read any project doc and will not.

## Deliverables

1. **`docs/manager-pitch.md`** — executive-readable, plain language (spell out
   every term of art on first use), structured as:
   - *The problem and what was built:* evidence-backed capability profiles from
     ticket history; ranked, explainable shortlists with cited evidence. One
     short paragraph on architecture, no jargon.
   - *How it was evaluated:* temporal holdout against historical assignments,
     leakage guards, and the honesty discipline (validation-only tuning, one
     test run per version, paired statistics, measured noise floor). One
     paragraph, framed as "why these numbers can be trusted."
   - *Results:* the v1/v2/v3 + baselines table from `docs/eval-results.md`,
     transcribed not restyled, with the calibration context: published systems
     on comparable roster sizes report Top-1 0.33–0.35 / Top-10 ~0.81
     (TriagerX, arXiv:2508.16860), and the label-noise literature puts 18–44%
     of historical assignee labels in doubt — parity with the published
     frontier, stated without inflation.
   - *The cost finding:* the deterministic ranking alone reaches Hit@5/Hit@10
     within noise of the full system at ~$0.004 and ~3s per query versus
     ~$0.03 and ~30s; the LLM stage buys cited-evidence explanations, and (in
     the v2 configuration) top-1 precision. This is the finding a product
     decision should be built on.
   - *Limitations and ethics:* research PoC on pseudonymous public data; not
     usable for real hiring/staffing/performance decisions (CLAUDE.md framing,
     non-negotiable); single-label ground truth undercounts genuinely
     acceptable assignees.
   - *What transfers to an MVP:* the list in `docs/agent-handoff.md` "Next
     phase", one line each.
2. **Optional (gated on the owner's demo-spend approval): demo notebook** —
   plan Task 8: one live query producing a cited shortlist, run against a
   **stated** engine configuration. Recommendation to record in the notebook:
   the v2-frozen configuration (strongest measured Hit@1/MRR), with one line
   noting v3 exists and what it changed. Convert + launch via `make demo`.

## Constraints

- Every number must be transcribed from `docs/eval-results.md` or
  `data/llm_costs.jsonl` — no recomputation, no rounding that flatters, no
  cherry-picking a single version's best metric without its worst.
- No benchmark re-runs, no test-split access, no new experiments.
- The pitch must present v3's Hit@1 regression as plainly as its Hit@10 gain;
  the honesty discipline is itself part of the pitch.
- Suite stays green (`uv run python -m pytest -q`, `uv run ruff check .`) if
  any code (notebook tooling) is touched.

## Acceptance criteria

1. Facts reconcile: every metric, cost, and count in the pitch doc traces to
   an artifact in the repo (spot-checked by the orchestrator).
2. Plain-language test: no unexplained term of art; an intelligent reader with
   zero context can follow the argument.
3. Ethics/limitations section present and unhedged.
4. If the demo runs: approval recorded first, spend ledgered under `demo`,
   configuration stated in the notebook output.
5. Report back: the pitch doc, what was included/omitted and why, demo status,
   and any deviations.

## Acceptance record (2026-08-13, orchestrator)

Reviewed independently on `agent/manager-pitch` (merged with this record).

- **Facts reconcile.** Test-split table matches my own recompute from raw v3
  run records (done at v3 acceptance) and the frozen v1/v2 tables. Ledger sums
  to $25.1267 over 4,199 calls exactly as stated. Slice figures (82,703 /
  62,554 / 316, five projects), bucket count (2,668), capability links
  (19,950), briefs (150), and the 10,630 + 344 = 10,974 term records all
  verified against the artifacts.
- **Plain-language, ethics, and honesty criteria met.** Every term of art is
  defined; the v3 Hit@1 regression is in the opening summary; the ethics
  section is categorical; the citation-validity correction and both rejected
  techniques are included; the source map makes spot-checking mechanical.
- **Demo correctly not run** — no approval was recorded in the order, no LLM
  calls were made (ledger unchanged at $25.1267).
- **Deviations accepted:** prose ratios rounded conservatively; scale counts
  sourced from data artifacts with each verified directly.
- Commended: making explicit that the v3 default configuration has no
  demonstrated top-1 advantage over its own deterministic arm — the order's
  v2-scoped phrasing would otherwise have misled a reader of the current
  defaults.

Demo notebook remains available under this order if the owner approves ≤$0.50
under stage name `demo`, pinned to a stated configuration (recommendation:
v2-frozen, noted in the order).

## Demo acceptance addendum (2026-08-14, orchestrator)

The approved demo ran and is merged. Verified: spend $0.0690 under stage
`demo` (4 ledger calls = two executions — headless validation then the final
run — of the single live query, per instruction), within the $0.50 ceiling;
configuration pinned to the frozen v2 baseline and read from the repo's
recorded values rather than retyped, with the pinning explained in the
notebook itself; only `notebooks/demo.py` and `.gitignore` touched; suite 430
passed, ruff clean. This order is now fully closed.
