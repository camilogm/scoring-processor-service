import pytest

from app.pipeline.format_check import check_format


def _checks(result):
    return {i["check"]: i["status"] for i in result["issues"]}


def test_vertical_1080p_is_optimal():
    result = check_format(1080, 1920)

    assert result["status"] == "optimal"
    assert result["platforms"] == ["instagram", "tiktok"]
    assert result["aspect_ratio"] == "9:16"
    assert result["orientation"] == "vertical"
    assert result["issues"] == []


def test_near_9_16_encodes_count_as_9_16():
    # Encoders round odd sizes (1080x1916, 1242x2208): still a full-screen fit.
    assert check_format(1080, 1916)["aspect_ratio"] == "9:16"
    assert check_format(1080, 1916)["status"] == "optimal"


def test_vertical_720p_is_acceptable_but_below_recommended():
    result = check_format(720, 1280)

    assert result["status"] == "acceptable"
    assert _checks(result) == {"resolution": "acceptable"}


def test_low_resolution_is_not_optimal():
    result = check_format(480, 854)

    assert result["status"] == "not_optimal"
    assert _checks(result) == {"resolution": "not_optimal"}


def test_vertical_4_5_is_acceptable():
    result = check_format(1080, 1350)

    assert result["aspect_ratio"] == "4:5"
    assert result["orientation"] == "vertical"
    assert result["status"] == "acceptable"
    assert _checks(result) == {"aspect_ratio": "acceptable"}


@pytest.mark.parametrize(("width", "height", "ratio", "orientation"), [
    (1920, 1080, "16:9", "horizontal"),
    (1080, 1080, "1:1", "square"),
])
def test_horizontal_and_square_are_not_optimal(width, height, ratio, orientation):
    result = check_format(width, height)

    assert result["aspect_ratio"] == ratio
    assert result["orientation"] == orientation
    assert result["status"] == "not_optimal"
    assert _checks(result)["aspect_ratio"] == "not_optimal"


def test_resolution_is_judged_on_the_short_side():
    # 1280x720 is 1280 px wide, but a 720 px short side is what ends up filling the phone's width.
    assert _checks(check_format(1280, 720)) == {"aspect_ratio": "not_optimal", "resolution": "acceptable"}


def test_unknown_dimensions_are_reported_not_guessed():
    result = check_format(0, 0)

    assert result["status"] == "unknown"
    assert result["aspect_ratio"] is None
    assert result["orientation"] is None


def test_every_issue_says_how_to_fix_it():
    for issue in check_format(640, 480)["issues"]:
        assert issue["message"]
        assert issue["fix"]
