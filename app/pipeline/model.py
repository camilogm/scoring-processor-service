from dataclasses import dataclass, field

CONFIDENCE_LEVELS = ("low", "medium", "high")


def lower_confidence(confidence: str) -> str:
    return CONFIDENCE_LEVELS[max(0, CONFIDENCE_LEVELS.index(confidence) - 1)]


def fmt_ts(seconds: float) -> str:
    """Seconds to m:ss, the format evidence and fixes use."""
    seconds = max(0.0, seconds)
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


@dataclass
class DimensionDraft:
    """One dimension on its way through judge → verify → score. Scores are 0-10."""

    id: str
    label: str
    weight: float
    basis: str
    score: int | None
    applicable: bool = True
    confidence: str = "medium"
    evidence: list[str] = field(default_factory=list)
    fix: str | None = None


@dataclass
class Verification:
    checks_run: int = 0
    contradictions: list[dict] = field(default_factory=list)
    cap_reason: str | None = None


class PipelineError(Exception):
    """A failure with a stable, documented code stored on the analysis."""

    code = "internal_error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ExtractionError(PipelineError):
    code = "extraction_failed"


class ModelError(PipelineError):
    code = "model_error"


class ModelOutputError(PipelineError):
    code = "model_invalid_output"
