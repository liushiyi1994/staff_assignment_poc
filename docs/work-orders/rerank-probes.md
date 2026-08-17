# Work order: re-rank probes — SOTA methods against the last-mile ranking

- Issued: 2026-08-16 by the orchestrator, on the owner's instruction to
  explore re-rank improvements (this re-opens experimental work after the
  2026-08-16 conclusion entry; the conclusion otherwise stands — config of
  record unchanged, final v4 test exposure stays reserved for a freeze order)
- Status: accepted 2026-08-16 by the orchestrator (see Acceptance record)
- Phase: research track, re-rank exploration (base is current `main`)
- Suggested working branch: `agent/rerank-probes`
- LLM authorization: **granted 2026-08-16 by the owner — ceiling $15 total**
  under stage name `rerank_probes`, v4 validation split only (28 cases,
  rewritten briefs, pinned pools). At most **four paid arms**, each
  pre-registered (see rules). `openai/gpt-5.6-sol` is authorized for probe
  arms (already in the provider/pricing maps); a scoped raise of the
  per-call ceiling for sol window calls is authorized up to $0.15/call,
  applied to this study's calls only. **No v4 test-split access of any
  kind.** Escalate above $15; record any in-session raise here at the time.

## Objective

Find a re-rank method that beats the current one on the failure bucket the
diagnostics isolated — in 54 of 60 top-1 misses a correct person was among
the 32 candidates shown and ranked too low — using literature methods probed
cheaply, cleanly, and pre-registered. Nothing is adopted here: a winner
graduates to a later freeze order (which owns the reserved test exposure);
a set of clean failures closes the question with evidence.

## What is measured and must be respected

- Baseline: the rerank-redesign study's baseline arm (current prompt,
  ordered, pinned pools) — Hit@1 0.393 / Hit@5 0.607 / MRR 0.501 on the pin.
- Floors (per-metric, this instrument): Hit@1 0.036 / Hit@5 0.071 /
  Recall@5 0.095 / MRR 0.034. A probe is "signal" only beyond them with
  paired support.
- Already measured dead ends — do not respend: prompt rewording
  (evidence-first, no gain), permutation self-consistency / shuffle+vote
  (hurt, arXiv:2310.07712 implemented and rejected), strong-model top-5
  permutation (hurt, v1 instrument), position controls (order effects inside
  noise on pinned pools).
- The pin (`data/eval/rerank_redesign/pin/validation.jsonl`) reuses cleanly
  for any arm that leaves pools and window membership unchanged — which is
  every arm in scope here. If an arm needs richer per-candidate data than
  the pin stores, extend the pin from production data read-only (graph reads
  allowed; no writes) as a tested feature.

## Method menu (vetted from the 2026-08-12 literature survey; status noted)

| Method | Source | Fit note |
|---|---|---|
| Stronger model, full 32-card window, listwise | RankGPT model-strength gap, arXiv:2304.09542 | **Never tried anywhere in this project at full window.** Cards cut input ~40%, ≈ $0.10/case with sol. Highest-prior arm. |
| Richer evidence for finalists (hybrid card: full contribution detail for top ~8, cards for the rest) | SumRank direction, arXiv:2603.24204 (thin evidence, cheap A/B) | Aims at close-teammate discrimination, the observed failure mode. |
| Setwise selection / tournament over the window | Setwise SIGIR 2024, arXiv:2310.09497; TourRank WWW 2025, arXiv:2406.11678 | Best per-decision accuracy; more calls/case; validator note below. |
| Pairwise refinement of the top ~5 | PRP, arXiv:2306.17563 | Only as a cheap add-on to a winning arm; alone it resembles the failed finisher. |
| Batched pointwise + self-consistency | arXiv:2505.12570 | Order-free scoring; moderate cost; contrast with the *permutation* SC that failed. |
| Long-CoT / reasoning rerankers | Rank-K arXiv:2505.14432 vs counter-evidence arXiv:2505.16886 | Discouraged — conflicting literature, cost; needs an explicit pre-registration argument to run. |

Off-menu methods are allowed with a citation and a pre-registration that
names the mechanism aimed at the 54-miss bucket.

## Rules (the discipline is unchanged)

1. **Pre-register every paid arm** in `docs/rerank-probes-report.md` before
   its first call: method + citation, the mechanism (why it should move
   shown-but-ranked-low cases), projected cost, and what result would count
   as failure. No post-hoc arm swaps.
2. **Sequencing gate:** run at most two arms, then continue only if at least
   one shows signal beyond the floor on Hit@1 or MRR with paired support —
   otherwise stop and report; the remaining budget returns.
3. Every arm: pinned pools proven identical to the baseline pin; paired
   per-case stats vs the baseline arm; rejection/validator accounting. A
   method that only *reorders* validated entries must say so; a method that
   generates new claims must pass the evidence validator unchanged — no
   uncited claim reaches an output in any arm.
4. Defaults untouched; no freeze; no test split. Checkpoints under
   `data/eval/rerank_probes/`; frozen namespaces and production graph
   read-only.
5. Suite green, ruff clean; study machinery mirrors the established
   measurement/prose split; "measured" vs "reasoned" distinct.

## Deliverables

1. `docs/rerank-probes-report.md`: pre-registrations, arm table with paired
   stats against baseline and floors, validator accounting, cost per arm,
   and a recommendation — graduate-to-freeze (with which arm), or close the
   re-rank question with the accumulated negative evidence.
2. Report back: findings, spend reconciled (single stage ≤ $15), test/ruff
   output, deviations. Escalate rather than improvise.

## Acceptance criteria

1. Ledger ≤ $15 under `rerank_probes`; zero test-split contact; ≤ 4 paid
   arms, each pre-registered before spend, sequencing gate honored.
2. Pin identity proven per arm; paired statistics against the correct
   baseline; claims read against the measured per-metric floors.
3. Validator integrity per arm demonstrated.
4. Defaults, frozen namespaces, production graph untouched.

## Spend record (worker, 2026-08-16)

Recorded here at the time, per the authorization rule above.

- **No in-session ceiling raise was requested or used.** The study ran inside
  the written $15 total and the written $0.15/call scoped raise for sol's
  window calls. The dearest single call was $0.1404.
- **Spent: $9.0158 of $15**, 119 calls, all under stage `rerank_probes`, all
  purpose `rerank`. No other stage spent anything from this study's first
  call onward. **$5.9842 returns unspent** — the sequencing gate closed after
  two arms, so arms 3 and 4 were not run.
- Two paid arms of the four authorized: **S1** (sol, full 32-card window,
  RankGPT permutation answer) $6.9795, and **S2** (hybrid rich-evidence card,
  top-8 head) $2.0363. Both pre-registered in
  `docs/rerank-probes-report.md` before their first call, on commit `8d8fede`,
  which predates every call in the ledger.
- **One declared deviation, pre-registered before spending.** The order's
  recommended sol arm cannot answer in the baseline's claim-generating shape
  inside $0.15/call: that shape averages 3,272 output tokens, so the
  worst-case pre-call estimate is $0.21 and the gateway refuses it. S1
  therefore uses RankGPT's own permutation answer format (an ordering of ids,
  no prose), which is faithful to the citation and fits at a verified $0.1332
  worst case. It is labelled reorder-only throughout, and the cost of the
  reasons pass it would need to be deployable is priced in the
  recommendation. Full argument in the report's S1 pre-registration.
- Result: **close-with-evidence**, not graduate-to-freeze. Neither arm moved
  Hit@1 or MRR beyond the measured v4 floor with paired support. The reserved
  final v4 test exposure is untouched, and no default changed.

## Acceptance record (2026-08-16, orchestrator)

Reviewed independently (suite 601 passed + ruff clean on my own run in the
worker's worktree; primary checkout undisturbed). Merged with this record.

- **Pre-registration held, provably:** commit `8d8fede` (14:53:47) predates
  the first of 119 ledger entries (14:54:18); the runner refuses
  unregistered arms by test. Ledger $9.0158 all under `rerank_probes`,
  dearest call $0.1404 against the scoped $0.15, $5.98 returned; the
  two-arm sequencing gate honored; zero test-split contact.
- **My recompute matches both arms exactly** on pools verified 28/28
  identical to the baseline pin: S1 0.321/0.607/0.857, R@10 0.669, MRR
  0.450; S2 0.429/0.571/0.679, R@10 0.491, MRR 0.516; baseline
  0.393/0.607/0.750, R@10 0.534, MRR 0.501.
- **Gate closure correct:** neither arm moved Hit@1 or MRR beyond the
  measured floors with paired support. S2's +0.036 is one case at the floor
  and negative on four other metrics.
- **Parked lead recorded:** S1 — the stronger model over the full window —
  orders the *tail* genuinely better (Recall@10 +0.135, CI excludes zero;
  Hit@10 +0.107) while ranking the head worse. Relevant only to a product
  that wants top-10 breadth; it is reorder-only (no cited reasons) and needs
  a reasons pass plus its cost case. Not adopted.
- **Deviation accepted (declared before spend):** S1 uses RankGPT's own
  permutation output format because the claim-generating shape cannot fit
  the per-call ceiling with sol; labelled reorder-only, validator accounting
  clean (S2's rejection rise is duplicates, not fabricated citations).
- **The re-rank question closes with evidence.** Five method families are
  now measured on this instrument — prompt wording, presentation order,
  sampling/voting, model strength, evidence richness — and the top-1
  decision among near-identical teammates has not moved. The standing
  hypothesis, accepted as the project's read: the missing signal is in the
  evidence itself (extraction granularity; per-person signals that separate
  close collaborators) — a Part-A pipeline question for the MVP, not
  another ranking method. The reserved final v4 test exposure remains
  unspent.
