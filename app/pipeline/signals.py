"""Deterministic signals derived from the raw extraction. Pure functions, no I/O."""

import re
from statistics import median

# Openers that continue a previous sentence on their own.
COORDINATING = frozenset({"and", "so", "but", "cause", "or", "then", "which", "also", "plus", "anyway", "like"})
# Openers that also start perfectly good sentences ("If you're over 30..."): a weak signal on their own.
SUBORDINATING = frozenset(
    {"if", "because", "when", "whenever", "although", "though", "since", "unless", "while", "whereas",
     "whether", "until", "once", "as", "after", "before"}
)
CONJUNCTIONS = COORDINATING | SUBORDINATING
PAUSE_MIN_S = 1.0
EDGE_TOLERANCE_S = 0.3
SPEECH_MERGE_GAP_S = 0.5
MAX_LISTED = 20


def _clean(word: str) -> str:
    return re.sub(r"[^\w']", "", word).strip()


def _speech_time(words: list[dict]) -> float:
    total, start, end = 0.0, None, None
    for w in words:
        if start is None:
            start, end = w["start"], w["end"]
        elif w["start"] - end <= SPEECH_MERGE_GAP_S:
            end = max(end, w["end"])
        else:
            total += end - start
            start, end = w["start"], w["end"]
    if start is not None:
        total += end - start
    return total


def _cut_rhythm(cuts: list[float], duration_s: float) -> dict:
    """Shot lengths between cuts, so a long static stretch shows up even when the average looks busy."""
    edges = [0.0, *[c for c in cuts if 0 < c < duration_s], duration_s]
    shots = [(a, b - a) for a, b in zip(edges, edges[1:])]
    start, length = max(shots, key=lambda s: s[1])
    third = duration_s / 3 if duration_s else 0.0
    per_third = [
        round(sum(1 for c in cuts if i * third <= c < (i + 1) * third) / (third / 60), 1) if third else 0.0
        for i in range(3)
    ]
    return {
        "longest_shot": {"t": round(start, 2), "duration_s": round(length, 2)},
        "static_tail_s": round(duration_s - edges[-2], 2),
        "median_shot_s": round(median(length for _, length in shots), 2) if cuts else None,
        "cuts_per_minute_by_third": per_third,
    }


def compute_signals(
    *,
    duration_s: float,
    words: list[dict],
    segments: list[dict],
    cuts: list[float],
    loudness: dict,
    silences: list[dict],
    has_audio: bool,
    frames_sampled: list[float],
) -> dict:
    minutes = duration_s / 60 if duration_s else 1.0
    first = words[0] if words else None
    last = words[-1] if words else None

    pauses = []
    for prev, nxt in zip(words, words[1:]):
        gap = nxt["start"] - prev["end"]
        if gap > PAUSE_MIN_S:
            pauses.append({"t": round(prev["end"], 2), "duration_s": round(gap, 2)})
    longest = max(pauses, key=lambda p: p["duration_s"]) if pauses else None

    span = (last["end"] - first["start"]) if words else 0.0
    first_word = _clean(first["word"]) if first else None
    opener = (first_word or "").lower()

    speech_segments = [s for s in segments if s["end"] > s["start"]]
    seg_time = sum(s["end"] - s["start"] for s in speech_segments)
    avg_logprob = (
        sum(s["avg_logprob"] * (s["end"] - s["start"]) for s in speech_segments) / seg_time
        if seg_time
        else None
    )

    return {
        "duration_s": round(duration_s, 2),
        "has_audio": has_audio,
        "word_count": len(words),
        "time_to_first_word_s": round(first["start"], 2) if first else None,
        "first_word": first_word,
        "first_word_is_conjunction": opener in CONJUNCTIONS,
        "first_word_is_subordinating": opener in SUBORDINATING,
        # Whisper capitalises sentence starts, so a lowercase opener hints at a sentence already under way.
        "first_word_lowercase": bool(first_word) and first_word[0].islower(),
        "speech_at_start": bool(first) and first["start"] <= EDGE_TOLERANCE_S,
        "speech_at_end": bool(last) and last["end"] >= duration_s - EDGE_TOLERANCE_S,
        "last_word_end_s": round(last["end"], 2) if last else None,
        "words_per_minute": round(len(words) / (span / 60), 1) if span > 0 else 0.0,
        "speech_ratio": round(min(1.0, _speech_time(words) / duration_s), 3) if duration_s else 0.0,
        "pause_count": len(pauses),
        "pauses": pauses[:MAX_LISTED],
        "longest_pause": longest,
        "cuts": [round(c, 2) for c in cuts[:MAX_LISTED]],
        "cut_count": len(cuts),
        "cuts_per_minute": round(len(cuts) / minutes, 2),
        "cuts_first_3s": sum(1 for c in cuts if c <= 3.0),
        **_cut_rhythm(cuts, duration_s),
        "loudness_lufs": loudness.get("integrated_lufs"),
        "true_peak_dbtp": loudness.get("true_peak_dbtp"),
        "silence_total_s": round(sum(s["duration_s"] for s in silences), 2),
        "avg_logprob": round(avg_logprob, 3) if avg_logprob is not None else None,
        "word_probability_mean": (
            round(sum(w.get("probability", 0.0) for w in words) / len(words), 3) if words else None
        ),
        "frames_sampled_s": frames_sampled,
    }


CLIPPING_DBTP = -0.1


def public_signals(s: dict) -> dict:
    """The documented `signals` block of the API contract. Internal signals stay internal."""
    peak = s.get("true_peak_dbtp")
    return {
        "time_to_first_word_s": s["time_to_first_word_s"],
        "words_per_minute": s["words_per_minute"],
        "speech_ratio": s["speech_ratio"],
        "longest_pause": s["longest_pause"],
        "pauses_over_1s": s["pause_count"],
        "cuts": s["cut_count"],
        "cuts_per_minute": s["cuts_per_minute"],
        "integrated_loudness_lufs": s["loudness_lufs"],
        "clipping": peak is not None and peak > CLIPPING_DBTP,
        "transcript_confidence": s["word_probability_mean"],
    }
