from app.pipeline.duration_check import TARGET_S, check_duration


def test_target_is_the_three_minutes_of_the_brief():
    assert TARGET_S == 180


def test_clip_up_to_three_minutes_is_within_target():
    assert check_duration(74.2, 240.0) == {"status": "within_target", "duration_s": 74.2, "target_s": 180,
                                           "limit_s": 240}
    assert check_duration(180.0, 240.0)["status"] == "within_target"


def test_short_overrun_is_reported_not_rejected():
    # The brief keeps a 3:05 clip in scope: over the target is information, not an error.
    assert check_duration(185.0, 240.0)["status"] == "over_target"
    assert check_duration(194.0, 240.0)["status"] == "over_target"


def test_limit_follows_the_configured_maximum():
    assert check_duration(100.0, 300.0)["limit_s"] == 300
