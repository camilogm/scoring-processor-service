"""OpenAI-compatible client: Ollama and Vercel AI Gateway are a config change, not a code change."""

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
