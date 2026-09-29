import json

import pytest

from app.pipeline.judge import parse_judge_output, transcript_lines
from app.pipeline.model import ModelOutputError


def _payload(**overrides):
    dim = {"evidence": ["0:01 opens with a claim"], "score": 4, "fix": "Add a headline.", "confidence": "high"}
    body = {
        "hook": dim,
        "standalone_completeness": {**dim, "starts_mid_thought": False, "ends_mid_thought": True},
        "clarity_payoff": dim,
        "on_screen_text": dim,
        "summary": "Solid clip.",
        "point": "Permits are slow.",
        "potential": {"helps": ["a", "b", "c", "d"], "holds_back": [], "shareable_line": None},
    }
    body.update(overrides)
    return body


def test_parses_json_wrapped_in_markdown_fence():
    raw = "```json\n" + json.dumps(_payload()) + "\n```"

    out = parse_judge_output(raw)

    assert out.hook.score == 4
    assert out.standalone_completeness.ends_mid_thought is True
    assert out.potential.helps == ["a", "b", "c"]  # trimmed to 3


def test_parses_json_in_a_bare_fence():
    raw = "```\n" + json.dumps(_payload()) + "\n```"

    assert parse_judge_output(raw).hook.score == 4


def test_clamps_and_rounds_scores():
    raw = json.dumps(_payload(hook={"evidence": [], "score": 7.4, "fix": None, "confidence": "HIGH"}))

    out = parse_judge_output(raw)

    assert out.hook.score == 5
    assert out.hook.confidence == "high"


def test_invalid_output_raises_model_output_error():
    with pytest.raises(ModelOutputError):
        parse_judge_output("I think this clip is great!")

    with pytest.raises(ModelOutputError):
        parse_judge_output(json.dumps({"hook": {"score": 3}}))


def _words(text, start, step=0.5):
    return [{"word": " " + w, "start": round(start + i * step, 2), "end": round(start + i * step + 0.4, 2)}
            for i, w in enumerate(text.split())]


def test_transcript_lines_break_long_segments_at_sentences():
    # C30 has one 9.4 s segment; the judge used to see only its start (0:29).
    words = _words("It's not gonna go east.", 28.62) + _words("It's gonna go to Europe.", 33.0)
    segments = [{"start": 28.62, "end": 38.06, "text": "It's not gonna go east. It's gonna go to Europe."}]

    lines = transcript_lines(words, segments)

    assert lines == ["[28.6s] It's not gonna go east.", "[33.0s] It's gonna go to Europe."]


def test_transcript_lines_split_runs_without_punctuation():
    words = _words(" ".join(f"w{i}" for i in range(40)), 0.0)

    lines = transcript_lines(words, [])

    assert len(lines) == 3
    assert lines[1].startswith("[8.0s] w16")


def test_transcript_lines_fall_back_to_segments_without_words():
    segments = [{"start": 22.98, "end": 25.4, "text": "but where the hell is that migration gonna go?"}]

    assert transcript_lines([], segments) == ["[23.0s] but where the hell is that migration gonna go?"]


@pytest.mark.parametrize("t, expected", [(23.0, 23.0), ("28.6s", 28.6), ("0:23", 23.0), ("soon", None)])
def test_shareable_line_time_is_parsed_leniently(t, expected):
    # The runner takes the time from the transcript, so a sloppy `t` must not fail the analysis.
    line = {"t": t, "text": "It's not gonna go east.", "note": "Stark."}

    out = parse_judge_output(json.dumps(_payload(potential={"shareable_line": line})))

    assert out.potential.shareable_line.t == expected
