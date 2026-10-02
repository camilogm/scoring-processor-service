"""OpenAI-compatible client: Ollama and Vercel AI Gateway are a config change, not a code change."""

import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.pipeline.model import ModelError
from app.settings import Settings

log = logging.getLogger(__name__)

# Vercel AI Gateway ingests usage asynchronously: a lookup right after the call returns 404
# for a few seconds.
_GENERATION_LOOKUP_ATTEMPTS = 5
_GENERATION_LOOKUP_DELAY_S = 2.0


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


def _gateway_cost(settings: Settings, generation_id: str) -> float | None:
    """Actual cost billed by Vercel AI Gateway for one generation, or None if it can't be read."""
    for attempt in range(_GENERATION_LOOKUP_ATTEMPTS):
        if attempt:
            time.sleep(_GENERATION_LOOKUP_DELAY_S)
        try:
            resp = httpx.get(
                f"{settings.llm_base_url.rstrip('/')}/generation",
                params={"id": generation_id},
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                timeout=10,
            )
        except httpx.HTTPError as exc:
            log.warning("Gateway cost lookup for %s failed: %s", generation_id, exc)
            return None
        if resp.status_code == 404:  # not ingested yet
            continue
        if resp.is_success:
            return float(resp.json()["data"]["total_cost"])
        log.warning("Gateway cost lookup for %s returned %s", generation_id, resp.status_code)
        return None
    log.warning("Gateway cost for %s not available after %s attempts", generation_id, _GENERATION_LOOKUP_ATTEMPTS)
    return None


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
    # Prefer the billed cost: Vercel AI Gateway generation ids (gen_...) can be looked up, some
    # gateways report cost in usage; otherwise estimate from configured prices.
    reported = (usage.model_extra or {}).get("cost") if usage else None
    if reported is None and (resp.id or "").startswith("gen_"):
        reported = _gateway_cost(settings, resp.id)
    if reported is not None:
        cost = float(reported)
    else:
        # Unset prices only pass Settings validation on a local endpoint, where the call is free.
        price_in = settings.llm_price_input_per_mtok or 0.0
        price_out = settings.llm_price_output_per_mtok or 0.0
        cost = (input_tokens * price_in + output_tokens * price_out) / 1e6
        if not settings.llm_is_local:
            log.warning("Cost of %s not reported by the provider; estimated from LLM_PRICE_*: $%.6f", resp.id, cost)
    if cost > settings.max_cost_per_clip_usd:
        log.warning(
            "Judge call cost $%.4f, over the $%.2f per-clip budget (MAX_COST_PER_CLIP_USD)",
            cost,
            settings.max_cost_per_clip_usd,
        )
    return Completion(
        content=resp.choices[0].message.content,
        model=resp.model or settings.llm_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 6),
    )
