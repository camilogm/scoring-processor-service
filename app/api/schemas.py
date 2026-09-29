"""Response models: the API contract (docs/report-example.json) as types.

Used as `response_model`, so Swagger documents the real shape and FastAPI validates it at runtime.
Field order is the contract's key order.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["queued", "processing", "completed", "failed"]
Confidence = Literal["high", "medium", "low"]


class InputFacts(BaseModel):
    filename: str | None = Field(description="Uploaded file name.")
    sha256: str = Field(description="SHA-256 of the file bytes.")
    size_bytes: int
    duration_s: float
    width: int
    height: int
    fps: float


class Metadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = None
    account: str | None = None
    platform: str | None = Field(None, description="For example `tiktok` or `instagram`.")
    external_id: str | None = Field(None, description="Caller's own reference, returned unchanged.")


class Overall(BaseModel):
    score: float = Field(description="0–10, one decimal, after the cap rule.")
    raw_score: float = Field(description="Weighted average before rounding and capping.")
    verdict: Literal["post", "improve", "skip"]
    verdict_label: str
    capped: bool = Field(description="True when the mid-sentence cap (6.0) applied.")
    cap_reason: str | None
    summary: str
    point: str = Field(description="The clip's point in one sentence.")


class ShareableLine(BaseModel):
    t: float = Field(description="Start time in seconds.")
    text: str
    note: str | None = None


class Potential(BaseModel):
    helps: list[str]
    holds_back: list[str]
    shareable_line: ShareableLine | None
    caveat: str


class PriorityFix(BaseModel):
    rank: int
    dimension: str
    label: str
    fix: str
    weighted_gap: float = Field(description="weight × (10 − score): the most this fix could recover.")


class Dimension(BaseModel):
    id: str
    label: str
    score: int | None = Field(description="0–10; null when not applicable.")
    weight: float = Field(description="Effective weight after rescaling non-applicable dimensions.")
    weighted_gap: float
    confidence: Confidence
    basis: Literal["measured", "judged", "mixed"]
    applicable: bool
    evidence: list[str]
    fix: str | None


class Pause(BaseModel):
    t: float
    duration_s: float


class Signals(BaseModel):
    """Deterministic measurements only, never model output."""

    time_to_first_word_s: float | None
    words_per_minute: float
    speech_ratio: float
    longest_pause: Pause | None
    pauses_over_1s: int
    cuts: int
    cuts_per_minute: float
    integrated_loudness_lufs: float | None
    clipping: bool
    transcript_confidence: float | None = Field(description="Mean Whisper word probability, 0–1.")


class Contradiction(BaseModel):
    rule: str
    dimension: str
    message: str


class Verification(BaseModel):
    checks_run: int
    contradictions: list[Contradiction]


class Provenance(BaseModel):
    model: str
    prompt_version: str
    rubric_version: str
    pipeline_version: str
    judge_calls: int
    cost_usd: float
    duration_ms: int


class ErrorDetail(BaseModel):
    code: str = Field(examples=["model_error"])
    message: str


class Analysis(BaseModel):
    """One analysis. Fields from `mode` to `provenance` are present only when `status` is `completed`."""

    id: str = Field(examples=["an_01a0e694f5dd29ac687b3b70"])
    status: Status
    current_step: Literal["extract", "judge", "verify", "score"] | None = Field(
        description="Pipeline stage while `processing`, otherwise null."
    )
    source: Literal["fresh", "cached"] | None = Field(description="Set when completed.")
    deduplicated: bool = Field(description="True when a POST matched an existing analysis of the same clip.")
    created_at: str
    finished_at: str | None
    input: InputFacts
    metadata: Metadata
    mode: Literal["speech", "low_speech"] | None = None
    overall: Overall | None = None
    potential: Potential | None = None
    priority_fixes: list[PriorityFix] | None = None
    dimensions: list[Dimension] | None = None
    signals: Signals | None = None
    verification: Verification | None = None
    provenance: Provenance | None = None
    error: ErrorDetail | None = Field(description="Set only when `status` is `failed`.")


class AnalysisList(BaseModel):
    items: list[Analysis] = Field(description="Most recent first.")


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Health(BaseModel):
    status: Literal["ok"]


def error_example(status: int, code: str, message: str, description: str) -> dict:
    return {
        status: {
            "model": ErrorResponse,
            "description": description,
            "content": {"application/json": {"example": {"error": {"code": code, "message": message}}}},
        }
    }
