"""Runs one analysis end to end: extract → judge → verify → score. Every step is committed before the next."""

import logging
import shutil
import time
from pathlib import Path

from app.config.rubric import Rubric
from app.pipeline.extract import extract_all
from app.pipeline.judge import PROMPT_VERSION, JudgeOutput, run_judge
from app.pipeline.measured import score_audio, score_pacing
from app.pipeline.model import DimensionDraft, PipelineError
from app.pipeline.score import compute_scores
from app.pipeline.signals import compute_signals, public_signals
from app.pipeline.verify import BoundaryFlags, locate_quote, verify
from app.settings import PIPELINE_VERSION, Settings
from app.storage.db import Store

log = logging.getLogger(__name__)

SPEECH_DIMENSIONS = ("hook", "standalone_completeness", "clarity_payoff", "pacing_energy", "audio_quality")
POTENTIAL_CAVEAT = (
    "Signals commonly linked to watching and sharing, read from this clip alone. Not a forecast of views."
)


class Runner:
    def __init__(self, settings: Settings, store: Store, rubric: Rubric):
        self.settings = settings
        self.store = store
        self.rubric = rubric

    def run(self, analysis_id: str) -> None:
        row = self.store.get(analysis_id)
        if row is None or row["status"] != "queued":
            return
        self.store.mark_processing(analysis_id, step="extract")
        started = time.monotonic()
        workdir = self.settings.work_dir / analysis_id
        try:
            result, cost = self._pipeline(analysis_id, row, workdir, started)
            self.store.complete(analysis_id, result, cost_usd=cost, duration_ms=result["provenance"]["duration_ms"])
            log.info("analysis %s completed: %s", analysis_id, result["overall"]["verdict"])
        except PipelineError as exc:
            log.warning("analysis %s failed: %s %s", analysis_id, exc.code, exc.message)
            self.store.fail(analysis_id, exc.code, exc.message)
        except Exception as exc:
            log.exception("analysis %s crashed", analysis_id)
            self.store.fail(analysis_id, "internal_error", f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _pipeline(self, analysis_id: str, row: dict, workdir: Path, started: float) -> tuple[dict, float]:
        inp = row["input_json"]
        metadata = row["metadata_json"]
        duration = inp["duration_s"]

        extraction = extract_all(Path(row["file_path"]), duration, inp["has_audio"], workdir, self.settings)
        signals = compute_signals(
            duration_s=duration,
            words=extraction.words,
            segments=extraction.segments,
            cuts=extraction.cuts,
            loudness=extraction.loudness,
            silences=extraction.silences,
            has_audio=inp["has_audio"],
            frames_sampled=[t for t, _ in extraction.frames],
        )
        low_speech = signals["speech_ratio"] < self.rubric.speech.low_speech_ratio

        self.store.set_step(analysis_id, "judge")
        # The model is chosen per upload (LLM_MODEL_CHOICES) and recorded on the row.
        judged = run_judge(
            self.settings.model_copy(update={"llm_model": row["model_id"]}),
            segments=extraction.segments,
            words=extraction.words,
            signals=signals,
            frames=extraction.frames,
            metadata=metadata,
        )

        self.store.set_step(analysis_id, "verify")
        drafts = self._drafts(judged.output, signals, frames_available=bool(extraction.frames))
        c = judged.output.standalone_completeness
        verification = verify(
            drafts, signals, BoundaryFlags(c.starts_mid_thought, c.ends_mid_thought), self.rubric, extraction.words
        )
        if low_speech:
            for dim_id in SPEECH_DIMENSIONS:
                drafts[dim_id].confidence = "low"

        self.store.set_step(analysis_id, "score")
        scored = compute_scores(list(drafts.values()), self.rubric, verification.cap_reason)

        potential = judged.output.potential.model_dump()
        line = potential.get("shareable_line")
        if line:
            # The time comes from the transcript, never the model (it once wrote 0:23 as 0.23).
            said_at = locate_quote(line["text"], extraction.words, min_words=1, near=line["t"])
            if said_at is None:
                potential["shareable_line"] = None  # a quote that isn't in the clip can't be trusted
            else:
                line["t"] = said_at
        potential["caveat"] = POTENTIAL_CAVEAT

        result = {
            "mode": "low_speech" if low_speech else "speech",
            "overall": {**scored["overall"], "summary": judged.output.summary, "point": judged.output.point},
            "potential": potential,
            "priority_fixes": scored["priority_fixes"],
            "dimensions": scored["dimensions"],
            "signals": public_signals(signals),
            "verification": {
                "checks_run": verification.checks_run,
                "contradictions": verification.contradictions,
            },
            "provenance": {
                "model": judged.model,
                "prompt_version": PROMPT_VERSION,
                "rubric_version": self.rubric.version,
                "pipeline_version": PIPELINE_VERSION,
                "vision": self.settings.llm_vision,
                "max_frames": self.settings.llm_max_frames,
                "judge_calls": 1,
                "cost_usd": judged.cost_usd,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        }
        return result, judged.cost_usd

    def _drafts(self, judged: JudgeOutput, signals: dict, frames_available: bool) -> dict[str, DimensionDraft]:
        measured = {"pacing_energy": score_pacing(signals), "audio_quality": score_audio(signals)}
        drafts = {}
        for spec in self.rubric.dimensions:
            base = dict(id=spec.id, label=spec.label, weight=spec.weight, basis=spec.basis)
            if spec.id in measured:
                m = measured[spec.id]
                drafts[spec.id] = DimensionDraft(
                    **base, score=m.score, applicable=m.applicable, confidence=m.confidence,
                    evidence=list(m.evidence), fix=m.fix,
                )
                continue
            j = getattr(judged, spec.id)
            applicable = frames_available or spec.id != "on_screen_text"
            drafts[spec.id] = DimensionDraft(
                **base,
                score=j.score * 2 if applicable else None,  # judges score 0-5; code maps to 0-10
                applicable=applicable,
                confidence=j.confidence if applicable else "low",
                evidence=list(j.evidence) if applicable else ["Frames not analysed in this run (text-only model)."],
                fix=j.fix if applicable else None,
            )
        return drafts
