"""OpenAI-compatible client: Ollama and Vercel AI Gateway are a config change, not a code change."""

import re
from dataclasses import dataclass

from app.pipeline.model import ModelError
from app.settings import Settings


@dataclass
class Completion:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


# How llama.cpp (llama-server, recent Ollama, LM Studio, Docker Model Runner), OpenAI and vLLM
# word a prompt that does not fit.
_CONTEXT_OVERFLOW = re.compile(
    r"exceed_context_size_error|exceeds the available context size|context_length_exceeded|maximum context length",
    re.IGNORECASE,
)
_LLAMA_CPP_COUNTS = re.compile(r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)")


def _is_context_overflow(detail: str) -> bool:
    return bool(_CONTEXT_OVERFLOW.search(detail))


def _context_overflow_message(detail: str, settings: Settings) -> str:
    counts = _LLAMA_CPP_COUNTS.search(detail)
    size = f" ({counts.group(1)} tokens > {counts.group(2)})" if counts else ""
    return (
        f"Model call failed: the judge prompt does not fit the model server's context window{size}. "
        "Start the server with a larger context (e.g. OLLAMA_CONTEXT_LENGTH=16384 or llama-server -c 16384) "
        "or send fewer frames "
        f"(LLM_MAX_FRAMES, currently {settings.llm_max_frames}; or LLM_VISION=false)."
    )


def chat(settings: Settings, messages: list[dict]) -> Completion:
    import openai

    client = openai.OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_s,
        max_retries=0,  # retries are out of scope; a failed call fails the analysis with a clear reason
    )
    kwargs = {}
    if settings.llm_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=0,
            seed=settings.llm_seed,
            **kwargs,
        )
    except openai.BadRequestError as exc:
        if _is_context_overflow(str(exc)):
            raise ModelError(_context_overflow_message(str(exc), settings)) from exc
        raise ModelError(f"Model call failed: {type(exc).__name__}: {exc}") from exc
    except openai.OpenAIError as exc:
        raise ModelError(f"Model call failed: {type(exc).__name__}: {exc}") from exc

    if not resp.choices or not resp.choices[0].message.content:
        raise ModelError("Model returned an empty response.")

    usage = resp.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    # Some gateways report cost in usage; otherwise estimate from configured prices.
    reported = (usage.model_extra or {}).get("cost") if usage else None
    cost = (
        float(reported)
        if reported is not None
        else (input_tokens * settings.llm_price_input_per_mtok + output_tokens * settings.llm_price_output_per_mtok) / 1e6
    )
    return Completion(
        content=resp.choices[0].message.content,
        model=resp.model or settings.llm_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 6),
    )
