# Work order: Stage 2 pilot gate

- Issued: 2026-08-10 by the orchestrator
- Status: **accepted 2026-08-10** (commit `d62927d`, branch `agent/stage2-pilot-gate`; acceptance record at the bottom of this file)
- Phase: research track (`docs/direction-decision.md`)
- Base branch: `agent/benchmark-foundation`
- Suggested working branch: `agent/stage2-pilot-gate`
- Hard constraint: **no LLM or network API call, no credentials, no `.env`.**
  This work order completes before the first API call is authorized.

## Objective

Make `src/capgraph/pipeline/stage2_extract.py` and `src/capgraph/llm.py` safe to
run a deterministic ~30-bucket extraction pilot. Today the command processes all
2,668 buckets, swallows failures and continues, validates nothing against the
`Contribution` contract, and the budget check is retrospective only — unknown
model names are priced at $0.

## Tasks

1. **Pilot selector.** Deterministic, project-stratified selection of 6 buckets
   per configured project (30 total) from `data/buckets/buckets.jsonl`, driven
   by a versioned seed in `config/settings.yaml`. Emit a pilot manifest
   (`data/contributions/pilot_manifest.v1.jsonl`, git-ignored) recording bucket
   IDs, selection parameters, and a settings digest. Rebuilds with the same
   inputs must be byte-identical.
2. **CLI controls on stage 2.** `--pilot <manifest>` (process only manifest
   buckets), `--limit N`, and `--dry-run` (render prompts and validate inputs
   with zero API calls). Pilot output goes to a separate file (e.g.
   `data/contributions/pilot_raw.jsonl`); checkpointing is preserved per mode;
   pilot and full-run outputs never mix.
3. **Output validation.** Validate every response against
   `models.Contribution`; require every `evidence_ticket_keys` entry to belong
   to the source bucket's tickets; count success / skip / invalid / failed;
   print a summary; exit nonzero when the configured acceptance threshold is
   missed.
4. **Prospective cost control in `llm.py`.** Refuse unknown model names instead
   of pricing them at $0; estimate per-call cost before the call; enforce a hard
   pre-call ceiling; add an explicit pilot budget (settings key, e.g.
   `llm.pilot_budget_usd`) enforced prospectively, not only after spend is
   logged.
5. **Fixture tests (no network).** Selection determinism, dry-run makes zero
   client instantiations or calls, checkpoint skip/`--force` behavior,
   evidence-key validation, threshold-miss exit status, unknown-model refusal,
   and budget refusal.

## Acceptance criteria

- `uv run python -m pytest -q` green (existing 57 tests plus new ones);
  `uv run ruff check .` clean.
- Pilot manifest rebuilds byte-identical across two runs.
- `--dry-run` provably makes no API client instantiation or call
  (test-enforced).
- All thresholds, seeds, and budgets live in `config/settings.yaml`; no magic
  numbers in code.
- No change to accepted artifacts: existing Stage 0 / Stage 1 / benchmark files
  still match the SHA-256 values in `docs/real-data-validation.md`.
- `prd (1).md` and all unrelated user files untouched; nothing committed under
  `data/`.

## Out of scope

- Any API call, even one. Provider/model/budget approval, running the pilot,
  and the qualitative review are orchestrator-gated follow-ups
  (`docs/agent-handoff.md`, research-track steps 6–7).
- Prompt content changes beyond what dry-run rendering requires; prompt
  iteration happens during the pilot review cycle.
- Stages 3–5 and query-engine work.

## Report back

Reply with: working branch and commits, a summary of design choices, full test
and ruff output, any deviation from this order with justification, and anything
discovered that should change the order — escalate to the orchestrator rather
than improvising scope.

## Acceptance record (orchestrator, 2026-08-10)

Implementation: commit `d62927d` on `agent/stage2-pilot-gate`. All acceptance
criteria independently re-verified by the orchestrator, not taken from the
worker's report:

- `uv run python -m pytest -q`: 98 passed (57 pre-existing + 41 new).
  `uv run ruff check .`: clean.
- Pilot manifest rebuilt by the orchestrator: byte-identical to the worker's
  copy, SHA-256 `fa86549c97eaac9503643fab67a4962ca46b634f8f23e2d61e5c739b95c21e34`,
  6/6 buckets for each of DM, EVG, FAB, MESOS, TIMOB.
- Dry run over the pilot manifest: 30 rendered, 0 invalid, projected $0.2790
  against the $1.00 pilot budget, exit 0, no output file and no cost log
  created. Zero-client behavior is test-enforced (`no_client` fixture forbids
  `llm._client`).
- All six accepted Stage 0 / Stage 1 / benchmark artifacts still match the
  SHA-256 values in `docs/real-data-validation.md`.
- No `.env`, no `data/llm_costs.jsonl`, nothing committed under `data/`,
  `prd (1).md` untouched.
- Model/pricing check: `claude-haiku-4-5-20251001` at $1/$5 per MTok is
  correct per current reference; `claude-sonnet-5` at $3/$15 matches list price
  (intro pricing through 2026-08-31 makes this a safe overestimate).

Worker deviations reviewed and accepted: pricing and `max_output_tokens` moved
into `config/settings.yaml`; `--force` + `--limit` refused; offline-only Make
targets. Known bounded gap, accepted: the retry loop may spend up to 4× one
call's estimate (≤ $0.20) before logged spend gates the next call — immaterial
at pilot scale, revisit before a full run.

Open item for full-run authorization (out of scope here): a full-corpus dry run
projects $25.79 worst-case against the $25.00 stage ceiling and correctly
refuses to start. Before authorizing a full run, either raise
`llm.max_stage_cost_usd`, lower `llm.max_output_tokens`, or re-project from
observed pilot output tokens (realistic spend ≈ $8–11).

Next gates (orchestrator/user): approve provider, model ID, credentials, and
pilot ceiling; run the pilot; qualitative review of all 30 contributions per
`docs/agent-handoff.md` research-track steps 6–7.
