from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

RUBRIC_PATH = Path(__file__).parent / "rubric.yaml"


class DimensionSpec(BaseModel):
    id: str
    label: str
    weight: float
    basis: Literal["measured", "judged", "mixed"]


class VerdictSpec(BaseModel):
    min_score: float
    label: str


class CapSpec(BaseModel):
    max_score: float


class VerifySpec(BaseModel):
    late_start_s: float
    late_start_hook_ceiling: int
    boundary_completeness_ceiling: int
    evidence_tolerance_s: float
    quote_tolerance_s: float


class SpeechSpec(BaseModel):
    low_speech_ratio: float


class Rubric(BaseModel):
    version: str
    dimensions: list[DimensionSpec]
    verdicts: dict[Literal["post", "improve", "skip"], VerdictSpec]
    cap: CapSpec
    verify: VerifySpec
    speech: SpeechSpec

    @model_validator(mode="after")
    def _weights_add_up(self) -> "Rubric":
        total = sum(d.weight for d in self.dimensions)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"rubric weights must add up to 1.0, got {total}")
        return self


@lru_cache
def load_rubric(path: Path = RUBRIC_PATH) -> Rubric:
    return Rubric.model_validate(yaml.safe_load(path.read_text()))
