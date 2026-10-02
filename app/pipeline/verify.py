"""Stage 5: deterministic rules that check the judge against the measurements.

Contradictions lower the dimension's confidence and are recorded in the output.
New rules are added here without touching the rest of the pipeline.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.config.rubric import Rubric
from app.pipeline.model import DimensionDraft, Verification, fmt_ts, lower_confidence

_MMSS = re.compile(r"\b(\d{1,2}):(\d{2}(?:\.\d+)?)\b")
_SECONDS = re.compile(r"\b(\d+(?:\.\d+)?)\s?s\b")
_QUOTE = re.compile(r"“([^”]+)”|\"([^\"]+)\"|(?<!\w)'([^']+)'(?!\w)")
_LINE_CITATION = re.compile(r"^\[(\d+(?:\.\d+)?)s\]\s*(.+)$")  # "[20.5s] ..." copied from the transcript
QUOTE_MIN_WORDS = 3  # shorter quotes ("And", "that") match anywhere
QUOTE_MIN_SIMILARITY = 0.75  # tolerates Whisper vs model wording ("gonna" / "going to")
# Quotes in these come from the frames (captions, headlines, charts), so the transcript can't check them.
FRAME_DIMENSIONS = {"on_screen_text"}


@dataclass
class BoundaryFlags:
    """What the judge said about the clip's edges (the second signal for the cap rule)."""

    starts_mid_thought: bool = False
    ends_mid_thought: bool = False


def parse_timestamps(text: str) -> list[float]:
    found = [int(m) * 60 + float(s) for m, s in _MMSS.findall(text)]
    without_mmss = _MMSS.sub(" ", text)
    found += [float(s) for s in _SECONDS.findall(without_mmss)]
    return found


def _timestamp_spans(text: str) -> list[tuple[int, int, float]]:
    spans = [(m.start(), m.end(), int(m[1]) * 60 + float(m[2])) for m in _MMSS.finditer(text)]
    masked = _MMSS.sub(lambda m: " " * len(m[0]), text)
    spans += [(m.start(), m.end(), float(m[1])) for m in _SECONDS.finditer(masked)]
    return sorted(spans)


def _tokens(text: str) -> list[str]:
    return [t for t in (re.sub(r"[^\w']", "", w).lower() for w in text.split()) if t]


def _find_quote(
    text: str, words: list[dict], min_words: int = QUOTE_MIN_WORDS, near: float | None = None
) -> tuple[int, int] | None:
    """Index range of the transcript words that best match the quote, or None if nothing matches well.

    A line can be said more than once, so with `near` the occurrence closest to that time wins.
    """
    quote = _tokens(text)
    if len(quote) < max(1, min_words) or not words:
        return None
    spoken = [re.sub(r"[^\w']", "", w["word"]).lower() for w in words]
    n = len(quote)
    matches = []
    for i in range(max(1, len(spoken) - n + 1)):
        matcher = SequenceMatcher(None, quote, spoken[i : i + n])
        ratio = matcher.ratio()
        if ratio >= QUOTE_MIN_SIMILARITY:
            # Trim the window to the words that actually matched, so it starts on the quote's first word.
            blocks = [b for b in matcher.get_matching_blocks() if b.size]
            matches.append((ratio, i + blocks[0].b, i + blocks[-1].b + blocks[-1].size))
    if not matches:
        return None
    top = max(m[0] for m in matches)
    # Only full repeats compete on distance; windows that half-overlap the phrase score lower.
    best = [m for m in matches if m[0] >= top - 0.05]
    if near is None:
        _, start, end = best[0]
    else:
        _, start, end = min(best, key=lambda m: abs(words[m[1]]["start"] - near))
    return start, end


def locate_quote(
    text: str, words: list[dict], min_words: int = QUOTE_MIN_WORDS, near: float | None = None
) -> float | None:
    """Start time of a quote in the word-level transcript; the model's own timestamps aren't trusted
    (they only serve as `near`, to pick between repeats of the same line)."""
    found = _find_quote(text, words, min_words, near)
    return round(words[found[0]]["start"], 2) if found else None


def verify(
    drafts: dict[str, DimensionDraft],
    signals: dict,
    flags: BoundaryFlags,
    rubric: Rubric,
    words: list[dict] | None = None,
) -> Verification:
    result = Verification()
    _late_start(drafts["hook"], signals, rubric, result)
    _boundary_cut(drafts["standalone_completeness"], signals, flags, rubric, result)
    _missing_evidence(drafts, signals, rubric, result)
    _quoted_evidence(drafts, words or [], rubric, result)
    return result


def _late_start(hook: DimensionDraft, signals: dict, rubric: Rubric, result: Verification) -> None:
    result.checks_run += 1
    first_word = signals.get("time_to_first_word_s")
    if first_word is None or first_word <= rubric.verify.late_start_s:
        return
    ceiling = rubric.verify.late_start_hook_ceiling
    hook.evidence.append(f"First word only at {first_word:.1f} s (measured).")
    if hook.score is not None and hook.score > ceiling:
        result.contradictions.append(
            {
                "rule": "late_start",
                "dimension": "hook",
                "message": f"First word at {first_word:.1f} s; judge scored the hook {hook.score}, capped at {ceiling}.",
            }
        )
        hook.score = ceiling
        hook.confidence = lower_confidence(hook.confidence)


def _boundary_cut(
    completeness: DimensionDraft, signals: dict, flags: BoundaryFlags, rubric: Rubric, result: Verification
) -> None:
    result.checks_run += 1
    # Two agreeing signals are required, so a good clip is never capped on one noisy signal.
    # "And"/"so" continue a sentence by themselves; "If"/"when" also open good hooks, so they
    # need a second hint: a lowercase opener (Whisper capitalises sentence starts) or the judge.
    subordinating = signals.get("first_word_is_subordinating", False)
    continues = signals.get("first_word_is_conjunction", False) and (
        not subordinating or signals.get("first_word_lowercase", False)
    )
    starts_cut = signals.get("speech_at_start", False) and (continues or flags.starts_mid_thought)
    ends_cut = signals.get("speech_at_end", False) and flags.ends_mid_thought
    if not (starts_cut or ends_cut):
        return

    reasons = []
    if starts_cut:
        first = signals.get("first_word") or "?"
        reasons.append(f"starts mid-sentence (speech already active at 0:00, opens with “{first}”)")
    if ends_cut:
        reasons.append(f"ends mid-sentence (speech still active at {fmt_ts(signals['duration_s'])})")
    result.cap_reason = "Clip " + " and ".join(reasons) + "."
    completeness.evidence.append(result.cap_reason + " (measured)")

    ceiling = rubric.verify.boundary_completeness_ceiling
    if completeness.score is not None and completeness.score > ceiling:
        result.contradictions.append(
            {
                "rule": "boundary_cut",
                "dimension": "standalone_completeness",
                "message": f"Boundary cut measured; judge scored completeness {completeness.score}, capped at {ceiling}.",
            }
        )
        completeness.score = ceiling
        completeness.confidence = lower_confidence(completeness.confidence)


def _missing_evidence(
    drafts: dict[str, DimensionDraft], signals: dict, rubric: Rubric, result: Verification
) -> None:
    result.checks_run += 1
    limit = signals["duration_s"] + rubric.verify.evidence_tolerance_s
    for draft in drafts.values():
        bad = next((t for ev in draft.evidence for t in parse_timestamps(ev) if t > limit), None)
        if bad is None:
            continue
        result.contradictions.append(
            {
                "rule": "missing_evidence",
                "dimension": draft.id,
                "message": f"Evidence cites {fmt_ts(bad)}, beyond the clip's {signals['duration_s']:.1f} s.",
            }
        )
        draft.confidence = lower_confidence(draft.confidence)


def _quoted_evidence(
    drafts: dict[str, DimensionDraft], words: list[dict], rubric: Rubric, result: Verification
) -> None:
    """Quotes must exist in the transcript, and a timestamp next to one must match where it was said."""
    result.checks_run += 1
    if not words:
        return
    tolerance = rubric.verify.quote_tolerance_s
    for draft in drafts.values():
        if draft.id in FRAME_DIMENSIONS:
            continue
        flagged = False
        for i, ev in enumerate(draft.evidence):
            cited_line = _LINE_CITATION.match(ev)
            if cited_line:
                # Written the report's way (m:ss); quoted only when it really is a transcript line.
                t, rest = float(cited_line[1]), cited_line[2].strip()
                ev = f"{fmt_ts(t)} “{rest}”" if _find_quote(rest, words) else f"{fmt_ts(t)} {rest}"
                draft.evidence[i] = ev
            spans = _timestamp_spans(ev)
            for m in _QUOTE.finditer(ev):
                quote = next(g for g in m.groups() if g)
                if len(_tokens(quote)) < QUOTE_MIN_WORDS:
                    continue
                found = _find_quote(quote, words, near=spans[0][2] if spans else None)
                if found is None:
                    result.contradictions.append(
                        {
                            "rule": "quote_not_in_transcript",
                            "dimension": draft.id,
                            "message": f"Evidence quotes “{quote}”, which is not in the transcript.",
                        }
                    )
                    flagged = True
                    continue
                said_at = words[found[0]]["start"]
                # The judge sees one timestamp per sentence, so citing the sentence start is fine.
                sentence = found[0]
                while sentence > 0 and not words[sentence - 1]["word"].strip().endswith((".", "?", "!")):
                    sentence -= 1
                low, high = words[sentence]["start"] - tolerance, words[found[1] - 1]["end"] + tolerance
                if not spans or any(low <= t <= high for _, _, t in spans):
                    continue
                start, end, cited = spans[0]
                ev = ev[:start] + fmt_ts(said_at) + ev[end:]
                draft.evidence[i] = ev
                result.contradictions.append(
                    {
                        "rule": "evidence_timestamp",
                        "dimension": draft.id,
                        "message": f"Evidence placed “{quote}” at {fmt_ts(cited)}; it is said at {fmt_ts(said_at)}. Corrected.",
                    }
                )
                flagged = True
        if flagged:
            draft.confidence = lower_confidence(draft.confidence)
