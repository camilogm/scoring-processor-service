"""Stage 4: one model call judges hook, completeness, clarity and on-screen text.

The model never computes the overall score or the verdict.
"""

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm.client import chat
from app.pipeline.model import ModelOutputError, fmt_ts
from app.settings import Settings

PROMPT_VERSION = "v1"
PROMPT_PATH = Path(__file__).parents[1] / "config" / "prompts" / "judge_v1.md"
MAX_ITEMS = 3
MAX_TRANSCRIPT_CHARS = 12_000

# Signals the judge gets to ground its reading; lists and internals are left out.
_JUDGE_SIGNALS = (
    "duration_s", "word_count", "time_to_first_word_s", "first_word", "speech_at_start", "speech_at_end",
    "words_per_minute", "speech_ratio", "pause_count", "longest_pause", "cuts_per_minute", "cuts_first_3s",
    "loudness_lufs",
)


def _trim(items: list[str]) -> list[str]:
    return [str(i).strip() for i in items if str(i).strip()][:MAX_ITEMS]


class JudgedDimension(BaseModel):
    evidence: list[str] = Field(default_factory=list)
    score: int
    fix: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"

    @field_validator("score", mode="before")
    @classmethod
    def _clamp(cls, v):
        return max(0, min(5, int(round(float(v)))))

    @field_validator("confidence", mode="before")
    @classmethod
    def _lower(cls, v):
        return str(v).strip().lower()

    @field_validator("evidence")
    @classmethod
    def _cap(cls, v):
        return _trim(v)


class CompletenessJudgement(JudgedDimension):
    starts_mid_thought: bool = False
    ends_mid_thought: bool = False


class ShareableLine(BaseModel):
    t: float
    text: str
    note: str | None = None


class Potential(BaseModel):
    helps: list[str] = Field(default_factory=list)
    holds_back: list[str] = Field(default_factory=list)
    shareable_line: ShareableLine | None = None

    @field_validator("helps", "holds_back")
    @classmethod
    def _cap(cls, v):
        return _trim(v)


class JudgeOutput(BaseModel):
    hook: JudgedDimension
    standalone_completeness: CompletenessJudgement
    clarity_payoff: JudgedDimension
    on_screen_text: JudgedDimension
    summary: str
    point: str
    potential: Potential = Field(default_factory=Potential)


@dataclass
class JudgeResult:
    output: JudgeOutput
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def parse_judge_output(raw: str) -> JudgeOutput:
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ModelOutputError(f"Model did not return JSON: {raw[:200]!r}")
    try:
        return JudgeOutput.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ModelOutputError(f"Model returned JSON that doesn't match the schema: {exc}") from exc


def build_messages(
    *, segments: list[dict], signals: dict, frames: list[tuple[float, Path]], metadata: dict
) -> list[dict]:
    transcript = "\n".join(
        f"[{fmt_ts(s['start'])}–{fmt_ts(s['end'])} | {s['start']:.1f}s] {s['text']}" for s in segments
    )[:MAX_TRANSCRIPT_CHARS] or "(no speech detected)"
    grounded = {k: signals.get(k) for k in _JUDGE_SIGNALS}

    header = (
        "CLIP\n"
        f"title (written by the team, context only): {metadata.get('title') or '(none)'}\n"
        f"platform: {metadata.get('platform') or 'unknown'}\n\n"
        "MEASURED SIGNALS (deterministic tools; treat as facts)\n"
        f"{json.dumps(grounded, indent=1)}\n\n"
        "TRANSCRIPT\n"
        f"{transcript}\n\n"
    )
    content: list[dict] = []
    if frames:
        header += "FRAMES\nThe images below are frames sampled at: " + ", ".join(f"{t:.1f}s" for t, _ in frames)
        content.append({"type": "text", "text": header})
        for _, path in frames:
            b64 = base64.b64encode(path.read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    else:
        header += (
            "FRAMES\nNo frames are available in this run. For on_screen_text, set score 0, confidence low, "
            'and evidence ["Frames not analysed in this run."]; the service will mark it not applicable.'
        )
        content.append({"type": "text", "text": header})

    return [
        {"role": "system", "content": PROMPT_PATH.read_text()},
        {"role": "user", "content": content},
    ]


def run_judge(
    settings: Settings, *, segments: list[dict], signals: dict, frames: list[tuple[float, Path]], metadata: dict
) -> JudgeResult:
    completion = chat(settings, build_messages(segments=segments, signals=signals, frames=frames, metadata=metadata))
    return JudgeResult(
        output=parse_judge_output(completion.content),
        model=completion.model,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        cost_usd=completion.cost_usd,
    )
