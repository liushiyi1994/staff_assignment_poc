# Work order: OpenRouter provider for the LLM gateway

- Issued: 2026-08-10 by the orchestrator
- Status: **accepted 2026-08-10** (commit `2e59958`, branch `agent/openrouter-provider`; record below)

## Acceptance record (orchestrator, 2026-08-10)

All criteria independently re-verified: 117 tests pass, ruff clean; pilot
manifest rebuilds byte-identical
(`3938174dcb25242be3f6224bb208ab2dbd3a4d219b8305369f209f764d32f699`) and selects
the same 30 bucket IDs in the same order as the previously accepted slice; the
offline dry run reports 30 rendered / 0 invalid, projected $0.0324 against the
$1.00 pilot budget, exit 0, with no output file or cost log created; all six
accepted Stage 0 / Stage 1 / benchmark artifacts still match
`docs/real-data-validation.md`; the key value appears nowhere in the working
tree outside `.env` and nowhere in the full git history.

Both worker deviations accepted: the missing-usage conservative-billing
fallback (with the additive `usage_source: "estimate"` marker) and the
suite-wide `tests/conftest.py` outbound-network guard. The base-branch
discrepancy the worker flagged was an orchestrator error in this order's
header (the order named `a735eb6`, written before the order itself was
committed as `475a915`); branching from `475a915` was correct.

Carried follow-ups: declare `httpx` as a direct dependency at the next
authorized lock refresh; resolve global-vs-per-call provider selection before
any query-engine work (with `llm.provider: openrouter`, the intent/rerank
Claude model IDs would mis-route if the query engine were run today); the old
$25.79 full-run projection is obsolete under $0.10/$0.60 pricing — re-project
from observed pilot tokens at full-run authorization time.
- Phase: research track (`docs/direction-decision.md`)
- Base branch: `agent/stage2-pilot-gate` (HEAD `a735eb6`)
- Suggested working branch: `agent/openrouter-provider`
- Hard constraint: **no LLM or network API call from repository code, no key
  material in code, logs, tests, or commits.** The only network use permitted
  while implementing is your own documentation lookup. `.env` (gitignored)
  already contains `OPENROUTER_API_KEY`; never print or commit its value.

## Decision context

On 2026-08-10 the project owner designated the Stage 2 extraction provider:
**OpenRouter**, model **`openai/gpt-5.6-luna`**, replacing the planned direct
Anthropic Haiku call. Pricing verified against the OpenRouter models API on
2026-08-10: **$0.10 input / $0.60 output per MTok** (context 1,050,000). The
pilot budget remains `llm.pilot_budget_usd: 1.00`; projected pilot worst-case
under the new pricing is roughly $0.03.

## Objective

Make `src/capgraph/llm.py` provider-configurable so the existing pilot-gated
Stage 2 command can run through OpenRouter's OpenAI-compatible API, without
weakening any cost control, checkpoint, or zero-call guarantee built in the
pilot gate (`docs/work-orders/stage2-pilot-gate.md`).

## Tasks

1. **Provider config.** Add `llm.provider` to `config/settings.yaml` with
   supported values `anthropic` and `openrouter` (set it to `openrouter`), and
   set `llm.extraction_model: "openai/gpt-5.6-luna"`. An unsupported provider
   value must be refused with a clear error, same spirit as unknown-model
   refusal. Keep the `anthropic` path working (tests cover both).
2. **OpenRouter client.** For `openrouter`, call the OpenAI-compatible chat
   completions endpoint (`https://openrouter.ai/api/v1`) authenticated with
   `OPENROUTER_API_KEY` loaded via `settings` (add the attribute alongside
   `anthropic_api_key`; missing key raises the same style of runtime error, and
   importing the module must never require credentials). Preserve the lazy
   single client-factory seam (`llm._client` or equivalent single patch point)
   so the existing `no_client`/`client_spy` test fixtures keep enforcing
   zero-client behavior with at most a mechanical update.
3. **Keep the gateway contract identical.** `call_json(prompt, model, stage,
   max_tokens)` keeps its signature, tolerant JSON extraction, tenacity retry
   (cost-control errors still raised outside the retry loop), and
   `temperature`/`max_output_tokens` from settings. Map OpenAI-style usage
   fields (`prompt_tokens`/`completion_tokens`) into the existing cost-log
   schema (`input_tokens`/`output_tokens`) so `data/llm_costs.jsonl` and
   `stage_cost_so_far` are unchanged.
4. **Pricing.** Add `openai/gpt-5.6-luna: {input: 0.10, output: 0.60}` to
   `llm.pricing_usd_per_mtok` with a comment citing the OpenRouter models API
   and the 2026-08-10 verification date. Unknown models remain refused; all
   prospective estimation and ceilings work unchanged with the new prices.
5. **`.env.example`.** Add an empty `OPENROUTER_API_KEY=` placeholder line
   (placeholder only — this file is tracked).
6. **Tests (no network).** Provider selection for both values; unsupported
   provider refusal; missing `OPENROUTER_API_KEY` error; OpenAI-style usage
   mapped correctly into the cost log; pricing/estimation with the new model;
   dry-run over the pilot manifest still makes zero client instantiations and
   projects against the $1.00 pilot budget; the full existing suite stays
   green.

## Acceptance criteria

- `uv run python -m pytest -q` green (98 existing + new); `uv run ruff check .`
  clean.
- `make stage2-pilot-dry-run` (offline) reports 30 selected/rendered, 0
  invalid, a projected cost consistent with $0.10/$0.60 per MTok, exit 0, and
  creates no output file or cost log.
- No accepted artifact changes: Stage 0 / Stage 1 / benchmark files still match
  `docs/real-data-validation.md`; the pilot manifest selection is unchanged
  (same 30 bucket IDs — note the manifest's `settings_digest` legitimately
  changes with the new extraction model, and rebuilds remain byte-identical
  run-to-run).
- No API call made anywhere (test-enforced); no key value appears in any file,
  test, log, or commit; `prd (1).md` and `.env` untouched beyond reading the
  key name.
- All new knobs live in `config/settings.yaml`; no magic numbers or inline
  URLs scattered outside the gateway/config.

## Out of scope

- Any real API call — the pilot run itself remains a separately authorized
  orchestrator step after this order is accepted.
- Prompt changes, Stage 3+ work, and any change to intent/rerank model
  configuration beyond leaving the existing entries valid.
- Response-format/JSON-mode experimentation; keep the tolerant parser.

## Report back

Reply with: working branch and commits, design choices (client library or
httpx, how the provider seam is structured), full test and ruff output, the
dry-run summary, any deviation with justification, and anything that should
change this order — escalate rather than improvise.
