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
