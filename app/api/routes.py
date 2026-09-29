import hashlib
import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.api.errors import ApiError
from app.api.schemas import Analysis, AnalysisList, Health, Metadata, error_example
from app.api.serialize import to_response
from app.pipeline import validate
from app.pipeline.judge import PROMPT_VERSION
from app.report.render import render_demo, render_report
from app.settings import PIPELINE_VERSION

router = APIRouter()

CHUNK = 1024 * 1024
# Metadata fields that change the analysis (they reach the judge). Everything else is bookkeeping.
ANALYSIS_FIELDS = ("title", "platform")

METADATA_EXAMPLE = '{"title": "Why permit approvals take so long", "account": "example_account", "platform": "tiktok", "external_id": "C07"}'
SERVER_ERROR = error_example(500, "internal_error", "Unexpected server error.", "Unexpected server error.")
NOT_FOUND = error_example(404, "not_found", "No analysis with id an_nope.", "Unknown analysis ID.")


def _validated(body: dict) -> dict:
    """Responses built by hand (POST) go through the same contract check as `response_model`."""
    return Analysis.model_validate(body).model_dump(mode="json", exclude_unset=True)


def _parse_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return Metadata.model_validate(json.loads(raw)).model_dump(exclude_none=True)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ApiError(400, "invalid_metadata", f"metadata must be a JSON object with string fields: {exc}") from exc


async def _save_upload(upload: UploadFile, dest_dir: Path, max_bytes: int) -> tuple[Path, str, int, bytes]:
    """Stream to a temp file while hashing; fsync so the file is on disk before we report anything."""
    sha = hashlib.sha256()
    size = 0
    head = b""
    fd, tmp_name = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(413, "file_too_large", f"The file exceeds {max_bytes // CHUNK} MB.")
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                sha.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, sha.hexdigest(), size, head


def _cache_key(file_sha: str, metadata: dict, model: str, rubric_version: str) -> str:
    relevant = {k: metadata.get(k) for k in ANALYSIS_FIELDS}
    material = json.dumps(
        [file_sha, relevant, model, PROMPT_VERSION, rubric_version, PIPELINE_VERSION], sort_keys=True
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _get_row(request: Request, analysis_id: str) -> dict:
    row = request.app.state.store.get(analysis_id)
    if row is None:
        raise ApiError(404, "not_found", f"No analysis with id {analysis_id}.")
    return row


@router.get("/health", tags=["ops"], summary="Liveness check", response_model=Health)
def health():
    return {"status": "ok"}


@router.post(
    "/analyses",
    tags=["analyses"],
    summary="Upload a clip for analysis",
    status_code=202,
    response_model=Analysis,
    response_model_exclude_unset=True,
    responses={
        202: {"description": "Stored as a new analysis and queued. Poll `GET /analyses/{id}`."},
        200: {
            "model": Analysis,
            "description": "The same clip is already queued, processing or completed: that analysis is returned "
            "with `deduplicated: true` and no new model cost.",
        },
        **error_example(400, "invalid_metadata", "metadata must be a JSON object with string fields.",
                        "Missing file or malformed metadata."),
        **error_example(413, "file_too_large", "The file exceeds 200 MB.", "File too large."),
        **error_example(415, "unsupported_media_type", "Only MP4 files are accepted.", "Not an MP4."),
        **error_example(422, "too_long", "The clip is 312 s; the limit is 240 s.",
                        "Valid MP4 but unusable: `unreadable_video`, `no_video_stream` or `too_long`."),
        **SERVER_ERROR,
    },
)
async def create_analysis(
    request: Request,
    file: UploadFile | None = File(None, description="The MP4 clip."),
    metadata: str | None = Form(
        None,
        description="JSON object as a string. All fields optional; `title` and `platform` reach the judge, "
        "`external_id` is returned unchanged and never affects deduplication.",
        examples=[METADATA_EXAMPLE],
    ),
    fresh: bool = Query(False, description="Force a new analysis even if an identical one exists (repeatability tests)."),
):
    """Validates and stores the clip, then analyses it in the background. The clip is never ranked against others."""
    settings = request.app.state.settings
    store = request.app.state.store
    rubric = request.app.state.rubric

    if file is None:
        raise ApiError(400, "missing_file", "Send the video as the multipart field 'file'.")
    meta = _parse_metadata(metadata)

    tmp, file_sha, size, head = await _save_upload(file, settings.uploads_dir, settings.max_upload_mb * CHUNK)
    try:
        # MP4 / ISO BMFF files carry 'ftyp' at byte 4, whatever the extension or content type says.
        if head[4:8] != b"ftyp":
            raise ApiError(415, "unsupported_media_type", "Only MP4 files are accepted.")
        try:
            info = await run_in_threadpool(validate.probe, tmp)
        except validate.ProbeError as exc:
            raise ApiError(422, exc.code, exc.message) from exc
        if info.duration_s > settings.max_duration_s:
            raise ApiError(
                422, "too_long", f"The clip is {info.duration_s:.0f} s; the limit is {settings.max_duration_s:.0f} s."
            )
        final = settings.uploads_dir / f"{file_sha}.mp4"
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)

    row, created = store.create_or_get(
        cache_key=_cache_key(file_sha, meta, settings.llm_model, rubric.version),
        fresh=fresh,
        file_sha256=file_sha,
        file_path=str(final),
        external_id=meta.get("external_id"),
        input_facts={
            "filename": file.filename,
            "sha256": file_sha,
            "size_bytes": size,
            "duration_s": round(info.duration_s, 2),
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "has_audio": info.has_audio,
        },
        metadata=meta,
        model_id=settings.llm_model,
        prompt_version=PROMPT_VERSION,
        rubric_version=rubric.version,
        pipeline_version=PIPELINE_VERSION,
    )
    if created:
        request.app.state.worker.submit(row["id"])
        return JSONResponse(status_code=202, content=_validated(to_response(row, deduplicated=False)))

    return JSONResponse(
        status_code=200, content=_validated(to_response(row, source="cached", deduplicated=True))
    )


@router.get(
    "/analyses",
    tags=["analyses"],
    summary="List recent analyses",
    response_model=AnalysisList,
    response_model_exclude_unset=True,
    responses={**SERVER_ERROR},
)
def list_analyses(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="How many analyses to return, newest first."),
):
    return {"items": [to_response(row) for row in request.app.state.store.list_recent(limit)]}


@router.get(
    "/analyses/{analysis_id}",
    tags=["analyses"],
    summary="Get an analysis: status, result or failure reason",
    response_model=Analysis,
    response_model_exclude_unset=True,
    responses={
        200: {"description": "Found. A `failed` analysis is still a 200: the request worked, the analysis didn't."},
        **NOT_FOUND,
        **SERVER_ERROR,
    },
)
def get_analysis(request: Request, analysis_id: str):
    return to_response(_get_row(request, analysis_id))


@router.get(
    "/analyses/{analysis_id}/report",
    tags=["analyses"],
    summary="Human-readable HTML report",
    response_class=HTMLResponse,
    responses={
        200: {"description": "The report, or a status page while queued, processing or failed."},
        **NOT_FOUND,
        **SERVER_ERROR,
    },
)
def get_report(request: Request, analysis_id: str):
    return HTMLResponse(render_report(to_response(_get_row(request, analysis_id))))


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo():
    """Minimal page for reviewers: upload a clip, watch the list, open reports. Uses only the public API."""
    return HTMLResponse(render_demo())
