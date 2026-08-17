# Work order: deterministic-side sweeps (G3a, G6) and the v4 noise floor

- Issued: 2026-08-15 by the orchestrator, on the owner's go-ahead
- Status: accepted 2026-08-15 by the orchestrator (see Acceptance record)
- Phase: research track (base is `main` @ `0dbd570`)
- Suggested working branch: `agent/deterministic-sweeps`
- LLM authorization: **granted 2026-08-15 by the owner — ceiling $8 total**
  across stage names `noise_floor` (one pinned-pool repeat of the baseline
  arm, ~$2) and `sweep_val` (at most two conditional full-system arms, ~$2
  each — see gates below). v4 validation split only (28 cases, rewritten
  briefs). **No v4 test-split access of any kind.** Escalate before
  proceeding if any projection exceeds $8. In-session ceiling raises, if the
  owner grants any, must be recorded in this file at the time.

## Objective

Measure the two adopted-but-dark wave-1 levers that attack where the measured
headroom is — the deterministic ordering (pinned-pool Hit@1 0.143 vs the full
system's 0.393) — and give benchmark v4 its own noise floor so every future
comparison on this instrument is read against a measured gauge instead of
v1's borrowed 0.100.

## The pinning rule, generalized (standing, from the rerank-redesign acceptance)

No arm comparison is evidence unless everything except the lever is held
fixed. Concretely for this order: **all arms reuse the checkpointed intent
parses** from `data/eval/rerank_redesign/pin/validation.jsonl` (intent is
brief-level and vocabulary-independent, so it pins cleanly across both
levers). Retrieval and scoring then vary only through the lever under test.
Prove parse-reuse byte-identically per arm; where a lever legitimately
changes the pools (G3a), report the pool diff explicitly — that diff IS the
lever's retrieval effect, not noise.

## Work items

1. **v4 noise floor (`noise_floor`, ~$2, unconditional).** Repeat the
   rerank-redesign baseline arm exactly — current prompt, ordered, pinned
   parses, identical pools — and report per-case agreement plus the paired
   metric deltas between the two runs. This is model-only variance on this
   instrument. It is also the gauge every other comparison in this order is
   read against.
2. **G3a — vocabulary frequency gating (df floor 3).** Two tiers:
   - *Tier 1, $0 (offline):* rebuild the Stage 3 vocabulary with
     `vocabulary.min_document_frequency: 3` into a **study namespace**
     (production `data/contributions/` untouched), rebuild projections, and
     recompute — with pinned parses — candidate pools, candidate recall,
     window recall, and the deterministic arm on v4 validation. Report the
     pool diff vs baseline (candidates gained/lost per case).
   - *Gate:* the paid tier runs only if tier 1 shows no recall regression
     and a deterministic-arm improvement or a materially changed window
     population. If tier 1 shows nothing, stop and report — that is the
     backlog's own success test ("a smaller vocabulary that does not improve
     retrieval is cosmetic").
   - *Tier 2, ~$2 (paid, conditional):* one full-system arm on the gated
     vocabulary, paired against the rerank-redesign baseline.
3. **G6 — specialization strength.** Same two tiers, scoring-only:
   - *Tier 1, $0:* offline re-score with `specialization_strength.enabled:
     true` on pinned pools, judged **against the constant-scale control**
     from `docs/improvement-wave1-report.md` (the control reproduces most of
     the naive gain; G6 is adopted only for what it adds beyond it). Report
     whether the window population changes at all.
   - *Tier 2, ~$2 (paid, conditional):* only if tier 1 beats the control
     beyond the (freshly measured) noise floor AND changes who reaches the
     window — otherwise the offline result is the full result.
4. **Graph hygiene (hard requirement).** If G3a tier 1 needs the live Neo4j
   graph rebuilt with the gated vocabulary, restore the production graph
   afterward (flags-off Stage 3–5 re-run) and verify at both ends by counts
   (Person 316, Contribution 2,666, Skill 10,630, HAS_SKILL 17,589,
   HAS_SPECIALIZATION 2,361). If a cleaner isolation exists (separate
   database, offline structured retrieval against study parquet), propose it
   — escalate rather than improvise anything that would leave the shared
   graph in a study state.

## Constraints

- Defaults stay untouched — flag flips happen only in study namespaces; the
  freeze order decides defaults.
- Paired per-case statistics throughout, read against the item-1 floor;
  "measured" vs "reasoned" kept distinct.
- All checkpoints under `data/eval/sweeps/`; frozen namespaces untouched.
- Suite green, ruff clean; any new pinning/namespace plumbing is a tested
  feature, not a script hack.

## Deliverables

1. `docs/deterministic-sweeps-report.md`: the noise-floor measurement, per-
   lever tier-1 tables and pool diffs, any tier-2 paired results, rejection
   accounting for paid arms, and a recommendation per lever
   (include-in-freeze / keep-dark / close).
2. Report back: findings, spend reconciled by stage (`noise_floor` +
   `sweep_val` ≤ $8), test/ruff output, graph-restoration verification,
   deviations. Escalate rather than improvise.

## In-session decisions (recorded at the time)

- **2026-08-15, G3a isolation — cleaner isolation approved.** The order's item 4
  allows proposing a cleaner isolation instead of rebuilding the shared graph.
  Proposed and approved: a **throwaway second Neo4j container**
  (`neo4j:5-community`, bolt 7688 / http 7475, its own docker volume) holds the
  df-floor-3 study graph; the study's driver is pointed at it by URI and the
  production graph at `bolt://localhost:7687` is **never written to**. Production
  counts are therefore verified by observation at both ends rather than restored
  after a study state, and the study container plus its volume are removed at
  study end. Same Cypher and same `query/retrieve.py` code path in both arms — no
  re-implemented retrieval that could drift.
- **Ceiling.** No in-session raise was requested or granted; the authorization
  stands at **$8 total** across `noise_floor` and `sweep_val`.

## Acceptance criteria

1. Ledger ≤ $8 across the two stages; zero test-split contact.
2. Pinned parses proven identical across arms; pool diffs reported where the
   lever moves them.
3. Production graph and frozen namespaces verified untouched (or restored,
   with counts) at study end.
4. Every adoption recommendation cites the measured v4 floor, not v1's.

## Acceptance record (2026-08-15, orchestrator)

Reviewed independently on `agent/deterministic-sweeps`; merged with this
record. My own runs: 544 passed, ruff clean. My own recomputes from raw
checkpoints:

- **Noise floor matches exactly** (Hit@1 0.0357 / Hit@5 0.0714 / Recall@5
  0.0946 / MRR 0.0341) on pools I verified 28/28 identical to the
  rerank-redesign baseline. This floor — per-metric, measured on this
  instrument — supersedes v1's borrowed 0.100 as the gauge for all future v4
  comparisons. Recalibration accepted: the redesign study's position effect
  (−0.071) sits between one and two floors — still not significant (two
  discordant cases, p = 0.500), but "inside noise" is now stated with the
  humility the tighter gauge demands; the re-rank's +0.250 Hit@1 premium
  survives at more than twice the floor.
- **G3a tier-1 confirmed** (det-Hit@1 0.143 → 0.071, candidate recall up,
  window population down, pools moved on 27/28): the gate's STOP was earned —
  tail recall the re-rank never sees is not a usable gain. **Closed**, with
  the worker's tension note kept on record: a reviewer pairing df-gating with
  a wider window could reasonably reopen it, and df=2 / specialization-only
  variants are recorded as untested, not rejected.
- **G6 tier-1 confirmed** (pools bit-identical 0/28 as scoring-only requires;
  0.179/0.318 loses to the constant-scale control 0.214/0.356). **Closed.**
  Caveat carried from the worker's own last commit: the control's constant is
  fitted on this 28-case split — it is a lead for a weights question, not an
  adopted result.
- **Ledger:** 54 calls, $1.5555, all `noise_floor`; `sweep_val` zero entries —
  neither gate opened a paid arm; $6.44 of the ceiling returned unspent.
  Zero test-split contact.
- **Graph hygiene:** the study graph lived in a throwaway second container
  (approved in-session and — per the new rule — recorded in this order file
  at the time); production verified by counts at three points and never
  written. Frozen namespaces untouched by mtimes.
- **Deviations accepted:** stage-3/4/5 path parameters (production loader
  reused, defaults unchanged, tested); `pin_role` rename; the recommendations
  module's WRITTEN_FOR guard (prose that invalidates itself if a re-run flips
  a gate is exactly the right instinct).

Outcome: both deterministic-side levers close, at a tenth of the authorized
spend, and the instrument now has its own floor. **No configuration change
has been adopted since the v4 baseline** — the current default remains the
best-known configuration, and its existing v4 test result stands as the
research track's headline unless the owner commissions a weights round on
the control's lead.
