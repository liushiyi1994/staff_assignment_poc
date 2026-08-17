# Work order: re-rank prompt redesign — make the model rank on evidence, not position

- Issued: 2026-08-15 by the orchestrator
- Status: accepted 2026-08-15 by the orchestrator (see Acceptance record)
- Phase: research track, re-rank rework (base is `main` @ `7dce0ca`; lifts the
  re-rank-tuning pause recorded in the wave-1 acceptance, for this order only,
  because it carries the required position controls)
- Suggested working branch: `agent/rerank-redesign`
- LLM authorization: **granted 2026-08-15 by the owner — ceiling $6** under
  stage name `rerank_redesign`, on the **v4 validation split only** (28 cases,
  rewritten briefs). Expected ~$5 (three re-rank arms ≈ $1.6 each plus
  retries). **No v4 test-split access of any kind** — its exposure budget is
  spent by a future config-freeze order, not here. Escalate before proceeding
  if any projection exceeds $6.

## Objective

Redesign the re-rank prompt so the model's ranking is driven by candidate
evidence rather than by presentation order, and prove it with position
controls on the v4 validation split — without touching engine defaults or the
test split.

## Context (what is established)

Wave-1's G7 probe: under the current prompt (`rerank_cards`), reversing the
candidate order halves Hit@1 (0.400 → 0.200, McNemar p = 0.031) and moves MRR
−0.132 (CI [−0.234, −0.040]); with v3's arms the dose-response is ordered
0.400 / shuffled 0.267 / reversed 0.200. The card prints the deterministic
score and the model does not use it. Permutation self-consistency was already
measured and rejected (v3): averaging over shuffles destroys the informative
order without adding evidence-use. The conclusion of record: the prompt must
change what the model *attends to*, not how many times we sample it.

## Method requirements (the discipline is the experiment)

1. **Pin retrieval across all arms.** Every arm reuses the *same* checkpointed
   intent parses and candidate pools as the existing v4 validation run
   (`data/eval/v4/runs/v3frozen/rewritten/validation.jsonl`), so the only
   thing that varies between arms is the re-rank stage. Implement pinning if
   the v4 runner lacks it. Prove it: candidate sets byte-identical across
   arms, asserted in the report and by a test. This also means the existing
   v4 validation run IS the "current prompt, ordered" baseline — $0.
2. **Arms** (each: `capgraph_full`, 28 cases, v3frozen engine otherwise):
   - **A — current prompt, reversed order** (~$1.6): the position control on
     this instrument; establishes the gap the redesign must close.
   - **B — redesigned prompt, ordered** (~$1.6).
   - **C — redesigned prompt, reversed** (~$1.6).
   One redesign iteration (new B'/C') is allowed if ≥$1.50 of ceiling
   remains after A–C; otherwise escalate with findings.
3. **Redesign constraints.**
   - Every citation and validation rule carries over verbatim — the evidence
     validator in `query/rank.py` must keep working unchanged, and rejected
     entries are still discarded, never repaired. Report the per-arm
     rejection accounting (offered / rejected / rate).
   - Same model, same window (32), same card *data*; card content/layout may
     change (presentation is in scope) but evidence keys stay citable.
   - The anti-position mechanism must be explicit and documented in the
     prompt file and the report — e.g. per-candidate evidence assessment
     emitted *before* any comparative ranking, instructions that candidate
     order carries no information (true in the reversed arms), required
     comparative justifications ("why #1 over #2") grounded in cited
     evidence, use of the printed deterministic score as a stated input.
     Which mechanisms to combine is the worker's design call; what is not
     acceptable is a wording-only tweak with no mechanism.
4. **Adoption criteria, decided on mechanism at 28-case scale** (noise ±~0.10;
   paired per-case stats, McNemar / bootstrap, as in v4):
   - Primary: the ordered-minus-reversed gap under the redesign (B−C) is
     materially smaller than under the current prompt (baseline−A).
   - Guard: B is not worse than the baseline beyond noise.
   - Also report B and C against `capgraph_score` on the same pools — how
     much the re-rank adds when it cannot lean on order is the number that
     finally sizes the re-rank's real contribution.
5. **If the redesign fails** both criteria, that is a reportable finding:
   recommend (do not run) the algorithmic alternatives — setwise selection
   over the top 5, or pointwise per-candidate scoring with deterministic
   tie-break — and stop. No further spend chasing wording.
6. **No default flips.** The redesigned prompt lands as a new file
   (`prompts/` + config-selectable via `llm.rerank_prompt`), defaults
   unchanged. Flipping the default and any test-split run belong to a future
   freeze order that bundles the winning wave-1 sweeps.

## Deliverables

1. The redesigned prompt file(s), config-selectable, defaults untouched;
   pinning support in the v4 runner as a tested feature; suite green, ruff
   clean.
2. `docs/rerank-redesign-report.md`: the arm table (baseline, A, B, C, any
   B'/C') with paired statistics, rejection accounting, the mechanism
   documentation, the B/C-vs-`capgraph_score` sizing, and a recommendation
   (adopt-at-next-freeze / iterate / switch-to-algorithmic).
3. Report back: findings, spend reconciled against `data/llm_costs.jsonl`
   (single stage `rerank_redesign`, ≤ $6), test/ruff output, deviations.
   Escalate rather than improvise.

## Acceptance criteria

1. Ledger: all spend under `rerank_redesign`, ≤ $6, zero entries under any
   `bench4_test`-adjacent name; no reads of the v4 test split anywhere in the
   study (checkpoints and code paths verifiable).
2. Candidate pools byte-identical across all arms and the baseline (tested).
3. Validator untouched in behavior; per-arm rejection accounting present.
4. Conclusions stated against the noise floor with paired statistics;
   "measured" and "reasoned" kept distinct.

## Acceptance record (2026-08-15, orchestrator)

Reviewed independently on `agent/rerank-redesign`; merged with this record.
My own runs: 511 passed, ruff clean. My own recompute from the raw run
records matches every published arm (baseline 0.393 Hit@1 / 0.501 MRR;
A 0.321/0.450; B 0.321/0.455; C 0.357/0.454) and I verified the pinning
directly: candidate pools byte-identical across all four arms, 28/28 in
every pairing. Ledger: 259 calls, $7.9603, all under `rerank_redesign`,
first call after the final `bench4_test` entry; no test-split run, read, or
diagnostic anywhere in the study.

- **Spend authorization.** The order's written ceiling was $6; the owner
  raised it in-session to $8.00 when the assumed-free baseline proved void
  (v4 never checkpointed intent parses; re-parsing reproduces 0/28 pools, so
  the ordered current-prompt arm had to be paid). The owner confirmed the $8
  authorization to the orchestrator at review. Process rule going forward:
  in-session raises are valid, and the worker must record them in the order
  file at the time, not only in the report.
- **Findings accepted:**
  1. On pinned pools, the position effect is inside noise for both prompts
     (current prompt ordered→reversed Hit@1 −0.071, two discordant cases,
     p = 0.500; redesign +0.036, p = 1.000).
  2. The re-rank adds **+0.250 Hit@1 / +0.182 MRR** over the deterministic
     arm on the identical pool, and ≥ +0.179 Hit@1 even fed worst-first —
     on this instrument it is not re-expressing presentation order.
  3. The redesigned prompt buys no ranking gain at ~38% higher cost, and is
     **not** adopted as default; it is kept config-selectable for its
     citation hygiene — rejection rate 0.2% vs the current prompt's 2.8% on
     identical inputs — which is worth remembering for the MVP.
- **Correction to a finding of record (G7).** Wave-1's G7 probe compared two
  separate engine runs, so its −0.200 Hit@1 bundled presentation order with
  a fresh retrieval draw; the clean, pinned measurement does not reproduce
  it. Whether position dominance was real on the retired v1-manifest
  instrument is now unresolvable and moot. The wave-1 acceptance text stays
  as written; this correction supersedes it, per the project's standing
  correction pattern. The same cross-run confound qualifies the *attribution*
  (not the headline numbers) of earlier arm-to-arm comparisons on v1–v3;
  none of those attributions is load-bearing for current decisions.
- **Standing guidance replaced.** The re-rank tuning pause is lifted as
  moot. The durable rule that replaces it: **no arm comparison is evidence
  unless retrieval is pinned** — cross-run deltas bundle a fresh retrieval
  draw that has now produced at least one false finding of record.
- **Deviations accepted:** study-specific runner (`eval/rerank_redesign.py`,
  precedented); per-arm output allowance difference (disclosed as the one
  non-prompt, non-order difference between arms); $0.197 superseded first
  draft, checkpoint archived not deleted; no B′/C′ iteration ($0.04
  remaining, and no measured gap left to close).
- Noted for a future order: v4 has never had its own noise floor measured;
  a pinned-pool repeat of one arm (~$2) would measure model-only variance
  and sharpen every future comparison on this instrument.

Outcome: the re-rank is rehabilitated on the current instrument — it earns
its Hit@1 premium and is order-robust; the deterministic arm (0.143 Hit@1 on
the same pool) is what limits the pool-resident ceiling now. Remaining
headroom is in retrieval/scoring quality, not prompt wording.
