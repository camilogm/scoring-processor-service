"""Stage 6: overall score, cap rule and verdict. Always code, never the model."""

from app.config.rubric import Rubric
from app.pipeline.model import DimensionDraft

MAX_PRIORITY_FIXES = 3


def verdict_for(score: float, rubric: Rubric) -> str:
    for verdict in ("post", "improve"):
        if score >= rubric.verdicts[verdict].min_score:
            return verdict
    return "skip"


def compute_scores(drafts: list[DimensionDraft], rubric: Rubric, cap_reason: str | None) -> dict:
    applicable = [d for d in drafts if d.applicable and d.score is not None]
    total_weight = sum(d.weight for d in applicable)

    dimensions = []
    for d in drafts:
        is_applicable = d in applicable
        # Non-applicable dimensions drop out; the rest are rescaled to add up to 100%.
        weight = d.weight / total_weight if is_applicable and total_weight else 0.0
        gap = weight * (10 - d.score) if is_applicable else 0.0
        dimensions.append(
            {
                "id": d.id,
                "label": d.label,
                "score": d.score if is_applicable else None,
                "weight": round(weight, 4),
                "weighted_gap": round(gap, 2),
                "confidence": d.confidence,
                "basis": d.basis,
                "applicable": is_applicable,
                "evidence": d.evidence,
                "fix": d.fix,
            }
        )

    raw = sum(d["score"] * d["weight"] for d in dimensions if d["applicable"]) if total_weight else 0.0
    capped = cap_reason is not None
    final = min(raw, rubric.cap.max_score) if capped else raw
    score = round(final, 1)
    verdict = verdict_for(score, rubric)

    ranked = sorted(
        (d for d in dimensions if d["applicable"] and d["fix"] and d["weighted_gap"] > 0),
        key=lambda d: (-d["weighted_gap"], -d["weight"]),
    )
    priority_fixes = [
        {
            "rank": i,
            "dimension": d["id"],
            "label": d["label"],
            "fix": d["fix"],
            "weighted_gap": d["weighted_gap"],
        }
        for i, d in enumerate(ranked[:MAX_PRIORITY_FIXES], start=1)
    ]

    return {
        "overall": {
            "score": score,
            "raw_score": round(raw, 4),
            "verdict": verdict,
            "verdict_label": rubric.verdicts[verdict].label,
            "capped": capped,
            "cap_reason": cap_reason,
        },
        "dimensions": dimensions,
        "priority_fixes": priority_fixes,
    }
