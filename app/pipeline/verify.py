"""Stage 5: deterministic rules that check the judge against the measurements.

Contradictions lower the dimension's confidence and are recorded in the output.
New rules are added here without touching the rest of the pipeline.
"""

import re
from dataclasses import dataclass

from app.config.rubric import Rubric
from app.pipeline.model import DimensionDraft, Verification, fmt_ts, lower_confidence

_MMSS = re.compile(r"\b(\d{1,2}):(\d{2}(?:\.\d+)?)\b")
_SECONDS = re.compile(r"\b(\d+(?:\.\d+)?)\s?s\b")


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


def verify(
    drafts: dict[str, DimensionDraft], signals: dict, flags: BoundaryFlags, rubric: Rubric
) -> Verification:
    result = Verification()
    _late_start(drafts["hook"], signals, rubric, result)
    _boundary_cut(drafts["standalone_completeness"], signals, flags, rubric, result)
    _missing_evidence(drafts, signals, rubric, result)
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
    starts_cut = signals.get("speech_at_start", False) and (
        signals.get("first_word_is_conjunction", False) or flags.starts_mid_thought
    )
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
