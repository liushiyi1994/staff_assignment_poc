"""Provider routing tests for the LLM gateway. No network, no real credentials.

Routing is per model (``llm.model_providers``), so these also pin the refusal
behaviour: an unmapped model, or one mapped to an unsupported provider, is refused
before any client exists — the same pre-flight treatment an unpriced model gets.

Every test either patches the client seam or blanks the key before the factory
runs, so no test ever constructs a real client or reads a real key value.
"""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from capgraph import llm
from capgraph.settings import settings

MODEL = "test-model"
PROMPT = "Summarize the tickets."


@pytest.fixture(autouse=True)
def gateway_sandbox(tmp_path, monkeypatch):
    """Isolate the ledger and budgets, and drop any cached client."""
    monkeypatch.setattr(llm, "_COST_LOG", tmp_path / "llm_costs.jsonl")
    monkeypatch.setitem(settings._cfg["llm"], "pilot_budget_usd", 1.0)
    monkeypatch.setitem(settings._cfg["llm"], "max_stage_cost_usd", 25.0)
    monkeypatch.setitem(settings._cfg["llm"], "max_call_cost_usd", 1.0)
    llm._provider_client.cache_clear()
    yield tmp_path / "llm_costs.jsonl"
    llm._provider_client.cache_clear()


@pytest.fixture
def fake_pricing(monkeypatch):
    """Price only MODEL, so tests never depend on shipped prices moving."""
    monkeypatch.setitem(
        settings._cfg["llm"], "pricing_usd_per_mtok", {MODEL: {"input": 1.0, "output": 5.0}}
    )


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeHTTPClient:
    """Stands in for the httpx client; records what would have been sent."""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self._status_code = status_code
        self.requests: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> FakeResponse:  # noqa: A002 - httpx kwarg name
        self.requests.append((path, json))
        return FakeResponse(self._payload, self._status_code)


def _openrouter_payload(text: str = '{"skip": true, "skip_reason": "too vague"}', **usage):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 2000, "completion_tokens": 400, **usage},
    }


def _route(monkeypatch, model: str, provider_name: str) -> None:
    monkeypatch.setitem(
        settings._cfg["llm"], "model_providers", {model: provider_name}
    )


def _use_openrouter(monkeypatch, client: FakeHTTPClient) -> None:
    _route(monkeypatch, MODEL, "openrouter")
    monkeypatch.setattr(llm, "_client", lambda provider_name: client)


# ---------- per-model provider selection ----------

@pytest.mark.parametrize("name", ["anthropic", "openrouter"])
def test_supported_providers_are_selected_per_model(monkeypatch, name):
    _route(monkeypatch, MODEL, name)
    assert llm.provider_for_model(MODEL) == name
    assert name in llm._COMPLETERS


def test_provider_value_is_normalized(monkeypatch):
    _route(monkeypatch, MODEL, "  OpenRouter  ")
    assert llm.provider_for_model(MODEL) == "openrouter"


def test_each_model_routes_to_its_own_provider(monkeypatch):
    """The point of the map: two models in one config go to different endpoints."""
    monkeypatch.setitem(
        settings._cfg["llm"],
        "model_providers",
        {"claude-sonnet-5": "anthropic", "openai/gpt-5.6-terra": "openrouter"},
    )
    assert llm.provider_for_model("claude-sonnet-5") == "anthropic"
    assert llm.provider_for_model("openai/gpt-5.6-terra") == "openrouter"


def test_unmapped_model_is_refused_before_any_client(monkeypatch, fake_pricing):
    """A priced but unrouted model still has nowhere to go — refuse, never guess."""
    monkeypatch.setitem(settings._cfg["llm"], "model_providers", {"other-model": "openrouter"})
    instantiations = SimpleNamespace(count=0)

    def _forbidden(provider_name):
        instantiations.count += 1
        raise AssertionError("no client may be built for an unmapped model")

    monkeypatch.setattr(llm, "_client", _forbidden)

    with pytest.raises(llm.UnroutableModelError, match=MODEL):
        llm.provider_for_model(MODEL)
    with pytest.raises(llm.UnroutableModelError):
        llm.call_json(PROMPT, model=MODEL, stage="stage2_pilot")
    assert instantiations.count == 0
    # Refused pre-flight like an unpriced model, so the caller stops the run.
    assert issubclass(llm.UnroutableModelError, llm.CostControlError)


def test_unsupported_provider_is_refused_before_any_client(monkeypatch):
    _route(monkeypatch, MODEL, "acme-ai")
    instantiations = SimpleNamespace(count=0)

    def _forbidden(provider_name):
        instantiations.count += 1
        raise AssertionError("no client may be built for an unsupported provider")

    monkeypatch.setattr(llm, "_client", _forbidden)

    with pytest.raises(llm.UnsupportedProviderError, match="acme-ai"):
        llm.provider_for_model(MODEL)
    with pytest.raises(llm.UnsupportedProviderError):
        llm.call_json(PROMPT, model=MODEL, stage="stage2_pilot")
    assert instantiations.count == 0
    # Refused pre-flight like an unpriced model, so Stage 2 stops the run.
    assert issubclass(llm.UnsupportedProviderError, llm.CostControlError)


def test_a_malformed_provider_map_is_rejected(monkeypatch):
    monkeypatch.setitem(settings._cfg["llm"], "model_providers", ["openrouter"])

    with pytest.raises(ValueError, match="llm.model_providers"):
        llm.provider_for_model(MODEL)


def test_shipped_config_routes_and_prices_every_configured_model():
    """Guards against config drift between the model, the route, and the price map."""
    for key in ("llm.extraction_model", "llm.intent_model", "llm.rerank_model"):
        model = settings[key]
        assert llm.provider_for_model(model) in llm.SUPPORTED_PROVIDERS
        assert llm.model_price_usd_per_mtok(model)


# ---------- credentials ----------

def test_missing_openrouter_key_raises_without_naming_a_value(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        llm._client("openrouter")
    assert "OPENROUTER_API_KEY" in str(excinfo.value)


def test_missing_anthropic_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm._client("anthropic")


def test_importing_the_gateway_requires_no_credentials(monkeypatch, fake_pricing):
    """The client seam is lazy: module import and cost math never touch a key."""
    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)

    assert llm.estimate_call_cost_usd(PROMPT, model=MODEL, max_tokens=100) > 0
    assert llm.stage_budget_usd("stage2_pilot") == 1.0


# ---------- openrouter request and response ----------

def test_openrouter_sends_an_openai_shaped_request_without_key_material(monkeypatch, fake_pricing):
    client = FakeHTTPClient(_openrouter_payload())
    _use_openrouter(monkeypatch, client)
    monkeypatch.setitem(settings._cfg["llm"], "temperature", 0.0)

    llm.call_json(PROMPT, model=MODEL, stage="stage2_pilot", max_tokens=1234)

    (path, body), = client.requests
    assert path == "/chat/completions"
    assert body["model"] == MODEL
    assert body["messages"] == [{"role": "user", "content": PROMPT}]
    assert body["max_tokens"] == 1234
    assert body["temperature"] == 0.0
    # Credentials travel in the client's headers, never in the request body.
    assert "Bearer" not in json.dumps(body)
    assert not {"authorization", "api_key"} & {key.lower() for key in body}


def test_openrouter_usage_maps_onto_the_existing_cost_log_schema(
    monkeypatch, fake_pricing, gateway_sandbox
):
    _use_openrouter(monkeypatch, FakeHTTPClient(_openrouter_payload()))

    assert llm.call_json(PROMPT, model=MODEL, stage="stage2_pilot") == {
        "skip": True, "skip_reason": "too vague"
    }

    record = json.loads(gateway_sandbox.read_text().splitlines()[0])
    assert record["input_tokens"] == 2000        # from prompt_tokens
    assert record["output_tokens"] == 400        # from completion_tokens
    assert record["stage"] == "stage2_pilot" and record["model"] == MODEL
    assert "usage_source" not in record          # provider-reported, schema unchanged
    expected = 2000 / 1e6 * 1.0 + 400 / 1e6 * 5.0
    assert record["cost_usd"] == pytest.approx(round(expected, 6))
    assert llm.stage_cost_so_far("stage2_pilot") == pytest.approx(round(expected, 6))


def test_openrouter_response_is_parsed_with_the_tolerant_json_extractor(monkeypatch, fake_pricing):
    fenced = 'Here you go:\n```json\n{"confidence": "high"}\n```\nHope that helps.'
    _use_openrouter(monkeypatch, FakeHTTPClient(_openrouter_payload(text=fenced)))

    assert llm.call_json(PROMPT, model=MODEL, stage="stage2_pilot") == {"confidence": "high"}


def test_missing_usage_is_billed_at_the_conservative_estimate_not_zero(
    monkeypatch, fake_pricing, gateway_sandbox
):
    payload = _openrouter_payload()
    del payload["usage"]
    _use_openrouter(monkeypatch, FakeHTTPClient(payload))

    llm.call_json(PROMPT, model=MODEL, stage="stage2_pilot", max_tokens=800)

    record = json.loads(gateway_sandbox.read_text().splitlines()[0])
    assert record["usage_source"] == "estimate"
    assert record["input_tokens"] == llm.estimate_tokens(PROMPT)
    assert record["output_tokens"] == 800
    assert record["cost_usd"] > 0
    assert llm.stage_cost_so_far("stage2_pilot") > 0


def test_upstream_error_in_a_200_body_is_raised():
    client = FakeHTTPClient({"error": {"message": "upstream refused", "code": 502}})

    with pytest.raises(RuntimeError, match="no choices"):
        llm._complete_openrouter(client, prompt=PROMPT, model=MODEL, max_tokens=100)


def test_http_error_status_is_raised():
    client = FakeHTTPClient(_openrouter_payload(), status_code=429)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        llm._complete_openrouter(client, prompt=PROMPT, model=MODEL, max_tokens=100)


def test_anthropic_response_is_normalized_the_same_way(
    monkeypatch, fake_pricing, gateway_sandbox
):
    _route(monkeypatch, MODEL, "anthropic")
    response = SimpleNamespace(
        content=[SimpleNamespace(text='{"confidence": "medium"}')],
        usage=SimpleNamespace(input_tokens=1500, output_tokens=250),
    )
    monkeypatch.setattr(
        llm, "_client", lambda provider_name: SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kwargs: response)
        )
    )

    assert llm.call_json(PROMPT, model=MODEL, stage="stage2") == {"confidence": "medium"}

    record = json.loads(gateway_sandbox.read_text().splitlines()[0])
    assert (record["input_tokens"], record["output_tokens"]) == (1500, 250)
    assert "usage_source" not in record


# ---------- pricing for the designated extraction model ----------

def test_designated_extraction_model_is_priced_as_verified():
    assert llm.model_price_usd_per_mtok("openai/gpt-5.6-luna") == (0.10, 0.60)


def test_estimate_uses_the_new_prices(monkeypatch):
    monkeypatch.setitem(
        settings._cfg["llm"],
        "pricing_usd_per_mtok",
        {"openai/gpt-5.6-luna": {"input": 0.10, "output": 0.60}},
    )
    monkeypatch.setitem(settings._cfg["llm"], "chars_per_token_estimate", 4.0)

    # 4000 chars -> 1000 input tokens at $0.10/MTok, plus 1500 output at $0.60/MTok.
    estimate = llm.estimate_call_cost_usd(
        "x" * 4000, model="openai/gpt-5.6-luna", max_tokens=1500
    )
    assert estimate == pytest.approx(1000 / 1e6 * 0.10 + 1500 / 1e6 * 0.60)
    assert estimate < float(settings["llm.max_call_cost_usd"])


def test_the_suite_blocks_outbound_connections():
    """Proves the conftest network guard is armed rather than silently inert."""
    with pytest.raises(AssertionError, match="must not open a network connection"):
        socket.socket().connect(("openrouter.ai", 443))


def test_unknown_model_is_still_refused_under_the_new_provider(monkeypatch):
    _route(monkeypatch, "openai/not-configured", "openrouter")
    monkeypatch.setattr(llm, "_client", FakeHTTPClient(_openrouter_payload()))

    with pytest.raises(llm.UnknownModelError):
        llm.call_json(PROMPT, model="openai/not-configured", stage="stage2_pilot")


def test_designated_query_models_are_priced_as_verified():
    assert llm.model_price_usd_per_mtok("openai/gpt-5.6-terra") == (1.00, 6.00)
    assert settings["llm.intent_model"] == settings["llm.rerank_model"] == "openai/gpt-5.6-terra"
