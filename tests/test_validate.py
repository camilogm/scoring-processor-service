import json
import subprocess

import pytest

from app.pipeline import validate


def _ffprobe(monkeypatch, video_stream):
    out = {"format": {"duration": "12.0"}, "streams": [{"codec_type": "video", "avg_frame_rate": "30/1", **video_stream}]}
    monkeypatch.setattr(
        validate.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(out), "")
    )


def test_probe_reports_stored_dimensions(monkeypatch, tmp_path):
    _ffprobe(monkeypatch, {"width": 1080, "height": 1920})

    info = validate.probe(tmp_path / "c.mp4")

    assert (info.width, info.height) == (1080, 1920)


@pytest.mark.parametrize("stream", [
    {"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]},
    {"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 90}]},
    {"tags": {"rotate": "270"}},
])
def test_probe_reports_displayed_dimensions_for_rotated_phone_video(monkeypatch, tmp_path, stream):
    # Phones often store vertical video as 1920x1080 plus a rotation flag; players show it 1080x1920.
    _ffprobe(monkeypatch, {"width": 1920, "height": 1080, **stream})

    info = validate.probe(tmp_path / "c.mp4")

    assert (info.width, info.height) == (1080, 1920)


def test_probe_keeps_dimensions_for_upside_down_video(monkeypatch, tmp_path):
    _ffprobe(monkeypatch, {"width": 1080, "height": 1920, "side_data_list": [{"rotation": 180}]})

    info = validate.probe(tmp_path / "c.mp4")

    assert (info.width, info.height) == (1080, 1920)
