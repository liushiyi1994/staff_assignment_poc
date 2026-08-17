# Work order: improvement wave 1 — free, offline items from the backlog

- Issued: 2026-08-14 by the orchestrator
- Status: accepted 2026-08-15 by the orchestrator (see Acceptance record)
- Phase: post-research-track improvements (base is `main` after the demo merge)
- Scope source: `docs/improvement-backlog.md` (orchestrator-reviewed; sampled
  claims verified against artifacts on 2026-08-14). This order implements the
  backlog's "Wave 1" — gaps G1, G3a, G5, G6, G8, G11a, G13 — exactly as scoped
  there, with the discipline constraints below.
- Suggested working branch: `agent/improvement-wave1`
- LLM authorization: **none.** $0 order. No extraction re-runs (G1's re-run and
  G3d are explicitly out of scope), no benchmark runs, no query-engine live
  calls. The retired 120-case test split is not touched in any form.
  - Add-on **G7 (reverse-order probe)**: **approved by the owner 2026-08-14,
    ceiling $2.** One 30-case validation-split run with candidates presented
    in reverse deterministic order, under stage name `probe_order`, against
    the current default (v3) configuration, reported as paired per-case deltas
    against the 0.100 noise floor. Success test per the backlog: a Hit@1 move
    beyond the floor means presentation order dominates the re-rank; within
    the floor means the score-order prior is doing its job. This is the only
    LLM spend in this order.

## Objective

Ship the backlog's free improvements as measured, flag-guarded engine/pipeline
features, and produce the measurements that Wave 2+ decisions need — without
pretending anything is validated that the retired benchmark can no longer
validate.

## Discipline constraints (read first)

1. **Defaults do not flip on unvalidated levers.** Anything that changes
   ranking or retrieval behavior (G3a, G5, G6, G11a) lands behind a config
   flag, default OFF, with an offline evidence report. Flags flip to default
   only when a benchmark (backlog G12) or the MVP pilot can measure them.
   Exceptions — allowed to change default behavior now — are correct by
   construction: G1's sentence-boundary truncation (strictly better than a
   mid-word slice; extraction is NOT re-run, so current data is unaffected)
   and G8's surfacing of an already-parsed field.
2. **Offline re-scoring reports are read against the 0.100 noise floor** and
   reported as directional evidence, never as validation. Use the existing
   checkpointed score components and `combine_parts()` so swept arithmetic is
   the engine's own.
3. Stage 0 stays a pure provenance layer: no LLM, no paraphrase (backlog G1
   constraint).
4. Suite green (`uv run python -m pytest -q`), ruff clean; every new flag
   documented in `config/settings.yaml` with a comment naming its backlog ID.

## Scope, per backlog item

- **G1 — truncation.** First measure: share of descriptions exceeding 1,200
  chars after code-stripping, and share that are majority log/boilerplate
  noise. Then implement sentence-boundary truncation in Stage 0. If the
  measurement shows truncation is rare, say so and recommend closing G1 as
  not-worth-fixing. Do NOT re-run Stage 2.
- **G3a — vocabulary frequency gating.** Config-flagged document-frequency
  floor in Stage 3: sub-floor terms attach as aliases to their nearest
  canonical instead of being canonical. Report vocabulary size and df
  distribution before/after (target-free; the backlog estimates ~10,630 →
  ~1,500). Flag OFF by default.
- **G5 — confidence into the score.** Add `confidence` as a score component
  or `evidence_strength` multiplier (config-flagged, OFF). Offline paired
  re-score on checkpointed validation components; report deltas vs noise.
- **G6 — strength into specialization match.** Carry `strength`
  (primary/secondary, verified distribution 2,853/2,663) onto the Stage 4
  projection edge and weight matched specializations by it (config-flagged,
  OFF). Same offline re-score report. Requires offline Stage 4 + 5 re-runs;
  note the `structured_strength` name collision the backlog flags — rename or
  comment to disambiguate.
- **G8 — surface `count`.** Engine output marks the top-`count` per role as
  the proposed set, remainder as alternates. No scoring change. Team
  composition is out of scope (needs G12).
- **G11a — activity currency.** Report the distribution of quarters-since-
  last-contribution across the 316 people. Implement a config-flagged (OFF)
  activity-currency signal per the backlog's option (a). No hard exclusion
  windows (option b is rejected in the backlog and stays rejected).
- **G13 — small items.** Declare `httpx` as a direct dependency in the next
  `uv lock` refresh (authorized here, $0). The stale pitch figures are already
  corrected on `main` — verify, don't redo.

## Deliverables

1. Implementation as above, tested (toy-data unit tests per convention).
2. `docs/improvement-wave1-report.md`: the G1 truncation measurement, G3a
   vocabulary before/after, G5/G6 offline re-score tables read against the
   noise floor, the G11 activity distribution, and a per-item recommendation
   (keep flag OFF / propose default-flip pending G12 / close as not worth it).
3. Report back: summary per item, test/ruff output, ledger delta (must be $0),
   deviations. Escalate rather than improvise.

## Acceptance criteria

1. All flags OFF by default; engine/benchmark behavior with flags OFF is
   byte-identical for the three baselines (they are deterministic — re-score
   one checkpointed validation namespace to prove it).
2. Suite green, ruff clean, ledger unchanged.
3. The report's claims trace to artifacts; recommendations distinguish
   "measured" from "reasoned".

## Acceptance record (2026-08-15, orchestrator)

Reviewed independently in a clean worktree; merged onto the post-v4 `main`
with this record (one Makefile both-sides-added conflict, resolved as the
union at merge time; merged suite 489 passed, ruff clean; branch suite 462
passed on my own run).

- **Flag discipline verified in `config/settings.yaml`:**
  `vocabulary.min_document_frequency: 0`, `confidence_signal.mode: "off"`,
  `specialization_strength.enabled: false`, activity currency off — all
  default-OFF with the rationale written into the config. G1's
  sentence-boundary truncation is default-on as the order allows (correct by
  construction, with a keep-fraction fallback so a long opening sentence
  degrades to a word boundary rather than losing the description).
- **Baseline parity:** the three deterministic baselines re-run and diffed
  record-by-record against the frozen v3 validation namespace — 90/90
  identical, configuration digest `1b74f4a2022b5cd7` unchanged, frozen
  namespaces untouched.
- **Ledger:** $0.9454, all 63 calls under `probe_order`, within the $2
  ceiling; every other item $0.
- **Finding of record — G7.** Reversing the re-rank's candidate order halves
  Hit@1 (0.400 → 0.200, 0 wins / 6 losses, McNemar p = 0.031) and moves MRR
  −0.132 (95% CI [−0.234, −0.040]) — beyond the 0.100 noise floor, against a
  score-arm gauge that moved +0.100 the other way. With v3's arms this
  triangulates: ordered 0.400 / shuffled 0.267 / reversed 0.200.
  **Presentation order dominates the re-rank**; the card prints the
  deterministic score and the model does not use it. This retro-explains
  v2's "better input, same output" and where v2+v3's ~$11.58 of re-rank
  lever spend went.
- **Orchestrator decisions on the escalations:**
  1. Standing guidance, effective now: **re-rank lever tuning is paused**
     until the re-rank prompt is redesigned around the position finding, and
     any future re-rank measurement must carry a position-control arm
     (~$1, authorized in whichever order runs it).
  2. Recorded: the extraction rubric produced zero "low" confidence in 2,668
     records — fold into the next extraction-prompt revision.
  3. Recorded: G11a cannot be validated under frozen-roster benchmarks; it is
     an MVP-phase item and its flag stays off.
- **Deviations accepted:** the pitch-figure correction and its
  "research-track-at-2026-08-14" scoping (kept as written — it keeps the
  document's copied-from-artifacts claim literally true); G5/G6 person-level
  stand-ins with sensitivity bounds (the checkpoint stores aggregate
  components, so per-match values are unrecoverable); the pre-existing
  `make eval-v3-pool-levers` exit (the v3 score-checkpoint drift guard firing
  against post-v3 default settings — the guard working as designed; needs a
  pinned-config override if that target is ever re-run).
- **Recommendations recorded for v4 sweeps:** G3a floor-3 is the first sweep
  candidate (10,630 → 1,752 canonicals, the backlog's estimate confirmed);
  G6 only against its constant-scale control (which reproduces nearly all of
  the naive offline gain); G5 is not worth a sweep (zero low-confidence
  records — the signal is near-constant); G1 stays open for the MVP's
  option (d), not for re-extraction here.
