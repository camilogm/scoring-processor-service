import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import create_app
from app.pipeline import validate
from app.pipeline.validate import ProbeError, VideoInfo
from app.settings import Settings

FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


@pytest.fixture
def settings(tmp_path, database_url):
    return Settings(
        data_dir=tmp_path, database_url=database_url, run_worker=False, max_upload_mb=1, auto_run_migrations=True,
        _env_file=None,
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


def _post(client, content=FAKE_MP4, metadata=None, params=None, filename="clip.mp4", data=None):
    data = dict(data or {})
    if metadata is not None:
        data["metadata"] = metadata
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


def test_upload_is_fsynced_off_the_event_loop(client, monkeypatch):
    # A blocking fsync on the loop thread freezes every other request (even /health) while a
    # large upload is flushed to disk, so it has to run in a worker thread.
    calls = []
    real_fsync = os.fsync

    def spy(fd):
        try:
            asyncio.get_running_loop()
            calls.append("event-loop")
        except RuntimeError:
            calls.append("worker-thread")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)

    assert _post(client).status_code == 202
    assert calls == ["worker-thread"]


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


def test_list_returns_recent_analyses_newest_first(client):
    first = _post(client, filename="a.mp4").json()["id"]
    second = _post(client, content=FAKE_MP4 + b"\x01", filename="b.mp4").json()["id"]

    res = client.get("/analyses")

    assert res.status_code == 200
    items = res.json()["items"]
    assert [a["id"] for a in items] == [second, first]
    assert items[0]["status"] == "queued"
    assert items[0]["input"]["filename"] == "b.mp4"


def test_list_respects_limit(client):
    for i in range(3):
        _post(client, content=FAKE_MP4 + bytes([i]))

    assert len(client.get("/analyses", params={"limit": 2}).json()["items"]) == 2


def test_list_is_empty_without_analyses(client):
    assert client.get("/analyses").json() == {"items": []}


def test_demo_page_uploads_lists_and_links_reports(client):
    res = client.get("/demo")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    html = res.text
    # The page is a thin client over the public API, nothing more.
    assert 'type="file"' in html
    assert "/analyses" in html
    assert "/report" in html


@pytest.fixture
def choice_client(settings, probe_ok):
    """Model choice enabled: the default model plus two alternatives."""
    enabled = settings.model_copy(update={"llm_model": "google/gemini-2.5-flash",
                                          "llm_model_choices": "openai/gpt-5-nano, anthropic/claude-sonnet-5.5"})
    with TestClient(create_app(enabled)) as c:
        yield c


def test_chosen_model_from_the_allowlist_is_stored_for_the_analysis(choice_client):
    res = _post(choice_client, data={"model": "openai/gpt-5-nano"})

    assert res.status_code == 202
    assert choice_client.app.state.store.get(res.json()["id"])["model_id"] == "openai/gpt-5-nano"


def test_without_a_choice_the_default_model_is_used(choice_client):
    res = _post(choice_client)

    assert choice_client.app.state.store.get(res.json()["id"])["model_id"] == "google/gemini-2.5-flash"


def test_model_outside_the_allowlist_is_rejected(choice_client):
    res = _post(choice_client, data={"model": "openai/gpt-5.5-pro"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "model_not_allowed"


def test_model_choice_is_off_by_default(client):
    res = _post(client, data={"model": "openai/gpt-5-nano"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "model_not_allowed"
    assert "LLM_MODEL_CHOICES" in res.json()["error"]["message"]


def test_the_default_model_can_always_be_named(client, settings):
    assert _post(client, data={"model": settings.llm_model}).status_code == 202


def test_each_model_gets_its_own_analysis_of_the_same_clip(choice_client):
    nano = _post(choice_client, data={"model": "openai/gpt-5-nano"}).json()
    sonnet = _post(choice_client, data={"model": "anthropic/claude-sonnet-5.5"}).json()
    nano_again = _post(choice_client, data={"model": "openai/gpt-5-nano"})

    assert nano["id"] != sonnet["id"]
    assert nano_again.status_code == 200
    assert nano_again.json()["id"] == nano["id"]


def test_demo_page_offers_the_model_choices(choice_client):
    html = choice_client.get("/demo").text

    assert 'name="model"' in html
    assert '<option value="google/gemini-2.5-flash" selected>' in html
    assert '<option value="anthropic/claude-sonnet-5.5">' in html


def test_demo_page_hides_the_model_picker_when_choice_is_off(client):
    assert 'name="model"' not in client.get("/demo").text


BASE_SETTINGS = Settings(_env_file=None)


def _key(settings=BASE_SETTINGS, metadata=None):
    return routes._cache_key("sha", metadata or {"title": "A"}, settings, "v2")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_model", "other-model"),
        ("llm_max_frames", 6),
        ("llm_vision", False),
        ("llm_seed", 8),
        ("llm_json_mode", False),
        ("whisper_model", "small.en"),
        ("whisper_compute_type", "float32"),
    ],
)
def test_cache_key_changes_with_result_settings(field, value):
    assert _key(BASE_SETTINGS.model_copy(update={field: value})) != _key()


def test_cache_key_changes_with_pipeline_version(monkeypatch):
    before = _key()
    monkeypatch.setattr(routes, "PIPELINE_VERSION", "9.9.9")

    assert _key() != before


def test_cache_key_ignores_unrelated_settings_and_bookkeeping():
    unrelated = BASE_SETTINGS.model_copy(
        update={"llm_base_url": "http://host.docker.internal:11434/v1", "llm_api_key": "secret", "llm_timeout_s": 1.0}
    )

    assert _key(unrelated) == _key()
    assert _key(metadata={"title": "A", "external_id": "x"}) == _key()
