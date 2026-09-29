"""Tiny HTTP helper shared by the evaluation scripts."""

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:9500"


def submit(video: Path, metadata: dict, fresh: bool = False, base: str = BASE) -> dict:
    with video.open("rb") as f:
        res = httpx.post(
            f"{base}/analyses",
            params={"fresh": str(fresh).lower()},
            files={"file": (video.name, f, "video/mp4")},
            data={"metadata": json.dumps(metadata)},
            timeout=120,
        )
    res.raise_for_status()
    return res.json()


def wait(analysis_id: str, base: str = BASE, timeout_s: float = 900) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = httpx.get(f"{base}/analyses/{analysis_id}", timeout=30).json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(2)
    raise TimeoutError(analysis_id)
