"""Stage 3: extract signals with deterministic tools. No AI judgment happens here."""

import logging
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.pipeline.model import ExtractionError
from app.settings import Settings

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT_S = 300
HOOK_FRAME_TIMES = (0.2, 1.5, 2.8)  # dense in the first 3 s
LATER_FRAME_FRACTIONS = (0.35, 0.65, 0.9)  # sparse after


@dataclass
class Extraction:
    segments: list[dict] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)
    cuts: list[float] = field(default_factory=list)
    loudness: dict = field(default_factory=dict)
    silences: list[dict] = field(default_factory=list)
    frames: list[tuple[float, Path]] = field(default_factory=list)


def extract_all(video: Path, duration_s: float, has_audio: bool, workdir: Path, settings: Settings) -> Extraction:
    workdir.mkdir(parents=True, exist_ok=True)
    out = Extraction()
    if has_audio:
        wav = extract_audio(video, workdir / "audio.wav")
        out.segments, out.words = transcribe(wav, settings)
        out.loudness = measure_loudness(video)
        out.silences = detect_silences(video)
    out.cuts = detect_cuts(video)
    if settings.llm_vision:
        out.frames = sample_frames(video, duration_s, workdir)
    return out


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise ExtractionError(f"{cmd[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        raise ExtractionError(f"{cmd[0]} exited with {proc.returncode}: {proc.stderr.strip()[-300:]}")
    return proc


def extract_audio(video: Path, wav: Path) -> Path:
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    return wav


@lru_cache
def _whisper(model: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model, device="cpu", compute_type=compute_type)


def transcribe(wav: Path, settings: Settings) -> tuple[list[dict], list[dict]]:
    try:
        model = _whisper(settings.whisper_model, settings.whisper_compute_type)
        segments_iter, _ = model.transcribe(
            str(wav),
            language="en",
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
        )
        segments, words = [], []
        for seg in segments_iter:
            seg_words = [
                {"word": w.word, "start": float(w.start), "end": float(w.end), "probability": float(w.probability)}
                for w in (seg.words or [])
            ]
            segments.append(
                {
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": seg.text.strip(),
                    "avg_logprob": float(seg.avg_logprob),
                }
            )
            words.extend(seg_words)
    except Exception as exc:  # faster-whisper raises a variety of runtime errors
        raise ExtractionError(f"Transcription failed: {exc}") from exc
    return segments, words


_LUFS = re.compile(r"I:\s+(-?[\d.]+) LUFS")
_PEAK = re.compile(r"Peak:\s+(-?[\d.]+|-inf) dBFS")


def measure_loudness(video: Path) -> dict:
    proc = _run(["ffmpeg", "-nostdin", "-nostats", "-i", str(video), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    lufs = _LUFS.findall(proc.stderr)
    peaks = _PEAK.findall(proc.stderr)
    # The summary block is printed last, so take the last match.
    return {
        "integrated_lufs": float(lufs[-1]) if lufs else None,
        "true_peak_dbtp": float(peaks[-1]) if peaks and peaks[-1] != "-inf" else None,
    }


_SIL_END = re.compile(r"silence_end: (-?[\d.]+) \| silence_duration: ([\d.]+)")


def detect_silences(video: Path) -> list[dict]:
    proc = _run(
        ["ffmpeg", "-nostdin", "-nostats", "-i", str(video), "-vn", "-af", "silencedetect=noise=-35dB:d=1", "-f", "null", "-"]
    )
    return [{"t": round(float(end) - float(dur), 2), "duration_s": float(dur)} for end, dur in _SIL_END.findall(proc.stderr)]


def detect_cuts(video: Path) -> list[float]:
    try:
        from scenedetect import AdaptiveDetector, detect

        scenes = detect(str(video), AdaptiveDetector())
    except Exception as exc:
        raise ExtractionError(f"Cut detection failed: {exc}") from exc
    return [start.get_seconds() for start, _ in scenes[1:]]


def sample_frames(video: Path, duration_s: float, workdir: Path) -> list[tuple[float, Path]]:
    times = [t for t in HOOK_FRAME_TIMES if t < duration_s]
    times += [round(duration_s * f, 2) for f in LATER_FRAME_FRACTIONS if duration_s * f > 3.0]
    frames = []
    for i, t in enumerate(times):
        out = workdir / f"frame_{i:02d}.jpg"
        _run(
            ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4", str(out)]
        )
        if out.exists():
            frames.append((t, out))
    return frames
