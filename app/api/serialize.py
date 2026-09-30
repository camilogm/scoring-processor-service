"""Turns a stored row into the public response. docs/report-example.json is the contract; the report renders it."""

from datetime import UTC, datetime

from app.pipeline.duration_check import check_duration
from app.pipeline.format_check import check_format

# Stored with the input for the pipeline, not part of the public contract.
_INTERNAL_INPUT_FIELDS = ("has_audio",)


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def to_response(row: dict, *, limit_s: float, source: str | None = None, deduplicated: bool = False) -> dict:
    """`limit_s` is the configured MAX_DURATION_S, shown next to the clip's length."""
    result = row["result_json"] or {}
    completed = row["status"] == "completed"
    input_facts = {k: v for k, v in row["input_json"].items() if k not in _INTERNAL_INPUT_FIELDS}
    return {
        "id": row["id"],
        "status": row["status"],
        "current_step": row["current_step"] if row["status"] == "processing" else None,
        "source": (source or "fresh") if completed else None,
        "deduplicated": deduplicated,
        "created_at": _iso(row["created_at"]),
        "finished_at": _iso(row["finished_at"]),
        "input": input_facts,
        "metadata": row["metadata_json"],
        # Derived from the probed size, so it is there from the upload on, for old analyses too.
        "format_check": check_format(input_facts.get("width") or 0, input_facts.get("height") or 0),
        "duration_check": check_duration(input_facts["duration_s"], limit_s),
        **result,
        "error": {"code": row["error_code"], "message": row["error_message"]} if row["status"] == "failed" else None,
    }
