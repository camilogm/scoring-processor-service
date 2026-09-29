import pytest

from app.pipeline.measured import score_audio, score_pacing
from app.pipeline.signals import compute_signals


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "probability": 0.9}


def _signals(words, duration=10.0, **overrides):
    kwargs = dict(
        duration_s=duration,
        words=words,
        segments=[{"start": 0, "end": duration, "text": "", "avg_logprob": -0.3}],
        cuts=[1.0, 5.0],
        loudness={"integrated_lufs": -14.0, "true_peak_dbtp": -1.0},
        silences=[],
        has_audio=True,
        frames_sampled=[],
    )
    kwargs.update(overrides)
    return compute_signals(**kwargs)


def test_boundary_and_pause_signals():
    words = [_w(" And", 0.0, 0.3), _w(" so", 0.35, 0.5), _w(" then", 2.0, 2.4), _w(" done.", 2.5, 9.9)]

    s = _signals(words)

    assert s["time_to_first_word_s"] == 0.0
    assert s["first_word"] == "And"
    assert s["first_word_is_conjunction"] is True
    assert s["speech_at_start"] is True
    assert s["speech_at_end"] is True
    assert s["pause_count"] == 1
    assert s["longest_pause"] == {"t": 0.5, "duration_s": 1.5}
    assert s["cuts_first_3s"] == 1
    assert s["cuts_per_minute"] == pytest.approx(12.0)


def test_no_words_gives_empty_speech_signals():
    s = _signals([])

    assert s["word_count"] == 0
    assert s["time_to_first_word_s"] is None
    assert s["speech_ratio"] == 0.0
    assert s["speech_at_start"] is False


def test_pacing_not_applicable_without_speech():
    result = score_pacing(_signals([]))

    assert result.applicable is False
    assert result.score is None


def test_pacing_penalises_slow_speech_with_long_pauses(base_signals):
    slow = {
        **base_signals,
        "words_per_minute": 95.0,
        "pause_count": 6,
        "pauses": [{"t": 10.0, "duration_s": 3.1}],
        "longest_pause": {"t": 10.0, "duration_s": 3.1},
        "speech_ratio": 0.55,
    }

    assert score_pacing(slow).score < score_pacing(base_signals).score
    assert score_pacing(slow).fix is not None
    assert score_pacing(base_signals).score == 10


def test_audio_penalises_quiet_and_clipped_mix(base_signals):
    bad = {**base_signals, "loudness_lufs": -26.0, "true_peak_dbtp": 0.2}

    assert score_audio(bad).score <= 5
    assert "LUFS" in score_audio(bad).fix
    assert score_audio(base_signals).score == 10
