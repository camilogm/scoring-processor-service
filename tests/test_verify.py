from app.pipeline.verify import BoundaryFlags, locate_quote, parse_timestamps, verify


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
    assert result.checks_run == 4


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


def _words(text, start, step=0.5):
    return [{"word": " " + w, "start": round(start + i * step, 2), "end": round(start + i * step + 0.4, 2)}
            for i, w in enumerate(text.split())]


# C30.mp4 transcript excerpt.
C30_WORDS = (
    _words("if they continue with this war.", 0.0)
    + _words("They can't handle more migration,", 20.52)
    + _words("but where the hell is that migration gonna go?", 22.98)
    + _words("It's gonna go to Europe.", 25.82)
)


def test_lowercase_subordinating_opener_caps_like_a_coordinating_one(make_drafts, rubric, base_signals):
    drafts = make_drafts(standalone_completeness=8)
    signals = {**base_signals, "speech_at_start": True, "first_word": "if", "first_word_is_conjunction": True,
               "first_word_is_subordinating": True, "first_word_lowercase": True}

    result = verify(drafts, signals, BoundaryFlags(), rubric)

    assert result.cap_reason is not None
    assert "if" in result.cap_reason


def test_capitalised_subordinating_opener_alone_does_not_cap(make_drafts, rubric, base_signals):
    drafts = make_drafts(standalone_completeness=8)
    signals = {**base_signals, "speech_at_start": True, "first_word": "If", "first_word_is_conjunction": True,
               "first_word_is_subordinating": True, "first_word_lowercase": False}

    result = verify(drafts, signals, BoundaryFlags(), rubric)

    assert result.cap_reason is None
    assert drafts["standalone_completeness"].score == 8


def test_capitalised_subordinating_opener_caps_when_the_judge_agrees(make_drafts, rubric, base_signals):
    drafts = make_drafts(standalone_completeness=8)
    signals = {**base_signals, "speech_at_start": True, "first_word": "If", "first_word_is_conjunction": True,
               "first_word_is_subordinating": True, "first_word_lowercase": False}

    result = verify(drafts, signals, BoundaryFlags(starts_mid_thought=True), rubric)

    assert result.cap_reason is not None


def test_locate_quote_finds_the_first_word_of_the_quote():
    assert locate_quote("Where the hell is that migration gonna go?", C30_WORDS) == 22.98 + 0.5
    assert locate_quote("but where the hell is that migration going to go", C30_WORDS) == 22.98
    assert locate_quote("Nothing like this was said", C30_WORDS) is None
    assert locate_quote("go", C30_WORDS) is None  # too short to anchor


def test_quoted_evidence_with_a_wrong_timestamp_is_corrected(make_drafts, rubric, base_signals):
    drafts = make_drafts()
    drafts["clarity_payoff"].evidence = ["At 0:40 “where the hell is that migration gonna go?” lands the point."]

    result = verify(drafts, base_signals, BoundaryFlags(), rubric, words=C30_WORDS)

    assert drafts["clarity_payoff"].evidence == [
        "At 0:23 “where the hell is that migration gonna go?” lands the point."
    ]
    assert drafts["clarity_payoff"].confidence == "medium"
    assert [c["rule"] for c in result.contradictions] == ["evidence_timestamp"]


def test_quoted_evidence_with_a_close_timestamp_is_kept(make_drafts, rubric, base_signals):
    drafts = make_drafts()
    drafts["clarity_payoff"].evidence = ["At 0:23 'where the hell is that migration gonna go' lands the point."]

    result = verify(drafts, base_signals, BoundaryFlags(), rubric, words=C30_WORDS)

    assert result.contradictions == []
    assert drafts["clarity_payoff"].confidence == "high"


def test_quote_missing_from_transcript_lowers_confidence(make_drafts, rubric, base_signals):
    drafts = make_drafts()
    drafts["hook"].evidence = ["Opens with “the whole world is watching Tehran tonight” at 0:01."]

    result = verify(drafts, base_signals, BoundaryFlags(), rubric, words=C30_WORDS)

    assert drafts["hook"].confidence == "medium"
    assert [c["rule"] for c in result.contradictions] == ["quote_not_in_transcript"]


def test_transcript_line_citations_are_formatted_and_checked(make_drafts, rubric, base_signals):
    # gemma3 copies the transcript's "[20.5s] ..." prefix into evidence, without quote marks.
    drafts = make_drafts()
    drafts["clarity_payoff"].evidence = [
        "[20.5s] They can't handle more migration, but where the hell...",
        "[40.0s] It's gonna go to Europe.",
        "[2.0s] Opens on bombing footage",  # not a quote from the transcript: left as a plain claim
    ]

    result = verify(drafts, base_signals, BoundaryFlags(), rubric, words=C30_WORDS)

    assert drafts["clarity_payoff"].evidence == [
        "0:20 “They can't handle more migration, but where the hell...”",
        "0:26 “It's gonna go to Europe.”",
        "0:02 Opens on bombing footage",
    ]
    assert [c["rule"] for c in result.contradictions] == ["evidence_timestamp"]


def test_repeated_line_is_checked_against_the_nearest_occurrence(make_drafts, rubric, base_signals):
    # C30 says "It's gonna go to Europe." at 25.8 s and again at 33.5 s.
    words = C30_WORDS + _words("It's not gonna go east.", 28.62) + _words("It's gonna go to Europe.", 33.5)
    drafts = make_drafts()
    drafts["clarity_payoff"].evidence = ["[33.5s] It's gonna go to Europe."]

    result = verify(drafts, base_signals, BoundaryFlags(), rubric, words=words)

    assert drafts["clarity_payoff"].evidence == ["0:34 “It's gonna go to Europe.”"]
    assert result.contradictions == []


def test_locate_quote_uses_a_hint_to_pick_between_repeats():
    words = C30_WORDS + _words("It's gonna go to Europe.", 33.5)

    assert locate_quote("It's gonna go to Europe.", words) == 25.82
    assert locate_quote("It's gonna go to Europe.", words, near=34.7) == 33.5
