"""OpenAPI / Swagger setup. The docs must describe what the API really returns."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

DESCRIPTION = """
Scores short-form clips (TikTok / Instagram, 1–2 min) and says whether each one is ready to **post**,
needs to be **improved**, or should be **skipped**, with a score, evidence and a fix per dimension.

**Flow:** `POST /analyses` stores the clip and returns `202` with an ID → poll `GET /analyses/{id}`
until `status` is `completed` or `failed` → optionally open `GET /analyses/{id}/report`.

Errors always have the shape `{"error": {"code": "...", "message": "..."}}`.
The full example response lives in `docs/report-example.json`.
"""

TAGS = [
    {"name": "analyses", "description": "Upload clips and read their analyses."},
    {"name": "ops", "description": "Operational endpoints."},
]

_FRAMEWORK_VALIDATION = "#/components/schemas/HTTPValidationError"
_ERROR_SCHEMA = "#/components/schemas/ErrorResponse"


def _errors_as_json(op: dict) -> None:
    """On an HTML route FastAPI files error models under text/html; errors are always JSON."""
    for resp in op.get("responses", {}).values():
        content = resp.get("content", {})
        if content.get("text/html", {}).get("schema", {}).get("$ref") == _ERROR_SCHEMA:
            del content["text/html"]
            content.setdefault("application/json", {})["schema"] = {"$ref": _ERROR_SCHEMA}


def install_openapi(app: FastAPI) -> None:
    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=DESCRIPTION, routes=app.routes, tags=TAGS
        )
        # FastAPI advertises a 422 HTTPValidationError on every route with parameters, but our handler
        # answers request validation errors with 400 invalid_request. Drop the fake entry.
        for operations in schema["paths"].values():
            for op in operations.values():
                resp = op.get("responses", {}).get("422", {})
                ref = resp.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
                if ref == _FRAMEWORK_VALIDATION:
                    del op["responses"]["422"]
                _errors_as_json(op)
        for name in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(name, None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
