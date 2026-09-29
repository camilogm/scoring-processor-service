"""Stage 1: is this a readable MP4 with a video stream? Runs in the request, before anything is stored."""

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

PROBE_TIMEOUT_S = 30


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool


class ProbeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def probe(path: Path) -> VideoInfo:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise ProbeError("unreadable_video", f"Could not probe the file: {exc}") from exc
    if proc.returncode != 0:
        raise ProbeError("unreadable_video", f"The file is corrupt or not a readable MP4: {proc.stderr.strip()[:200]}")

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no_video_stream", "The file has no video stream.")

    duration = data.get("format", {}).get("duration") or video.get("duration")
    if not duration or float(duration) <= 0:
        raise ProbeError("unreadable_video", "Could not determine the video duration.")

    try:
        fps = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"))
    except (ZeroDivisionError, ValueError):
        fps = 0.0

    return VideoInfo(
        duration_s=float(duration),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=round(fps, 3),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )
