"""Single gateway for all Claude calls: retry, JSON extraction, cost accounting.

Every LLM interaction in this repo MUST go through call_json(). Prompts live in
prompts/*.md and are loaded via settings.load_prompt — never inline prompt text here.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from functools import lru_cache

from tenacity import retry, stop_after_attempt, wait_exponential

from .settings import DATA_DIR, settings

_COST_LOG = DATA_DIR / "llm_costs.jsonl"


@lru_cache
def _client():
    """Lazy so importing this module never requires credentials/network (tests)."""
    import anthropic
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — copy .env.example to .env")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)

# USD per million tokens (input, output). Verify against current pricing docs.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
}


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from a model response, tolerating ```json fences and prose."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = match.group(1) if match else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def _log_cost(model: str, usage: Any, stage: str) -> float:
    inp, out = _PRICES.get(model, (0.0, 0.0))
    cost = usage.input_tokens / 1e6 * inp + usage.output_tokens / 1e6 * out
    _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_COST_LOG, "a") as f:
        f.write(json.dumps({
            "ts": time.time(), "stage": stage, "model": model,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cost_usd": round(cost, 6),
        }) + "\n")
    return cost


def stage_cost_so_far(stage: str) -> float:
    if not _COST_LOG.exists():
        return 0.0
    total = 0.0
    for line in _COST_LOG.read_text().splitlines():
        rec = json.loads(line)
        if rec["stage"] == stage:
            total += rec["cost_usd"]
    return total


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
def call_json(prompt: str, model: str, stage: str, max_tokens: int = 1500) -> dict[str, Any]:
    """One prompt -> parsed JSON dict. Raises after retries on API or parse failure."""
    budget = settings["llm.max_stage_cost_usd"]
    if stage_cost_so_far(stage) > budget:
        raise RuntimeError(f"Stage '{stage}' exceeded budget ${budget} — check data/llm_costs.jsonl")
    resp = _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=settings["llm.temperature"],
        messages=[{"role": "user", "content": prompt}],
    )
    _log_cost(model, resp.usage, stage)
    return _extract_json(resp.content[0].text)
