# Work order: final weights round — the control's lead, by mechanism, gated to the end

- Issued: 2026-08-15 by the orchestrator, on the owner's choice of option (a)
- Status: accepted 2026-08-16 by the orchestrator (see Acceptance record)
- Phase: research track, final round (base is `main` @ `eed6847`)
- Suggested working branch: `agent/weights-round`
- LLM authorization: **granted 2026-08-15 by the owner — ceiling $10 total**
  across stage names `weights_val` (ONE paid validation arm, ~$2, gated) and
  `weights_test` (ONE test run, ~$7, gated — this is the **second and final
  planned exposure** of the v4 test split). Both gates below must pass in
  writing before their stage spends. Escalate before proceeding if any
  projection exceeds $10; in-session raises must be recorded in this file at
  the time.

## Objective

Decide, at the lowest possible cost, whether the deterministic-score weight
retune suggested by the G6 constant-scale control is real — and if it
propagates through the re-rank, freeze it and produce the research track's
final headline with the last planned v4 test exposure. If it is not real or
does not propagate, conclude the research track with the existing v4
baseline headline and the exposure unspent.

## What is established (read first)

- The G6 control — a flat down-weighting of `specialization_match` — beat
  both the base weighting and G6 itself on the offline deterministic arm
  (det-Hit@1 0.214 vs 0.143), but its constant was **fitted on the 28-case
  split** (`docs/deterministic-sweeps-report.md`); it is a lead, not a result.
- v2 measured this lever class before: a mechanism-chosen weight retune
  improved the deterministic arm and did **not** move the full system.
- The re-rank is order-robust (rerank-redesign acceptance): a retune can move
  the full system only by changing **window membership** (who reaches the
  top 32), not by reordering inside it.
- The instrument's own floors (deterministic-sweeps acceptance): Hit@1 0.036 /
  Hit@5 0.071 / Recall@5 0.095 / MRR 0.034. All paid-arm claims read against
  these.
- Score components for the 28 validation cases are checkpointed
  (`data/eval/sweeps/offline/base/`), so the entire offline tier is exact
  arithmetic through `combine_parts()` — $0, no model calls.

## Tiers and gates

1. **Tier 0 — offline mechanism sweep ($0).** Sweep the four score weights on
   the checkpointed components with pinned pools/parses, selecting v2-style:
   marginal effects that are monotone across the grid, an adopted vector
   inside a robust plateau, minimal deviation from current weights — never
   the best row of a 28-case grid. Report det-Hit@K/MRR, and **window
   membership diff** (cases whose top-32 set changes, and whether any truth
   person enters or leaves) for the candidate vector vs current weights.
2. **GATE 1 (to `weights_val`, ~$2):** passes only if the candidate vector
   (a) improves the deterministic ordering on a plateau, AND (b) changes
   window membership in the truth-relevant direction on enough cases that
   propagation is arithmetically possible. If the window population barely
   moves, the full system provably cannot improve — stop at $0 and conclude.
3. **Tier 1 — one paid validation arm (`weights_val`).** Full system under
   the candidate weights, pinned parses, paired case-by-case against the
   rerank-redesign baseline arm, read against the measured floors.
4. **GATE 2 (to `weights_test`, ~$7):** passes only if the paid arm beats the
   baseline beyond the per-metric floor on the metrics the retune targets,
   with the paired win/loss pattern supporting it (not one lucky aggregate).
   The orchestrator reviews the gate-2 evidence **before** the test run —
   escalate with the tier-1 table and wait for explicit go-ahead.
5. **Tier 2 — freeze and final test (`weights_test`).** Freeze first:
   `docs/weights-freeze-config.md` written before the run and not edited
   after (v2/v3 pattern); defaults flipped in `config/settings.yaml` as part
   of the freeze. Then ONE test-split run of `capgraph_full` and
   `capgraph_score` under the frozen config. **Checkpoint the intent parses
   this time** — the missing test-parse checkpoint is what forced a paid
   baseline in the redesign study; fix it as a tested feature. Report the
   new config's absolute test numbers and its paired comparison against the
   deterministic baselines (same-run, clean); the comparison against the
   *old* config's test run is cross-run and must be labeled indicative —
   retrieval draws differ and our standing rule applies.

## Constraints

- Validation tuning only; the test split is touched only in tier 2, once,
  after the freeze commit. Zero test-split reads before that.
- All checkpoints under `data/eval/weights/`; frozen namespaces untouched;
  production graph untouched (this order needs no graph changes).
- Suite green, ruff clean; "measured" vs "reasoned" distinct; every claim
  against the measured v4 floors.
- If any gate fails, that is the deliverable: the research track concludes on
  the v4 baseline. Say so plainly; do not shop for a passing variant.

## Deliverables

1. `docs/weights-round-report.md`: tier-0 sweep with marginal effects,
   plateau, and window-membership diff; gate decisions with their evidence;
   tier-1 paired table if run; tier-2 freeze doc + final tables if run.
2. Report back: findings, gate outcomes, spend by stage (≤ $10), test/ruff
   output, deviations. Escalate at gate 2 regardless of how clear it looks.

## In-session decisions (recorded at the time)

- **2026-08-15, gate 1 — STOP, recorded before any spend.** Tier 0 ran offline on the
  sweeps study's `base` checkpoint and evaluated gate 1 in code
  (`capgraph.eval.weights_round.gate_one`). The deterministic half passes: the
  mechanism-selected vector (`specialization_match` 0.25 → 0.20, `recency` 0.40 →
  0.45) moves det-Hit@1 0.143 → 0.214 (+0.0714, twice the measured floor) on a
  plateau — 70 of 81 neighbouring vectors beat the current weighting, none is worse.
  The propagation half fails: 0 truth people enter or leave the 32-card window, on
  this vector, on all 270 vectors of the mechanism direction, and — for the re-rank's
  own rank-1 choices — on all 13,776 vectors of the whole simplex. `weights_val` was
  therefore never called; **$0.00 of the $10 ceiling is spent**, gate 2 was never
  reached, no freeze document was written and no default was flipped.
- **Ceiling.** No in-session raise was requested or granted; the authorization stands
  at **$10 total** across `weights_val` and `weights_test`, returned unspent.
- **Deviations.** The tier-0 machinery landed as a tested module
  (`src/capgraph/eval/weights_round.py`) with its report renderer
  (`report_weights_round.py`), mirroring the sweeps study's split between measurement
  and prose. The tier-1/tier-2 paid-arm machinery was **not** written, because the
  gate closed before it could be used; the report records what a later paid weights
  arm would need (a fresh pin under the candidate weights — the existing pin stores
  profiles only for the window it was captured under).

## Acceptance criteria

1. Gates enforced in writing and in order; gate-2 run only after orchestrator
   go-ahead; ledger reconciled per stage.
2. Tier-0 selection is mechanism-based (monotone marginals + plateau), not
   leaderboard; window-membership arithmetic shown.
3. If tier 2 runs: freeze doc predates the run and is unedited after; intent
   parses checkpointed; defaults flipped at freeze, not before.
4. Frozen namespaces and production graph untouched.

## Acceptance record (2026-08-16, orchestrator)

Reviewed independently (branch suite 565 passed + ruff clean on my own run in
the worker's worktree; primary checkout undisturbed). Merged with this record.

- **Zero spend verified:** no `weights_val` or `weights_test` ledger entries;
  `config/settings.yaml` diff empty — defaults unflipped; frozen namespaces
  and production graph untouched.
- **Tier-0 recompute, my own arithmetic from the checkpointed components:**
  current Hit@1 0.1429 → candidate 0.2143 reproduces exactly; and the
  gate-closing claim — **zero truth-membership changes in the top-32 window
  under the retune** — reproduces exactly. (My simplified single-role
  arithmetic differs on secondary counts — MRR by ~0.005, window-changed
  cases 7 vs 10 — definitional, not substantive; the worker's multi-role
  handling is the engine's.)
- **Gate-1 STOP accepted on its own evidence.** The retune is a real
  deterministic improvement (plateau: 70/81 neighbours better, 0 worse;
  gain 2× the measured floor) that provably cannot propagate: across all
  270 mechanism-direction vectors no truth person enters the window; across
  the whole 13,776-vector simplex at most one could, via a degenerate vector
  worth a tenth of the Recall@5 floor; and no weighting anywhere removes any
  of the 28 rank-1 picks the paid re-rank made. A paid arm would have
  measured model variance with a story attached. The $10 authorization and
  the final v4 test exposure are returned unspent.
- **Deviations accepted:** tier-1/2 machinery not built (the gate closed
  first — correct frugality); the pin-replay finding is recorded (a future
  paid weights arm needs a fresh pin from the offline replay, because the
  existing pin stores profiles only for the old window); the test-split
  intent-parse checkpoint remains an open item and transfers to any future
  order that runs the test split.
- **Carried to the MVP notes:** the retune (specialization_match 0.20,
  recency 0.45) is a real improvement *to the cheap deterministic arm* —
  exactly the arm the cost finding says a product would serve breadth with.
  Not adopted as an engine default here (the research track's headline
  config is frozen and the full system provably doesn't change); recorded as
  a scoring lead for the MVP's own pilot to measure.

**With this order, the research track's experimental program is concluded.**
Final headline: the v4 baseline (`capgraph_full` test Hit@1 0.508 / Hit@5
0.754 / Hit@10 0.803 / MRR 0.622, leading every baseline on all six metrics,
McNemar p = 0.000 on Hit@1 vs BM25). The v4 test split retains one unspent
planned exposure.
