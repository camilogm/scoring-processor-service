"""Where the clip sits against the brief's length scope.

The brief targets clips of up to 3 minutes and keeps a short overrun (3:05) in scope; long-form is out.
So 3:00 is the target and MAX_DURATION_S (240 s by default) the hard limit the upload enforces.
Being over the target is information, never an error, and never changes the score.
"""

TARGET_S = 180


def check_duration(duration_s: float, limit_s: float) -> dict:
    return {
        "status": "within_target" if duration_s <= TARGET_S else "over_target",
        "duration_s": duration_s,
        "target_s": TARGET_S,
        "limit_s": int(limit_s) if float(limit_s).is_integer() else limit_s,
    }
