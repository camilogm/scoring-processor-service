import json

import pytest

from app.pipeline.judge import parse_judge_output
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
