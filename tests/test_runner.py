import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm import client as llm_client
from app.main import create_app
from app.pipeline import runner as runner_mod
from app.pipeline import validate
from app.pipeline.extract import Extraction
from app.pipeline.runner import Runner
from app.pipeline.validate import VideoInfo
from app.settings import Settings

FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _words(text, start, step=0.3):
    return [{"word": " " + w, "start": start + i * step, "end": start + i * step + 0.25, "probability": 0.9}
            for i, w in enumerate(text.split())]


JUDGE_JSON = {
    "hook": {"evidence": ["0:00 opens mid-thought"], "score": 4, "fix": "Start at 0:06.", "confidence": "high"},
    "standalone_completeness": {"evidence": ["Opens with 'And'"], "score": 5, "fix": "Trim the opening.", "confidence": "high",
                     "starts_mid_thought": True, "ends_mid_thought": False},
    "clarity_payoff": {"evidence": ["Point stated at 0:03"], "score": 4, "fix": None, "confidence": "medium"},
    "on_screen_text": {"evidence": ["Captions in every frame"], "score": 4, "fix": None, "confidence": "medium"},
    "summary": "Clear point, but it starts mid-sentence.",
    "point": "Permits take too long.",
    "potential": {"helps": ["Concrete number"], "holds_back": ["Cold open"],
                  "shareable_line": {"t": 3.0, "text": "Fourteen months.", "note": "Repeatable"}},
}


@pytest.fixture
def app_settings(tmp_path, database_url):
    return Settings(
        data_dir=tmp_path, database_url=database_url, run_worker=False, _env_file=None, llm_model="test-model",
        auto_run_migrations=True,
    )


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    monkeypatch.setattr(validate, "probe", lambda p: VideoInfo(20.0, 1080, 1920, 30.0, True))
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8\xff")
    words = _words("And that is why permits take fourteen months to approve here", 0.0) + \
        _words("which is far too long for anyone", 12.0)
    extraction = Extraction(
        segments=[{"start": 0.0, "end": 19.9, "text": "And that is why ...", "avg_logprob": -0.3}],
        words=words,
        cuts=[2.0, 9.0],
        loudness={"integrated_lufs": -15.0, "true_peak_dbtp": -1.2},
        silences=[],
        frames=[(0.2, frame)],
    )
    monkeypatch.setattr(runner_mod, "extract_all", lambda *a, **k: extraction)
    calls = []

    def fake_chat(settings, messages):
        calls.append(messages)
        return llm_client.Completion(json.dumps(JUDGE_JSON), "test-model", 1200, 300, 0.0021)

    monkeypatch.setattr("app.pipeline.judge.chat", fake_chat)
    return calls


def test_full_pipeline_produces_documented_contract(app_settings, stubs):
    with TestClient(create_app(app_settings)) as c:
        analysis_id = c.post("/analyses", files={"file": ("c.mp4", FAKE_MP4, "video/mp4")},
                             data={"metadata": json.dumps({"title": "Permits", "external_id": "C07"})}).json()["id"]
        state = c.app.state
        Runner(state.settings, state.store, state.rubric).run(analysis_id)
        body = c.get(f"/analyses/{analysis_id}").json()
        report = c.get(f"/analyses/{analysis_id}/report").text
        again = c.post("/analyses", files={"file": ("c.mp4", FAKE_MP4, "video/mp4")},
                       data={"metadata": json.dumps({"title": "Permits"})}).json()

    assert body["status"] == "completed", body["error"]
    assert body["source"] == "fresh"
    assert [d["id"] for d in body["dimensions"]] == ["hook", "standalone_completeness", "clarity_payoff", "pacing_energy", "audio_quality", "on_screen_text"]
    # Speech at 0:00 + "And" + judge agrees → cap rule and completeness capped by the verifier.
    assert body["overall"]["capped"] is True
    assert body["overall"]["score"] <= 6.0
    assert body["overall"]["verdict"] in ("improve", "skip")
    completeness = next(d for d in body["dimensions"] if d["id"] == "standalone_completeness")
    assert completeness["score"] == 4
    assert body["priority_fixes"][0]["rank"] == 1
    assert body["provenance"]["cost_usd"] == 0.0021
    # The judge said 3.0 s; the quote starts at 1.8 s in the word-level transcript.
    assert body["potential"]["shareable_line"]["t"] == 1.8
    assert body["metadata"]["external_id"] == "C07"
    assert "Capped at 6.0" in report
    # Same clip afterwards comes back from the store, no second model call.
    assert again["deduplicated"] is True and again["source"] == "cached"
    assert len(stubs) == 1


def test_invalid_model_output_fails_with_reason(app_settings, stubs, monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.judge.chat", lambda s, m: llm_client.Completion("not json", "test-model", 1, 1, 0.0)
    )
    with TestClient(create_app(app_settings)) as c:
        analysis_id = c.post("/analyses", files={"file": ("c.mp4", FAKE_MP4, "video/mp4")}).json()["id"]
        state = c.app.state
        Runner(state.settings, state.store, state.rubric).run(analysis_id)
        body = c.get(f"/analyses/{analysis_id}").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "model_invalid_output"


CONTRACT = json.loads((Path(__file__).parents[1] / "docs" / "report-example.json").read_text())


def _assert_same_shape(actual, expected, path="$"):
    """Same keys, in the same order, all the way down. Values are free; lists compare their first item."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, got {actual!r}"
        assert list(actual) == list(expected), f"{path}: keys {list(actual)} != contract {list(expected)}"
        for key, value in expected.items():
            if value is not None and actual[key] is not None:
                _assert_same_shape(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list) and expected and actual:
        _assert_same_shape(actual[0], expected[0], f"{path}[0]")


def test_completed_response_matches_documented_contract(app_settings, stubs):
    with TestClient(create_app(app_settings)) as c:
        analysis_id = c.post("/analyses", files={"file": ("c.mp4", FAKE_MP4, "video/mp4")},
                             data={"metadata": json.dumps(CONTRACT["metadata"])}).json()["id"]
        state = c.app.state
        Runner(state.settings, state.store, state.rubric).run(analysis_id)
        body = c.get(f"/analyses/{analysis_id}").json()

    _assert_same_shape(body, CONTRACT)
    assert [d["id"] for d in body["dimensions"]] == [d["id"] for d in CONTRACT["dimensions"]]
    assert body["deduplicated"] is False
    assert body["created_at"].endswith("Z")
    assert body["potential"]["caveat"] == CONTRACT["potential"]["caveat"]
    assert body["provenance"]["prompt_version"] == "v2"
    assert body["provenance"]["pipeline_version"] == "0.3.1"
    assert body["provenance"]["vision"] is True
    assert body["provenance"]["max_frames"] == 16
    assert isinstance(body["signals"]["cuts"], int)
    assert isinstance(body["signals"]["clipping"], bool)
    assert 0 <= body["signals"]["transcript_confidence"] <= 1


def test_shareable_line_missing_from_transcript_is_dropped(app_settings, stubs, monkeypatch):
    invented = {**JUDGE_JSON, "potential": {**JUDGE_JSON["potential"],
                                            "shareable_line": {"t": 0.23, "text": "Nobody said this at all.", "note": "x"}}}
    monkeypatch.setattr("app.pipeline.judge.chat", lambda s, m: llm_client.Completion(
        json.dumps(invented), "test-model", 1, 1, 0.0))
    with TestClient(create_app(app_settings)) as c:
        analysis_id = c.post("/analyses", files={"file": ("c.mp4", FAKE_MP4, "video/mp4")}).json()["id"]
        state = c.app.state
        Runner(state.settings, state.store, state.rubric).run(analysis_id)
        body = c.get(f"/analyses/{analysis_id}").json()

    assert body["status"] == "completed", body["error"]
    assert body["potential"]["shareable_line"] is None


def test_judge_uses_the_model_chosen_for_the_analysis(app_settings, stubs, monkeypatch):
    judged_with = []

    def fake_chat(settings, messages):
        judged_with.append(settings.llm_model)
        return llm_client.Completion(json.dumps(JUDGE_JSON), settings.llm_model, 1, 1, 0.0)

    monkeypatch.setattr("app.pipeline.judge.chat", fake_chat)
    choice = app_settings.model_copy(update={"llm_model_choices": "openai/gpt-5-nano"})
    with TestClient(create_app(choice)) as c:
        analysis_id = c.post("/analyses", files={"file": ("c.mp4", FAKE_MP4, "video/mp4")},
                             data={"model": "openai/gpt-5-nano"}).json()["id"]
        state = c.app.state
        Runner(state.settings, state.store, state.rubric).run(analysis_id)
        body = c.get(f"/analyses/{analysis_id}").json()

    assert judged_with == ["openai/gpt-5-nano"]
    assert body["provenance"]["model"] == "openai/gpt-5-nano"
