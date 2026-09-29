"""Dimensions scored from measurements only (pacing, audio). Thresholds are norms, tuned on the dev subset."""

from dataclasses import dataclass, field

from app.pipeline.model import fmt_ts
from app.pipeline.signals import CLIPPING_DBTP

TARGET_LUFS = -14.0
RHYTHM_MIN_CUTS = 3  # fewer cuts than this is a static format, not an edit rhythm
STATIC_SHOT_MIN_S, STATIC_SHOT_RATIO = 8.0, 3.0
STATIC_SHOT_SEVERE_S, STATIC_SHOT_SEVERE_RATIO = 10.0, 5.0


@dataclass
class MeasuredScore:
    score: int | None
    applicable: bool = True
    confidence: str = "high"
    evidence: list[str] = field(default_factory=list)
    fix: str | None = None


def _finish(score: float, evidence: list[str], fixes: list[str], confidence: str = "high") -> MeasuredScore:
    return MeasuredScore(
        score=int(round(max(0.0, min(10.0, score)))),
        confidence=confidence,
        evidence=evidence,
        fix=" ".join(fixes[:2]) or None,
    )


def score_pacing(s: dict) -> MeasuredScore:
    if not s.get("word_count"):
        return MeasuredScore(score=None, applicable=False, evidence=["No speech detected."])

    score, evidence, fixes = 10.0, [], []

    wpm = s["words_per_minute"]
    evidence.append(f"{wpm:.0f} words per minute (comfortable range 140–195).")
    if wpm < 110:
        score -= 3
        fixes.append("Delivery is slow: trim filler and pauses, or speed the clip up 10–15%.")
    elif wpm < 140:
        score -= 1
        fixes.append("Delivery is a little slow: tighten gaps between sentences.")
    elif wpm > 220:
        score -= 2
        fixes.append("Speech is very fast: add captions and let key lines breathe.")
    elif wpm > 195:
        score -= 1

    minutes = s["duration_s"] / 60 or 1.0
    per_minute = s["pause_count"] / minutes
    if s["pause_count"]:
        evidence.append(f"{s['pause_count']} pauses over 1 s ({per_minute:.1f} per minute).")
    if per_minute > 1:
        score -= min(3.0, per_minute - 1)

    longest = s.get("longest_pause")
    if longest and longest["duration_s"] > 2.0:
        score -= 1
        evidence.append(f"Longest pause {longest['duration_s']:.1f} s at {fmt_ts(longest['t'])}.")
        fixes.insert(0, f"Cut the {longest['duration_s']:.1f} s pause at {fmt_ts(longest['t'])}.")

    ratio = s["speech_ratio"]
    evidence.append(f"Speech covers {ratio:.0%} of the clip.")
    if ratio < 0.6:
        score -= 2
        fixes.append("Remove stretches without speech or cover them with b-roll and text.")
    elif ratio < 0.75:
        score -= 1

    # Cuts weigh lightly so static podcast cameras aren't punished for the format.
    thirds = s.get("cuts_per_minute_by_third")
    by_third = f" ({' / '.join(f'{c:.0f}' for c in thirds)} by third)" if thirds and s.get("cut_count") else ""
    evidence.append(f"{s['cuts_per_minute']:.1f} cuts per minute{by_third}.")
    if s["duration_s"] >= 30 and s["cuts_per_minute"] == 0:
        score -= 0.5

    # What hurts is a static stretch in a clip that set a faster rhythm, not a static format.
    shot, typical = s.get("longest_shot"), s.get("median_shot_s")
    if shot and typical and s.get("cut_count", 0) >= RHYTHM_MIN_CUTS:
        length, start = shot["duration_s"], shot["t"]
        ratio = length / typical
        if length >= STATIC_SHOT_MIN_S and ratio >= STATIC_SHOT_RATIO:
            score -= 2 if length >= STATIC_SHOT_SEVERE_S and ratio >= STATIC_SHOT_SEVERE_RATIO else 1
            at_tail = start + length >= s["duration_s"] - 0.05
            where = f"the last {length:.1f} s, from {fmt_ts(start)}" if at_tail else f"{length:.1f} s from {fmt_ts(start)}"
            evidence.append(f"No cut for {where} (typical shot {typical:.1f} s).")
            fixes.insert(
                0,
                f"Break up the {length:.1f} s static shot at {fmt_ts(start)}–{fmt_ts(start + length)} "
                "with b-roll, a punch-in or a text card.",
            )

    return _finish(score, evidence, fixes)


def score_audio(s: dict) -> MeasuredScore:
    if not s.get("has_audio"):
        return MeasuredScore(score=None, applicable=False, evidence=["No audio stream."])
    if not s.get("word_count"):
        return MeasuredScore(score=None, applicable=False, evidence=["No speech detected; speech quality not scored."])

    score, evidence, fixes, confidence = 10.0, [], [], "high"

    lufs = s.get("loudness_lufs")
    if lufs is None:
        confidence = "low"
        evidence.append("Loudness could not be measured.")
    else:
        deviation = abs(lufs - TARGET_LUFS)
        evidence.append(f"Integrated loudness {lufs:.1f} LUFS (platform target about {TARGET_LUFS:.0f}).")
        if deviation > 8:
            score -= 4
        elif deviation > 5:
            score -= 2
        elif deviation > 3:
            score -= 1
        if deviation > 3:
            fixes.append(f"Normalise loudness to about {TARGET_LUFS:.0f} LUFS (currently {lufs:.1f}).")

    peak = s.get("true_peak_dbtp")
    if peak is not None:
        evidence.append(f"True peak {peak:.1f} dBTP.")
        if peak > CLIPPING_DBTP:
            score -= 2
            fixes.append("Peaks hit 0 dBTP: add a limiter at -1 dBTP to avoid distortion.")

    logprob = s.get("avg_logprob")
    if logprob is not None:
        # Whisper's confidence is a rough intelligibility proxy.
        evidence.append(f"Speech recognition confidence (avg log-prob) {logprob:.2f}.")
        if logprob < -0.9:
            score -= 3
            fixes.append("Speech is hard to make out: reduce background noise or music under the voice.")
        elif logprob < -0.6:
            score -= 1.5
            fixes.append("Clean up the voice track (noise reduction, lower music bed).")

    silence = s.get("silence_total_s") or 0.0
    if s["duration_s"] and silence / s["duration_s"] > 0.15:
        score -= 1
        evidence.append(f"{silence:.1f} s of silence in total.")

    return _finish(score, evidence, fixes, confidence)
