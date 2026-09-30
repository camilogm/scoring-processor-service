"""HTTP Basic auth for deployments: enough to keep a test deployment private, not user management."""

import base64
import binascii
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.errors import _body
from app.settings import Settings

# The platform health check (fly.toml) can't send credentials.
OPEN_PATHS = frozenset({"/health"})
REALM = "clip-scoring"


def _credentials(header: str) -> tuple[str, str] | None:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        user, sep, password = base64.b64decode(encoded, validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user, password) if sep else None


def install_basic_auth(app: FastAPI, settings: Settings) -> None:
    if not settings.basic_auth_user:
        return
    expected_user = settings.basic_auth_user.encode()
    expected_password = settings.basic_auth_password.encode()

    @app.middleware("http")
    async def _basic_auth(request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        given = _credentials(request.headers.get("authorization", ""))
        # Compare both, always, in constant time: no early exit that leaks which part was wrong.
        ok = given is not None and all([
            secrets.compare_digest(given[0].encode(), expected_user),
            secrets.compare_digest(given[1].encode(), expected_password),
        ])
        if not ok:
            return JSONResponse(
                status_code=401,
                content=_body("unauthorized", "Sign in with the deployment's credentials."),
                headers={"WWW-Authenticate": f'Basic realm="{REALM}"'},
            )
        return await call_next(request)
