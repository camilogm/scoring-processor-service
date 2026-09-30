"""Does the frame fit Instagram Reels and TikTok? Both play full screen at 9:16, 1080×1920 recommended.

Deterministic, from the probed dimensions only. It informs the report and never changes the score:
the rubric grades the content, and a horizontal clip can be reframed without re-editing it.
"""

from math import gcd

PLATFORMS = ("instagram", "tiktok")
RECOMMENDED = (1080, 1920)
MIN_SHORT_SIDE = 720
# Encoders round odd sizes (1080x1916), so ratios within 2% count as the named one.
RATIO_TOLERANCE = 0.02
NAMED_RATIOS = {"9:16": 9 / 16, "2:3": 2 / 3, "3:4": 3 / 4, "4:5": 4 / 5, "1:1": 1.0,
                "5:4": 5 / 4, "4:3": 4 / 3, "3:2": 3 / 2, "16:9": 16 / 9}
_RANK = {"optimal": 0, "acceptable": 1, "not_optimal": 2}
_TARGET = f"9:16 ({RECOMMENDED[0]}×{RECOMMENDED[1]})"


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= b * RATIO_TOLERANCE


def _ratio_name(width: int, height: int) -> str:
    ratio = width / height
    for name, value in NAMED_RATIOS.items():
        if _close(ratio, value):
            return name
    d = gcd(width, height)
    return f"{width // d}:{height // d}"


def _orientation(width: int, height: int) -> str:
    ratio = width / height
    if _close(ratio, 1.0):
        return "square"
    return "vertical" if ratio < 1 else "horizontal"


def _aspect_issue(width: int, height: int, name: str, orientation: str) -> dict | None:
    if _close(width / height, NAMED_RATIOS["9:16"]):
        return None
    if orientation == "vertical":
        return {"check": "aspect_ratio", "status": "acceptable",
                "message": f"Vertical {name} instead of 9:16: full-screen feeds crop it or add bars.",
                "fix": f"Export at {_TARGET}."}
    return {"check": "aspect_ratio", "status": "not_optimal",
            "message": f"{orientation.capitalize()} {name}: it plays small, with wide bars, in a vertical feed.",
            "fix": f"Reframe to {_TARGET}, keeping the speaker centred."}


def _resolution_issue(width: int, height: int) -> dict | None:
    short = min(width, height)
    if short >= RECOMMENDED[0]:
        return None
    status = "acceptable" if short >= MIN_SHORT_SIDE else "not_optimal"
    detail = "below the recommended" if status == "acceptable" else "too low; it will look soft next to the"
    return {"check": "resolution", "status": status,
            "message": f"{width}×{height} is {detail} {RECOMMENDED[0]}×{RECOMMENDED[1]}.",
            "fix": f"Export from a higher-resolution source at {RECOMMENDED[0]}×{RECOMMENDED[1]}."}


def check_format(width: int, height: int) -> dict:
    base = {"platforms": list(PLATFORMS), "recommended": {"width": RECOMMENDED[0], "height": RECOMMENDED[1],
                                                          "aspect_ratio": "9:16"}}
    if width <= 0 or height <= 0:
        return {"status": "unknown", "aspect_ratio": None, "orientation": None, **base, "issues": []}

    name, orientation = _ratio_name(width, height), _orientation(width, height)
    issues = [i for i in (_aspect_issue(width, height, name, orientation), _resolution_issue(width, height)) if i]
    status = max((i["status"] for i in issues), key=_RANK.__getitem__, default="optimal")
    return {"status": status, "aspect_ratio": name, "orientation": orientation, **base, "issues": issues}
