import pytest

from app.pipeline.score import compute_scores, verdict_for


def test_weighted_average_when_every_dimension_applies(make_drafts, rubric):
    drafts = make_drafts(hook=8, standalone_completeness=8, clarity_payoff=8, pacing_energy=8, audio_quality=8, on_screen_text=8)

    out = compute_scores(list(drafts.values()), rubric, cap_reason=None)

    assert out["overall"]["score"] == 8.0
    assert out["overall"]["verdict"] == "post"
    assert out["overall"]["capped"] is False


def test_non_applicable_dimension_is_dropped_and_weights_rescaled(make_drafts, rubric):
    drafts = make_drafts(hook=10, standalone_completeness=6, clarity_payoff=6, pacing_energy=6, audio_quality=None, on_screen_text=6)

    out = compute_scores(list(drafts.values()), rubric, cap_reason=None)

    # (10*.25 + 6*.20 + 6*.20 + 6*.15 + 6*.10) / 0.90 = 7.11
    assert out["overall"]["score"] == 7.1
    audio = next(d for d in out["dimensions"] if d["id"] == "audio_quality")
    assert audio["score"] is None
    assert audio["applicable"] is False
    assert sum(d["weight"] for d in out["dimensions"] if d["applicable"]) == pytest.approx(1.0)


def test_cap_rule_limits_overall_to_six(make_drafts, rubric):
    drafts = make_drafts(hook=9, standalone_completeness=9, clarity_payoff=9, pacing_energy=9, audio_quality=9, on_screen_text=9)

    out = compute_scores(list(drafts.values()), rubric, cap_reason="Starts mid-sentence")

    assert out["overall"]["raw_score"] == pytest.approx(9.0)
    assert out["overall"]["score"] == 6.0
    assert out["overall"]["verdict"] == "improve"
    assert out["overall"]["capped"] is True
    assert out["overall"]["cap_reason"] == "Starts mid-sentence"


@pytest.mark.parametrize(
    ("score", "verdict"),
    [(10.0, "post"), (7.0, "post"), (6.9, "improve"), (5.0, "improve"), (4.9, "skip"), (0.0, "skip")],
)
def test_verdict_thresholds(rubric, score, verdict):
    assert verdict_for(score, rubric) == verdict


def test_priority_fixes_ranked_by_weighted_gap(make_drafts, rubric):
    # gaps: hook .25*(10-5)=1.25, clarity .20*(10-4)=1.2, audio .10*(10-0)=1.0
    drafts = make_drafts(hook=5, standalone_completeness=10, clarity_payoff=4, pacing_energy=10, audio_quality=0, on_screen_text=10)

    out = compute_scores(list(drafts.values()), rubric, cap_reason=None)

    fixes = out["priority_fixes"]
    assert [f["dimension"] for f in fixes] == ["hook", "clarity_payoff", "audio_quality"]
    assert [f["rank"] for f in fixes] == [1, 2, 3]
    assert fixes[0]["weighted_gap"] == 1.25


def test_priority_fixes_skip_dimensions_without_a_fix(make_drafts, rubric):
    drafts = make_drafts(
        hook=2, standalone_completeness=10, clarity_payoff=10, pacing_energy=10, audio_quality=10, on_screen_text=10, fixes={"hook": None}
    )

    out = compute_scores(list(drafts.values()), rubric, cap_reason=None)

    assert out["priority_fixes"] == []
