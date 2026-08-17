# Agent handoff

Updated 2026-08-11 (orchestrator succession — prior orchestrator session hit
its context limit). The research track is **complete and merged to `main`**
(merge commit `7808c55`); PR #1 closed as merged. This document is the entry
point for the successor orchestrator.

## ACTIVE WORK — read first

`docs/work-orders/benchmark-v2.md` is **accepted and merged** (2026-08-12;
acceptance record in the order). v2 outcome: the full system did not move
within the measured 0.100 noise floor (test Hit@1 0.308 / Hit@5 0.592 /
Hit@10 0.775 / MRR 0.445), but the retuned deterministic score arm gained
Hit@5 +0.117 / MRR +0.047 and now matches the full system on Hit@5/Hit@10 at
~10x lower cost. Finding of record: **the LLM re-rank bounds the full
system** — further score tuning buys nothing until the re-rank or candidate
recall (0.925, nine retrieval-bound test misses) improves.

`docs/work-orders/benchmark-v3.md` is **accepted and merged** (2026-08-12;
acceptance record in the order). v3 outcome: candidate recall 0.925 → 0.975
and window recall 1.000 (BM25 union arm + window 32 + candidate cards,
adopted), Hit@10 0.833 — the best of the three versions at the lowest cost —
but Hit@1 fell 0.308 → 0.225 (McNemar p = 0.052), now below BM25.
Self-consistency and the sol top-5 finisher were measured on validation and
rejected on evidence. **The re-rank is the only remaining bottleneck; the
tuning phase is closed.** The 120-case test split is retired — any v4 needs
a freshly cut manifest. Engine defaults carry the frozen v3 config; v2's
config remains the strongest measured Hit@1/MRR setting (both reachable via
`config/settings.yaml`) — demo/pitch usage must pick deliberately.

The manager-pitch order is **fully closed** (doc accepted 2026-08-13; demo
accepted and merged 2026-08-14 — $0.069 under stage `demo`, pinned to the
frozen v2 config). Learning doc `docs/system-deep-dive.md` delivered
2026-08-14.

**`docs/improvement-backlog.md`** (owner + external agent, 2026-08-14,
orchestrator-verified by sampling) is the reviewed gap inventory.
`docs/work-orders/benchmark-v4.md` is **accepted and merged** (2026-08-15;
acceptance record in the order). v4 outcome — **the first statistically
significant separation in this project**: on sprint-grouped work-package
briefs with multi-person truth (28/122 splits, mean truth-set 4.21),
`capgraph_full` leads every baseline on all six metrics; vs BM25 Hit@1
0.508 vs 0.303 (+0.205, McNemar p = 0.000), MRR 0.622 vs 0.459, with CIs
excluding zero on Recall@5/10 and MRR. The G12 hypothesis is confirmed: the
single-ticket instrument was hiding the difference. Spend $14.21 of $15.
The v2frozen test run was escalated and **declined for now** (within-noise
on validation; would burn a test exposure and exceed the ceiling) — needs a
separately approved order if ever wanted. The v4 test split has had ONE
exposure; the v1 split stays retired.

`docs/work-orders/improvement-wave1.md` is **accepted and merged**
(2026-08-15; acceptance record in the order; spend $0.9454 under
`probe_order`). Finding of record — **G7: presentation order dominates the
re-rank.** Reversing candidate order halves Hit@1 (0.400 → 0.200, p = 0.031;
MRR CI excludes zero); triangulation across arms: ordered 0.400 / shuffled
0.267 / reversed 0.200. Standing guidance from the acceptance: **re-rank
lever tuning is paused** until the re-rank prompt is redesigned around this
finding, and any future re-rank measurement carries a position-control arm
(~$1). Wave-1 levers are flag-guarded, default OFF (G1 truncation on, by
construction); sweep recommendations per lever are in the acceptance record.

`docs/work-orders/rerank-redesign.md` is **accepted and merged** (2026-08-15;
spend $7.9603, owner-confirmed $8 ceiling, stage `rerank_redesign`). The
pinned-retrieval study — four arms on byte-identical candidate pools —
**corrects the G7 finding of record**: with retrieval held fixed, the
position effect is inside noise for both prompts (current prompt
ordered→reversed Hit@1 −0.071, p = 0.500), and the re-rank adds **+0.250
Hit@1 / +0.182 MRR** over the deterministic arm on the identical pool, ≥
+0.179 even fed worst-first. G7's −0.200 bundled position with a fresh
retrieval draw (fresh intent parses reproduce 0/28 pools). The re-rank is
rehabilitated on this instrument; the redesigned prompt is NOT the default
(no ranking gain, ~38% dearer) but stays selectable for its citation hygiene
(0.2% vs 2.8% rejections). **Durable rule replacing the tuning pause: no
arm comparison is evidence unless retrieval is pinned.**

`docs/work-orders/deterministic-sweeps.md` is **accepted and merged**
(2026-08-15; spend $1.5555 of $8, all `noise_floor` — neither sweep gate
opened a paid arm). **v4 now has its own measured noise floor** (per-metric,
pinned pools, model-only: Hit@1 0.036 / Hit@5 0.071 / Recall@5 0.095 / MRR
0.034) — use it, not v1's 0.100, for every future v4 comparison. Both
deterministic-side levers **closed**: G3a df-gating buys tail recall and
halves deterministic Hit@1 (untested variants recorded as untested); G6
loses to its constant-scale control (whose constant is fitted on the split —
a weights lead, not a result). **No configuration change has been adopted
since the v4 baseline**: the current default config's existing v4 test
result (Hit@1 0.508 etc.) stands as the research track's headline, and the
one remaining planned v4 test exposure stays unspent.

`docs/work-orders/weights-round.md` is **accepted and merged** (2026-08-16,
**$0.00 of $10 spent** — gate 1 STOPPED it: the mechanism retune is a real
deterministic improvement, det-Hit@1 0.143 → 0.214 on a plateau, and
provably cannot propagate — zero truth people cross the re-rank window
under any mechanism vector, and no weighting removes any rank-1 pick the
re-rank made). **The research track's experimental program is concluded**
(`docs/direction-decision.md`, 2026-08-16 entry): configuration of record
unchanged since v4; final headline = the v4 baseline test result; the final
v4 test exposure and the retune scoring lead (spec 0.20 / recency 0.45,
for the cheap arm) are both preserved for the MVP.

Both closing orders are **accepted and merged** (2026-08-16):

1. `docs/work-orders/rerank-probes.md` — both pre-registered arms measured
   on pinned pools and closed below the floors ($9.0158 of $15, $5.98
   returned; pre-registration commit provably predates every ledger entry).
   **The re-rank question is closed with evidence**: five method families
   measured (wording, order, sampling, model strength, evidence richness)
   and the top-1 decision among near-identical teammates did not move.
   Parked lead: sol-over-full-window orders the tail better (R@10 +0.135,
   CI excludes zero) — top-10-breadth use only, reorder-only, not adopted.
   Standing hypothesis of record: the missing signal is in the evidence
   itself (extraction granularity; collaborator-separating signals) — a
   Part-A pipeline question for the MVP.
2. `docs/work-orders/pitch-v4-addendum.md` — addendum verified (pure
   insertion, every number reconciled); an orchestrator postscript brings
   the pitch current with the probes round. Ledger stands at **$58.8779 /
   5,521 calls**.

**Incident 2026-08-16** (`docs/incident-2026-08-16-data-loss.md`): the
probes merge carried accidentally committed `data/` symlinks that clobbered
the local benchmark data (all `data/eval` checkpoints, contributions,
buckets). No published result is affected — everything was independently
verified pre-loss — and the ledger, parquet, and production graph survived.
Remediated same-day: gitignore hardened to `data/**`, buckets regenerated
(2,668 exact). The reserved v4 test exposure is no longer runnable on the
frozen manifest; a restoration menu awaits the owner in the incident doc.
Standing rule: every merge review now audits `git ls-files data/`; workers
never symlink under a checkout's `data/`.

**`docs/work-orders/incident-restoration.md` is open** ($0: test
skip-guards, deterministic v1-manifest rebuild verified against recorded
counts, contributions reconstructed from the live graph and labeled as
reconstructions; v4 manifest rebuild explicitly out of scope).

`docs/work-orders/nearmiss-study.md` is **accepted and merged** (2026-08-17,
$1.6978 of $4). Verdict: top-1 misses are measurably closer to truth than a
random roster member (all three pre-specified CIs exclude zero) but far from
interchangeable — margins sit below the intra-team yardstick, 20 misses
collapse to 10 people (`DM:145735` first in 10 of them — an over-preference
insight for the MVP), adjacent-sprint membership null. The only sanctioned
report wording is the report-ready paragraph in `docs/nearmiss-study.md`.
Standing rule added: durable study checkpoints go to the primary checkout's
data root — worktree data is ephemeral (this study's raw runs died with its
auto-cleaned worktree; the committed per-case table preserves everything).

Still open: `docs/work-orders/incident-restoration.md` (report pending).

**Public-release prep (2026-08-17, owner-directed):** internal system names
scrubbed from three docs; remote history replaced with a single-root public
commit; all remote agent/* branches deleted. The full commit history lives
ONLY locally: branch `private-history` and the bundle
`../staff_poc_full_history_2026-08-17.bundle`. **Critical rule: every branch
created before this date (including the in-flight incident-restoration
branch) is based on the old root — its changes must be CHERRY-PICKED onto
the new main, never merged, or the entire pre-scrub history returns to the
public remote.** Before the owner flips visibility to public: verify the
remote still has only `main`, and no old-root branch has been re-pushed.
After it: **the MVP phase decision** per
`docs/direction-decision.md` — fresh branch or repo, own scope/acceptance
record, PRD as the anchor. Parked: G4 multi-vector, G9 graph proof,
df-gating variants, sol tail-ordering.

## Orchestrator playbook (how this project runs)

- Roles per `CLAUDE.md` Operating model: orchestrator issues work orders in
  `docs/work-orders/`, reviews independently, records an acceptance section
  in the order, commits, pushes (pushing is standing-authorized). The
  orchestrator never implements pipeline/product code; running approved
  commands (pilots, evals, stage re-runs) is in-scope operations.
- Verify by counts/tests/behavior, not artifact hashes — the owner explicitly
  dislikes hash ceremony. Reconcile every LLM spend claim against
  `data/llm_costs.jsonl`. Independent verification, not report-trust: check
  out the worker branch, run `uv run python -m pytest -q` and
  `uv run ruff check .` yourself.
- New LLM spend needs explicit owner approval (model + ceiling); the owner
  approves fast when given a realistic estimate. All calls go through the
  gateway with prospective budgets; unknown models/providers are refused.
- Owner preferences: plain-language explanations on request (they ask
  "explain like I'm 18"), momentum over ceremony, honest numbers over
  flattering ones. `prd (1).md` stays untracked and user-owned. The
  OpenRouter key lives in `.env` (gitignored) — never in tracked files.
- Environment: Neo4j via `colima start` + `make db-up`; embedding model
  cached locally; providers/models/pricing all in `config/settings.yaml`
  (`llm.model_providers` map — extraction `openai/gpt-5.6-luna`,
  intent/re-rank `openai/gpt-5.6-terra`, all via OpenRouter).

## Pending after benchmark-v2

1. Manager-pitch package: v1/v2 numbers plus optionally the demo notebook
   (plan Task 8, order not yet issued) — one live cited-shortlist query lands
   harder than tables.
2. Parked: 5% extraction grading (~$3, owner never approved — do not nag);
   `httpx` undeclared direct dependency (next lock refresh).
3. The strategic pivot: product-MVP phase per `docs/direction-decision.md`
   and `prd (1).md` — fresh branch/repo, own scope/acceptance record.

## State

- **All research milestones are done and accepted:** Stage 0/1 + benchmark
  foundation (accepted 2026-07-16), Stage 2 extraction via OpenRouter
  `openai/gpt-5.6-luna` (2,666 contributions + 2 skips, $1.84), Stage 3
  normalization (10,630 skills / 344 specializations, term review applied),
  Stage 4 projections (19,950 edges), Stage 5 Neo4j graph (verified live),
  Stage 6 query engine (smoke-tested, latency waiver recorded), Stage 7
  benchmark (150 cases, frozen test split).
- **Headline result** (`docs/eval-results.md`): test split Hit@1 0.325 /
  Hit@5 0.567 / Hit@10 0.767, MRR 0.449 — beats plain-RAG vector search on
  every metric and BM25 on Hit@1/Hit@10/MRR (BM25 +0.025 ahead on Hit@5).
  Ablation: the LLM re-rank supplies most of the ranking gain; candidate
  recall 0.925 bounds the system.
- **Benchmark v2** (accepted 2026-08-12): score weights retuned by mechanism
  (recency 0.40, evidence_strength 0.05); fusion, roster backstop, prompt
  revision, and window widening all measured on validation and rejected;
  stronger re-rank model escalated, not run. v2 checkpoints in
  `data/eval/v2/`; v1 artifacts verified untouched.
- **Benchmark v3** (accepted 2026-08-12): lexical union arm, candidate cards,
  window 32 adopted; permutation self-consistency and the gpt-5.6-sol top-5
  finisher measured and rejected; label-noise audit found zero contested truth
  labels at the safe resolution boundary. v3 checkpoints in `data/eval/v3/`;
  v1/v2 artifacts verified untouched; test split retired.
- **Total LLM spend:** ≈ $25.13 across all stages ($6.58 benchmark v2, $10.79
  benchmark v3), every call ledgered in `data/llm_costs.jsonl`.
- **Operating model** (`CLAUDE.md`): orchestrator (Fable 5) issues work
  orders in `docs/work-orders/` and accepts against criteria; workers
  implement in separate sessions. Every work order carries its acceptance
  record. Decisions live in `docs/direction-decision.md`.
- Generated data (`data/`) is local-only; Neo4j runs via `make db-up`
  (Colima). `prd (1).md` remains untracked and user-owned.

## Carried follow-ups (none blocking)

1. `httpx` is an undeclared direct dependency — fold into the next authorized
   `uv lock` refresh.
2. Optional research extras never exercised: demo notebook (plan Task 8),
   the 5% strong-model extraction grading (plan Task 3 step 2), and a
   validation-split retrieval-recall experiment (9/120 test misses were
   retrieval-bound).
3. Query latency ≈ 20s/brief is re-rank generation; waiver recorded in the
   Stage 6 order.

## Next phase

The owner's stated goal is the product MVP (`prd (1).md`,
`docs/direction-decision.md`): curator-mediated ingestion, canonical roster
identity, relational+vector storage, real pilot evaluation. Research-track
learnings that transfer: the extraction prompt and its evidence-guard
pattern, bucket granularity, emergent-vocabulary normalization with override
review, hybrid union retrieval, deterministic-score + LLM-re-rank ranking
with evidence-citation enforcement, prospective cost gating, and the
benchmark methodology. TAWOS data itself transfers nowhere — research-only
terms.

Before MVP work starts: record its own scope/acceptance per the direction
decision, in a fresh branch or repository.
