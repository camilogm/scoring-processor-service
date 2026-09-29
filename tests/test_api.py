import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import validate
from app.pipeline.validate import ProbeError, VideoInfo
from app.settings import Settings

FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


@pytest.fixture
def settings(tmp_path, database_url):
    return Settings(
        data_dir=tmp_path, database_url=database_url, run_worker=False, max_upload_mb=1, _env_file=None
    )


@pytest.fixture
def probe_ok(monkeypatch):
    info = VideoInfo(duration_s=42.0, width=1080, height=1920, fps=30.0, has_audio=True)
    monkeypatch.setattr(validate, "probe", lambda path: info)
    return info


@pytest.fixture
def client(settings, probe_ok):
    with TestClient(create_app(settings)) as c:
        yield c


def _post(client, content=FAKE_MP4, metadata=None, params=None, filename="clip.mp4"):
    data = {} if metadata is None else {"metadata": metadata}
    return client.post(
        "/analyses", files={"file": (filename, content, "video/mp4")}, data=data, params=params or {}
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_upload_is_stored_and_queued(client):
    res = _post(client, metadata=json.dumps({"title": "Permits", "external_id": "C07"}))

    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued"
    assert body["id"].startswith("an_")

    got = client.get(f"/analyses/{body['id']}").json()
    assert got["status"] == "queued"
    assert got["metadata"]["external_id"] == "C07"
    assert got["input"]["duration_s"] == 42.0
    assert got["error"] is None


def test_same_clip_is_deduplicated(client):
    first = _post(client, metadata=json.dumps({"title": "A", "external_id": "x1"})).json()
    # external_id is bookkeeping, not part of the cache key.
    second = _post(client, metadata=json.dumps({"title": "A", "external_id": "x2"}))

    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert second.json()["deduplicated"] is True


def test_fresh_forces_new_analysis(client):
    first = _post(client).json()
    fresh = _post(client, params={"fresh": "true"})

    assert fresh.status_code == 202
    assert fresh.json()["id"] != first["id"]


def test_failed_analysis_is_not_deduplicated(client):
    first = _post(client).json()
    client.app.state.store.fail(first["id"], "model_error", "boom")

    again = _post(client)

    assert again.status_code == 202
    assert again.json()["id"] != first["id"]


def test_not_an_mp4_is_rejected(client):
    res = _post(client, content=b"GIF89a not a video at all", filename="clip.gif")

    assert res.status_code == 415
    assert res.json()["error"]["code"] == "unsupported_media_type"


def test_malformed_metadata_is_rejected(client):
    res = _post(client, metadata="{not json")

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_metadata"


def test_missing_file_is_rejected(client):
    res = client.post("/analyses", data={"metadata": "{}"})

    assert res.status_code == 400


def test_file_too_large(client):
    res = _post(client, content=FAKE_MP4 + b"\x00" * (1024 * 1024 + 1))

    assert res.status_code == 413


def test_too_long_video(client, monkeypatch):
    monkeypatch.setattr(
        validate, "probe", lambda p: VideoInfo(duration_s=600.0, width=1, height=1, fps=30.0, has_audio=True)
    )

    res = _post(client)

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "too_long"


def test_corrupt_video(client, monkeypatch):
    def _raise(path):
        raise ProbeError("unreadable_video", "ffprobe could not read the file")

    monkeypatch.setattr(validate, "probe", _raise)

    res = _post(client)

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "unreadable_video"


def test_unknown_id_is_404(client):
    res = client.get("/analyses/an_nope")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_report_renders_status_page_while_queued(client):
    analysis_id = _post(client).json()["id"]

    res = client.get(f"/analyses/{analysis_id}/report")

    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "queued" in res.text


def test_restart_marks_unfinished_runs_as_interrupted(settings, probe_ok):
    with TestClient(create_app(settings)) as c:
        analysis_id = _post(c).json()["id"]

    # Simulates a crash + restart: new app, same database.
    with TestClient(create_app(settings)) as c:
        body = c.get(f"/analyses/{analysis_id}").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "interrupted_by_restart"
