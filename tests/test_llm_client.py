import httpx
import openai
import pytest

from app.llm import client
from app.pipeline.model import ModelError
from app.settings import Settings

LLAMA_CPP_OVERFLOW = (
    '{"error":{"code":400,"message":"request (6042 tokens) exceeds the available context size '
    '(4096 tokens), try increasing it","type":"exceed_context_size_error","n_prompt_tokens":6042,"n_ctx":4096}}'
)


def _settings() -> Settings:
    return Settings(llm_max_frames=6, _env_file=None)


def _raising_openai(monkeypatch, exc: Exception) -> None:
    class FakeCompletions:
        def create(self, **_):
            raise exc

    class FakeOpenAI:
        def __init__(self, **_):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)


def _bad_request(message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "http://llm/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(message, response=response, body={"message": message})


@pytest.mark.parametrize(
    "message",
    [
        LLAMA_CPP_OVERFLOW,
        "This model's maximum context length is 8192 tokens. However, your messages resulted in 9000 tokens.",
        "context_length_exceeded",
    ],
)
def test_context_overflow_is_reported_as_an_actionable_model_error(monkeypatch, message):
    _raising_openai(monkeypatch, _bad_request(message))

    with pytest.raises(ModelError) as info:
        client.chat(_settings(), [{"role": "user", "content": "hi"}])

    assert info.value.code == "model_error"
    assert "context window" in info.value.message
    assert "LLM_MAX_FRAMES" in info.value.message


def test_context_overflow_message_includes_token_counts_when_the_server_reports_them(monkeypatch):
    _raising_openai(monkeypatch, _bad_request(LLAMA_CPP_OVERFLOW))

    with pytest.raises(ModelError) as info:
        client.chat(_settings(), [{"role": "user", "content": "hi"}])

    assert "6042" in info.value.message
    assert "4096" in info.value.message
    assert "currently 6" in info.value.message


def test_other_bad_requests_keep_the_raw_server_message(monkeypatch):
    _raising_openai(monkeypatch, _bad_request("unknown model 'nope'"))

    with pytest.raises(ModelError) as info:
        client.chat(_settings(), [{"role": "user", "content": "hi"}])

    assert info.value.message.startswith("Model call failed: BadRequestError:")
    assert "context window" not in info.value.message


def _completing_openai(monkeypatch, completion_id: str) -> None:
    completion = openai.types.chat.ChatCompletion.model_validate({
        "id": completion_id,
        "object": "chat.completion",
        "created": 0,
        "model": "google/gemini-2.5-flash",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    })

    class FakeCompletions:
        def create(self, **_):
            return completion

    class FakeOpenAI:
        def __init__(self, **_):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)


def _gateway_settings(**overrides) -> Settings:
    return Settings(
        llm_base_url="https://ai-gateway.vercel.sh/v1",
        llm_api_key="vck_test",
        llm_price_input_per_mtok=1.0,
        llm_price_output_per_mtok=10.0,
        _env_file=None,
        **overrides,
    )


def _generation_lookup(monkeypatch, responses: list[httpx.Response]) -> list[httpx.Request]:
    """Serve `responses` in order to GET /generation (repeating the last) and record the requests."""
    seen: list[httpx.Request] = []

    def fake_get(url, *, params, headers, timeout):
        request = httpx.Request("GET", url, params=params, headers=headers)
        seen.append(request)
        response = responses[min(len(seen), len(responses)) - 1]
        response.request = request
        return response

    monkeypatch.setattr(client.httpx, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    return seen


def test_gateway_cost_comes_from_the_generation_lookup(monkeypatch):
    _completing_openai(monkeypatch, "gen_01ABC")
    seen = _generation_lookup(monkeypatch, [httpx.Response(200, json={"data": {"id": "gen_01ABC", "total_cost": 0.0042}})])

    completion = client.chat(_gateway_settings(), [{"role": "user", "content": "hi"}])

    assert completion.cost_usd == 0.0042
    assert str(seen[0].url) == "https://ai-gateway.vercel.sh/v1/generation?id=gen_01ABC"
    assert seen[0].headers["authorization"] == "Bearer vck_test"


def test_gateway_lookup_retries_until_the_usage_event_is_ingested(monkeypatch):
    _completing_openai(monkeypatch, "gen_01ABC")
    not_yet = httpx.Response(404, json={"error": {"message": "Usage event not found"}})
    found = httpx.Response(200, json={"data": {"total_cost": 0.0042}})
    seen = _generation_lookup(monkeypatch, [not_yet, not_yet, found])

    completion = client.chat(_gateway_settings(), [{"role": "user", "content": "hi"}])

    assert completion.cost_usd == 0.0042
    assert len(seen) == 3


def test_gateway_lookup_failure_falls_back_to_the_price_estimate(monkeypatch):
    _completing_openai(monkeypatch, "gen_01ABC")
    _generation_lookup(monkeypatch, [httpx.Response(404, json={})])

    completion = client.chat(_gateway_settings(), [{"role": "user", "content": "hi"}])

    # 1000 input tokens at $1/M + 100 output tokens at $10/M
    assert completion.cost_usd == 0.002


def test_non_gateway_completions_skip_the_lookup(monkeypatch):
    _completing_openai(monkeypatch, "chatcmpl-123")
    seen = _generation_lookup(monkeypatch, [httpx.Response(200, json={"data": {"total_cost": 9.0}})])

    completion = client.chat(_gateway_settings(), [{"role": "user", "content": "hi"}])

    assert seen == []
    assert completion.cost_usd == 0.002


def test_cost_over_the_per_clip_budget_is_logged(monkeypatch, caplog):
    _completing_openai(monkeypatch, "gen_01ABC")
    _generation_lookup(monkeypatch, [httpx.Response(200, json={"data": {"total_cost": 1.25}})])

    with caplog.at_level("WARNING", logger="app.llm.client"):
        completion = client.chat(_gateway_settings(max_cost_per_clip_usd=1.0), [{"role": "user", "content": "hi"}])

    assert completion.cost_usd == 1.25
    assert "over the $1.00 per-clip budget" in caplog.text
