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


# C30.mp4: 15 cuts, the last at 33.17 s, then a 13.5 s static tail to 46.63 s.
C30_CUTS = [1.42, 2.33, 3.38, 4.5, 5.54, 6.71, 8.25, 11.83, 15.29, 17.0, 19.21, 23.17, 28.75, 30.04, 33.17]


def test_subordinating_opener_is_a_conjunction_but_flagged_as_weak():
    s = _signals([_w(" if", 0.0, 0.2), _w(" they", 0.2, 0.4), _w(" continue.", 0.4, 9.9)])

    assert s["first_word_is_conjunction"] is True
    assert s["first_word_is_subordinating"] is True
    assert s["first_word_lowercase"] is True


def test_capitalised_subordinating_opener_reads_as_a_sentence_start():
    s = _signals([_w(" If", 0.0, 0.2), _w(" you're", 0.2, 0.4), _w(" over", 0.4, 9.9)])

    assert s["first_word_is_subordinating"] is True
    assert s["first_word_lowercase"] is False


def test_coordinating_opener_is_not_subordinating():
    s = _signals([_w(" And", 0.0, 0.3), _w(" so.", 0.35, 9.9)])

    assert s["first_word_is_conjunction"] is True
    assert s["first_word_is_subordinating"] is False


def test_cut_rhythm_signals_find_the_static_tail():
    s = _signals([_w(" Hello.", 0.5, 45.0)], duration=46.63, cuts=C30_CUTS)

    assert s["longest_shot"] == {"t": 33.17, "duration_s": 13.46}
    assert s["static_tail_s"] == 13.46
    assert s["median_shot_s"] == pytest.approx(1.63, abs=0.01)
    assert s["cuts_per_minute_by_third"] == pytest.approx([34.7, 19.3, 3.9], abs=0.1)


def test_cut_rhythm_signals_without_cuts():
    s = _signals([_w(" Hello.", 0.5, 9.0)], cuts=[])

    assert s["longest_shot"] == {"t": 0.0, "duration_s": 10.0}
    assert s["static_tail_s"] == 10.0
    assert s["median_shot_s"] is None
    assert s["cuts_per_minute_by_third"] == [0.0, 0.0, 0.0]


def _rhythm(base, **kw):
    return {**base, "longest_shot": None, "static_tail_s": 0.0, "median_shot_s": None,
            "cuts_per_minute_by_third": [4.0, 4.0, 4.0], **kw}


def test_pacing_penalises_a_long_static_tail_after_a_fast_edit(base_signals):
    c30 = _rhythm(base_signals, duration_s=46.63, cut_count=15, cuts_per_minute=19.3,
                  longest_shot={"t": 33.17, "duration_s": 13.46}, static_tail_s=13.46, median_shot_s=1.63,
                  cuts_per_minute_by_third=[34.7, 19.3, 3.9])

    result = score_pacing(c30)

    assert result.score <= 8
    assert any("13.5 s" in e and "0:33" in e for e in result.evidence)
    assert "0:33" in result.fix


def test_pacing_does_not_punish_a_static_podcast_camera(base_signals):
    podcast = _rhythm(base_signals, cut_count=0, cuts_per_minute=0.0,
                      longest_shot={"t": 0.0, "duration_s": 60.0}, static_tail_s=60.0,
                      cuts_per_minute_by_third=[0.0, 0.0, 0.0])

    assert score_pacing(podcast).score >= 9


def test_pacing_ignores_a_long_shot_that_matches_the_clip_rhythm(base_signals):
    steady = _rhythm(base_signals, cut_count=6, longest_shot={"t": 40.0, "duration_s": 10.0},
                     static_tail_s=10.0, median_shot_s=8.5)

    assert score_pacing(steady).score == 10
