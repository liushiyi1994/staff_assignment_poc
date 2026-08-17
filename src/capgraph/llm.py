"""Single gateway for every model call: retry, JSON extraction, cost accounting.

Every LLM interaction in this repo MUST go through call_json(). Prompts live in
prompts/*.md and are loaded via settings.load_prompt — never inline prompt text here.

Two providers are supported, and the provider is resolved **per model** from the
``llm.model_providers`` map:

* ``anthropic``  — the native Messages API via the anthropic SDK.
* ``openrouter`` — the OpenAI-compatible chat completions endpoint over httpx.

There is deliberately no global default provider. Routing used to be a single
``llm.provider`` setting, which meant a Claude model id would have been posted to
an OpenAI-compatible endpoint the moment a second provider entered the config; an
unmapped model is now refused for exactly the same reason an unpriced one is.

Both providers are reduced to one internal :class:`Completion` shape, so retry,
tolerant JSON parsing, the cost-log schema, and every budget guard are
provider-agnostic. OpenAI-style ``prompt_tokens``/``completion_tokens`` are mapped
onto the existing ``input_tokens``/``output_tokens`` log fields.

Cost control is prospective, not retrospective. Before a request is sent:

* a model that is unmapped, mapped to an unsupported provider, or has no
  configured price is refused (never silently routed or priced at $0),
* the call's worst-case cost is estimated offline (no tokenizer call, no network),
* that estimate must fit both ``llm.max_call_cost_usd`` and the stage budget.

Only then is the API client instantiated. Stage names ending in
``_pilot`` draw on ``llm.pilot_budget_usd`` so a small authorized pilot cannot
spend a full stage's ceiling, and its spend stays separable in the cost log.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, NamedTuple

from tenacity import retry, stop_after_attempt, wait_exponential

from .settings import DATA_DIR, settings

_COST_LOG = DATA_DIR / "llm_costs.jsonl"

# The benchmark-v3 self-consistency arm issues several re-ranks at once, so the ledger
# is written and read from more than one thread. Serializing those two operations keeps
# a line from being interleaved and keeps a budget check from reading a half-written
# one; it does not serialize the API calls themselves.
_COST_LOG_LOCK = threading.Lock()

PILOT_STAGE_SUFFIX = "_pilot"
ANTHROPIC = "anthropic"
OPENROUTER = "openrouter"
SUPPORTED_PROVIDERS = (ANTHROPIC, OPENROUTER)


class CostControlError(RuntimeError):
    """A call was refused before any request was made. Never retried."""


class UnknownModelError(CostControlError):
    """The model has no configured price, so its spend cannot be accounted for."""


class UnsupportedProviderError(CostControlError):
    """llm.model_providers names a provider this gateway cannot route to."""


class UnroutableModelError(CostControlError):
    """The model has no llm.model_providers entry, so there is nowhere to send it."""


class CallCostCeilingError(CostControlError):
    """One request's estimated cost exceeds llm.max_call_cost_usd."""


class BudgetExceededError(CostControlError):
    """Logged spend plus the estimate would exceed the stage budget."""


class Completion(NamedTuple):
    """One provider response, normalized before anything else touches it."""

    text: str
    input_tokens: int
    output_tokens: int
    usage_reported: bool = True


def provider_for_model(model: str) -> str:
    """Resolve one model's provider from llm.model_providers, refusing guesses."""
    mapping = settings.get("llm.model_providers") or {}
    if not isinstance(mapping, Mapping):
        raise ValueError("llm.model_providers must be a mapping of model to provider")
    configured = mapping.get(model)
    if configured is None:
        raise UnroutableModelError(
            f"model '{model}' has no llm.model_providers entry — map it to one of "
            f"{', '.join(SUPPORTED_PROVIDERS)} in config/settings.yaml before calling it"
        )
    name = str(configured).strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise UnsupportedProviderError(
            f"llm.model_providers['{model}'] is '{name}', which is not supported — "
            f"use one of {', '.join(SUPPORTED_PROVIDERS)}"
        )
    return name


@lru_cache
def _provider_client(provider_name: str):
    """Build one provider client. Cached per provider, never at import time."""
    if provider_name == ANTHROPIC:
        import anthropic
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set — copy .env.example to .env")
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)

    import httpx
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set — copy .env.example to .env")
    return httpx.Client(
        base_url=str(settings["llm.openrouter_base_url"]),
        # The key lives only in this header, never in a log line or an exception.
        headers={
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
        },
        timeout=float(settings["llm.request_timeout_seconds"]),
    )


def _client(provider_name: str):
    """The single lazy client seam: importing this module needs no credentials."""
    return _provider_client(provider_name)


def model_price_usd_per_mtok(model: str) -> tuple[float, float]:
    """Return (input, output) USD per million tokens, refusing unpriced models."""
    prices = settings.get("llm.pricing_usd_per_mtok") or {}
    if not isinstance(prices, Mapping):
        raise ValueError("llm.pricing_usd_per_mtok must be a mapping of model to prices")
    entry = prices.get(model)
    if entry is None:
        raise UnknownModelError(
            f"model '{model}' has no llm.pricing_usd_per_mtok entry — add its verified "
            "price to config/settings.yaml before calling it"
        )
    if not isinstance(entry, Mapping) or "input" not in entry or "output" not in entry:
        raise ValueError(f"llm.pricing_usd_per_mtok['{model}'] needs input and output prices")
    return float(entry["input"]), float(entry["output"])


def resolve_max_tokens(max_tokens: int | None = None) -> int:
    tokens = int(settings["llm.max_output_tokens"] if max_tokens is None else max_tokens)
    if tokens < 1:
        raise ValueError("max output tokens must be at least 1")
    return tokens


def estimate_tokens(text: str) -> int:
    """Coarse offline token estimate from llm.chars_per_token_estimate."""
    chars_per_token = float(settings["llm.chars_per_token_estimate"])
    if chars_per_token <= 0:
        raise ValueError("llm.chars_per_token_estimate must be positive")
    return math.ceil(len(text) / chars_per_token)


def estimate_call_cost_usd(prompt: str, *, model: str, max_tokens: int | None = None) -> float:
    """Worst-case cost of one request: estimated input plus the full output allowance."""
    price_in, price_out = model_price_usd_per_mtok(model)
    tokens_out = resolve_max_tokens(max_tokens)
    return estimate_tokens(prompt) / 1e6 * price_in + tokens_out / 1e6 * price_out


def stage_budget_usd(stage: str) -> float:
    """Pilot stages spend from the pilot budget; every other stage from the ceiling."""
    key = (
        "llm.pilot_budget_usd"
        if stage.endswith(PILOT_STAGE_SUFFIX)
        else "llm.max_stage_cost_usd"
    )
    return float(settings[key])


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from a model response, tolerating ```json fences and prose."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = match.group(1) if match else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def _log_cost(
    model: str, completion: Completion, stage: str, purpose: str | None = None
) -> float:
    price_in, price_out = model_price_usd_per_mtok(model)
    cost = (
        completion.input_tokens / 1e6 * price_in
        + completion.output_tokens / 1e6 * price_out
    )
    record: dict[str, Any] = {
        "ts": time.time(), "stage": stage, "model": model,
        "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens,
        "cost_usd": round(cost, 6),
    }
    if purpose:
        # Which call this was inside the stage (intent parse, re-rank, extraction).
        # Budgets are per stage; this only makes the log auditable per call type, and
        # lets the benchmark attribute spend to the ablation it belongs to.
        record["purpose"] = purpose
    if not completion.usage_reported:
        # The counts are this gateway's conservative estimate, not the provider's.
        # Marked so an audit can tell the two apart; stage_cost_so_far is unaffected.
        record["usage_source"] = "estimate"
    _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _COST_LOG_LOCK, open(_COST_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return cost


def cost_log_path():
    """Where completed calls are logged. Resolved on call so tests can redirect it."""
    return _COST_LOG


def stage_cost_so_far(stage: str) -> float:
    if not _COST_LOG.exists():
        return 0.0
    with _COST_LOG_LOCK:
        lines = _COST_LOG.read_text().splitlines()
    total = 0.0
    for line in lines:
        rec = json.loads(line)
        if rec["stage"] == stage:
            total += rec["cost_usd"]
    return total


def enforce_call_cost_ceiling(estimate_usd: float, *, model: str) -> None:
    """Refuse a single request that is too expensive to be worth attempting."""
    ceiling = float(settings["llm.max_call_cost_usd"])
    if estimate_usd > ceiling:
        raise CallCostCeilingError(
            f"estimated call cost ${estimate_usd:.4f} for model '{model}' exceeds "
            f"llm.max_call_cost_usd ${ceiling:.4f}"
        )


def enforce_call_budget(estimate_usd: float, *, stage: str, model: str) -> None:
    """Refuse one call that breaks the per-call ceiling or the stage budget."""
    enforce_call_cost_ceiling(estimate_usd, model=model)
    enforce_projected_stage_cost(estimate_usd, stage=stage)


def enforce_projected_stage_cost(projected_usd: float, *, stage: str) -> None:
    """Refuse work whose projected spend would break the stage budget."""
    budget = stage_budget_usd(stage)
    spent = stage_cost_so_far(stage)
    if spent + projected_usd > budget:
        raise BudgetExceededError(
            f"stage '{stage}' would spend ${spent + projected_usd:.4f} "
            f"(logged ${spent:.4f} + projected ${projected_usd:.4f}) against a "
            f"${budget:.4f} budget — see data/llm_costs.jsonl"
        )


def _complete_anthropic(client: Any, *, prompt: str, model: str, max_tokens: int) -> Completion:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=settings["llm.temperature"],
        messages=[{"role": "user", "content": prompt}],
    )
    return Completion(
        text=resp.content[0].text,
        input_tokens=int(resp.usage.input_tokens),
        output_tokens=int(resp.usage.output_tokens),
    )


def _complete_openrouter(client: Any, *, prompt: str, model: str, max_tokens: int) -> Completion:
    """OpenAI-compatible chat completion, normalized onto the internal shape."""
    response = client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": settings["llm.temperature"],
        },
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        # OpenRouter can report an upstream failure in a 200 body.
        raise RuntimeError(f"OpenRouter returned no choices: {payload.get('error') or payload}")
    text = (choices[0].get("message") or {}).get("content") or ""
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    if not (input_tokens or output_tokens):
        # Never bill a real response at $0: fall back to the same conservative
        # estimate the pre-call guard used, and mark the log entry as estimated.
        return Completion(
            text=text,
            input_tokens=estimate_tokens(prompt),
            output_tokens=max_tokens,
            usage_reported=False,
        )
    return Completion(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


_COMPLETERS = {ANTHROPIC: _complete_anthropic, OPENROUTER: _complete_openrouter}


def call_json(
    prompt: str,
    model: str,
    stage: str,
    max_tokens: int | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """One prompt -> parsed JSON dict.

    Provider routing and cost control run first and are never retried; the
    API/parse retry loop starts only after the call has been authorized. Raises
    after retries on API or parse failure. ``purpose`` is recorded on the cost-log
    line and has no effect on routing or budgets.
    """
    provider_name = provider_for_model(model)
    tokens_out = resolve_max_tokens(max_tokens)
    estimate = estimate_call_cost_usd(prompt, model=model, max_tokens=tokens_out)
    enforce_call_budget(estimate, stage=stage, model=model)
    return _call_json_with_retry(
        prompt,
        provider_name=provider_name,
        model=model,
        stage=stage,
        max_tokens=tokens_out,
        purpose=purpose,
    )


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
def _call_json_with_retry(
    prompt: str,
    *,
    provider_name: str,
    model: str,
    stage: str,
    max_tokens: int,
    purpose: str | None = None,
) -> dict[str, Any]:
    completion = _COMPLETERS[provider_name](
        _client(provider_name), prompt=prompt, model=model, max_tokens=max_tokens
    )
    _log_cost(model, completion, stage, purpose)
    return _extract_json(completion.text)
