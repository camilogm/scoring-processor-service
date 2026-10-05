"""Tiny HTTP helper shared by the evaluation scripts.

Targets a local server by default. For another one (e.g. the deployment behind Basic auth):
  CLIP_API_URL=https://clip-scoring.fly.dev CLIP_API_AUTH=user:password uv run python scripts/...
"""

import json
import os
import time
from pathlib import Path

import httpx

BASE = os.environ.get("CLIP_API_URL", "http://127.0.0.1:9500").rstrip("/")
_user, _, _password = os.environ.get("CLIP_API_AUTH", "").partition(":")
AUTH = (_user, _password) if _user else None


def submit(video: Path, metadata: dict, fresh: bool = False, base: str = BASE) -> dict:
    with video.open("rb") as f:
        res = httpx.post(
            f"{base}/analyses",
            params={"fresh": str(fresh).lower()},
            files={"file": (video.name, f, "video/mp4")},
            data={"metadata": json.dumps(metadata)},
            auth=AUTH,
            timeout=120,
        )
    res.raise_for_status()
    return res.json()


def wait(analysis_id: str, base: str = BASE, timeout_s: float = 900) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            res = httpx.get(f"{base}/analyses/{analysis_id}", auth=AUTH, timeout=30)
            res.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # A machine that stops and restarts (Fly) drops connections or answers 5xx for a moment.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            time.sleep(5)
            continue
        body = res.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(2)
    raise TimeoutError(analysis_id)
