# Work order: Stage 2 pilot gate

- Issued: 2026-08-10 by the orchestrator
- Status: open
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
