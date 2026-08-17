"""Fixture tests for prospective LLM cost control. No network, no credentials."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from capgraph import llm
from capgraph.settings import settings

MODEL = "test-model"


@pytest.fixture
def cost_log(tmp_path, monkeypatch):
    """Redirect the cost ledger, then price and route the fake models under test.

    ``claude-not-configured`` is routed but deliberately left unpriced, so the
    unknown-price refusal is what these tests actually exercise.
    """
    path = tmp_path / "llm_costs.jsonl"
    monkeypatch.setattr(llm, "_COST_LOG", path)
    monkeypatch.setitem(
        settings._cfg["llm"], "pricing_usd_per_mtok", {MODEL: {"input": 1.0, "output": 5.0}}
    )
    monkeypatch.setitem(
        settings._cfg["llm"],
        "model_providers",
        {MODEL: "anthropic", "claude-not-configured": "anthropic"},
    )
    return path


@pytest.fixture
def client_spy(monkeypatch):
    """Fail loudly on any client instantiation, and count the attempts."""
    calls = SimpleNamespace(instantiations=0)

    def _forbidden(provider_name):
        calls.instantiations += 1
        raise AssertionError("no API client may be instantiated in this test")

    monkeypatch.setattr(llm, "_client", _forbidden)
    return calls


def _log(path, stage: str, cost: float) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps({
            "ts": 0.0, "stage": stage, "model": MODEL,
            "input_tokens": 1000, "output_tokens": 100, "cost_usd": cost,
        }) + "\n")


def test_unknown_model_is_refused_before_any_client(cost_log, client_spy):
    with pytest.raises(llm.UnknownModelError):
        llm.model_price_usd_per_mtok("claude-not-configured")
    with pytest.raises(llm.UnknownModelError):
        llm.call_json("prompt", model="claude-not-configured", stage="stage2_pilot")
    assert client_spy.instantiations == 0


def test_unpriced_model_is_never_charged_zero(cost_log):
    completion = llm.Completion(text="{}", input_tokens=1000, output_tokens=1000)
    with pytest.raises(llm.UnknownModelError):
        llm._log_cost("claude-not-configured", completion, "stage2_pilot")
    assert not cost_log.exists()


def test_call_cost_estimate_covers_input_and_full_output_allowance(cost_log, monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "chars_per_token_estimate", 4.0)
    # 4000 chars -> 1000 input tokens at $1/Mtok; 2000 output tokens at $5/Mtok.
    estimate = llm.estimate_call_cost_usd("x" * 4000, model=MODEL, max_tokens=2000)
    assert estimate == pytest.approx(1000 / 1e6 * 1.0 + 2000 / 1e6 * 5.0)


def test_per_call_ceiling_refuses_before_any_client(cost_log, client_spy, monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "max_call_cost_usd", 0.000001)
    with pytest.raises(llm.CallCostCeilingError):
        llm.call_json("prompt", model=MODEL, stage="stage2_pilot")
    assert client_spy.instantiations == 0


def test_pilot_stage_draws_on_the_pilot_budget(cost_log, monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 1.5)
    monkeypatch.setitem(settings._cfg["llm"], "max_stage_cost_usd", 25.0)
    assert llm.stage_budget_usd("stage2_pilot") == 1.5
    assert llm.stage_budget_usd("stage2") == 25.0


def test_pilot_budget_is_enforced_prospectively(cost_log, client_spy, monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 0.01)
    monkeypatch.setitem(settings._cfg["llm"], "max_call_cost_usd", 1.0)
    _log(cost_log, "stage2_pilot", 0.0099)

    # Spend so far is still under budget, so a retrospective check would allow this.
    assert llm.stage_cost_so_far("stage2_pilot") < llm.stage_budget_usd("stage2_pilot")
    with pytest.raises(llm.BudgetExceededError):
        llm.call_json("prompt " * 100, model=MODEL, stage="stage2_pilot")
    assert client_spy.instantiations == 0


def test_full_stage_spend_does_not_consume_the_pilot_budget(cost_log, monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 0.10)
    _log(cost_log, "stage2", 24.0)

    assert llm.stage_cost_so_far("stage2_pilot") == 0.0
    llm.enforce_projected_stage_cost(0.05, stage="stage2_pilot")


def test_authorized_call_logs_actual_cost_from_configured_prices(cost_log, monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 1.0)
    monkeypatch.setitem(settings._cfg["llm"], "max_call_cost_usd", 1.0)
    response = SimpleNamespace(
        content=[SimpleNamespace(text='{"skip": true, "skip_reason": "too vague"}')],
        usage=SimpleNamespace(input_tokens=2000, output_tokens=400),
    )
    stub = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    monkeypatch.setattr(llm, "_client", lambda provider_name: stub)

    assert llm.call_json("prompt", model=MODEL, stage="stage2_pilot") == {
        "skip": True, "skip_reason": "too vague"
    }
    expected = 2000 / 1e6 * 1.0 + 400 / 1e6 * 5.0
    assert llm.stage_cost_so_far("stage2_pilot") == pytest.approx(round(expected, 6))
