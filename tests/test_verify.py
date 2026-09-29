from app.pipeline.verify import BoundaryFlags, parse_timestamps, verify


def test_late_start_caps_hook_and_lowers_confidence(make_drafts, rubric, base_signals):
    drafts = make_drafts(hook=8)
    signals = {**base_signals, "time_to_first_word_s": 5.2}

    result = verify(drafts, signals, BoundaryFlags(), rubric)

    assert drafts["hook"].score == rubric.verify.late_start_hook_ceiling
    assert drafts["hook"].confidence == "medium"
    assert [c["rule"] for c in result.contradictions] == ["late_start"]


def test_early_start_leaves_hook_untouched(make_drafts, rubric, base_signals):
    drafts = make_drafts(hook=8)

    result = verify(drafts, base_signals, BoundaryFlags(), rubric)

    assert drafts["hook"].score == 8
    assert result.contradictions == []
    assert result.checks_run == 3


def test_boundary_cut_needs_two_agreeing_signals(make_drafts, rubric, base_signals):
    drafts = make_drafts(standalone_completeness=8)
    signals = {**base_signals, "speech_at_start": True}  # speech at frame 0, but a clean first word

    result = verify(drafts, signals, BoundaryFlags(starts_mid_thought=False), rubric)

    assert result.cap_reason is None
    assert drafts["standalone_completeness"].score == 8


def test_boundary_cut_at_start_applies_cap(make_drafts, rubric, base_signals):
    drafts = make_drafts(standalone_completeness=8)
    signals = {**base_signals, "speech_at_start": True, "first_word_is_conjunction": True, "first_word": "And"}

    result = verify(drafts, signals, BoundaryFlags(), rubric)

    assert result.cap_reason is not None
    assert "start" in result.cap_reason.lower()
    assert drafts["standalone_completeness"].score == rubric.verify.boundary_completeness_ceiling
    assert any(c["rule"] == "boundary_cut" for c in result.contradictions)


def test_boundary_cut_at_end_uses_judge_flag(make_drafts, rubric, base_signals):
    drafts = make_drafts(standalone_completeness=4)
    signals = {**base_signals, "speech_at_end": True}

    result = verify(drafts, signals, BoundaryFlags(ends_mid_thought=True), rubric)

    assert result.cap_reason is not None
    # Judge already agreed it was weak: no contradiction, score kept.
    assert drafts["standalone_completeness"].score == 4
    assert result.contradictions == []


def test_evidence_beyond_clip_length_lowers_confidence(make_drafts, rubric, base_signals):
    drafts = make_drafts()
    drafts["clarity_payoff"].evidence = ["The payoff lands at 1:45 with the 90-day proposal."]

    result = verify(drafts, base_signals, BoundaryFlags(), rubric)

    assert drafts["clarity_payoff"].confidence == "medium"
    assert [c["rule"] for c in result.contradictions] == ["missing_evidence"]


def test_parse_timestamps():
    assert parse_timestamps("At 0:06 the claim lands, and again at 1:02.5") == [6.0, 62.5]
    assert parse_timestamps("First word at 4.2 s") == [4.2]
    assert parse_timestamps("no timestamps here") == []
