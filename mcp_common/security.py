"""HMAC authentication for service-to-service MCP calls.

The harness is the only trusted caller. Every call carries the tenant and job
identity in signed headers; tool servers never read a tenant from model
arguments. A token, when present, is part of the signature so it cannot be
swapped by a caller that only replays headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

MAX_CLOCK_SKEW_SECONDS = 90
MAX_ALLOWLIST_ENTRIES = 50

HEADER_USER = "x-fp-user-id"
HEADER_JOB = "x-fp-job-id"
HEADER_TIMESTAMP = "x-fp-timestamp"
HEADER_SIGNATURE = "x-fp-signature"
HEADER_TOKEN = "x-fp-token"
HEADER_ALLOWLIST = "x-fp-repo-allowlist"
HEADER_BASE = "x-fp-base-branch"

_CONTEXT: ContextVar["TrustedContext | None"] = ContextVar("free_point_mcp_context", default=None)


class InternalAuthError(Exception):
    """Raised when an internal call is unsigned, stale or tampered with."""


@dataclass(frozen=True)
class TrustedContext:
    """Identity attached by the harness, never by the model."""

    user_id: str
    job_id: str
    token: str = ""
    repo_allowlist: tuple[str, ...] = field(default_factory=tuple)
    base_branch: str = "main"


def require_context() -> TrustedContext:
    context = _CONTEXT.get()
    if context is None:
        raise InternalAuthError("No trusted call context is active.")
    return context


def _canonical(
    user_id: str,
    job_id: str,
    timestamp: str,
    token: str,
    allowlist: Sequence[str],
    base_branch: str,
) -> bytes:
    encoded_allowlist = json.dumps(sorted(allowlist), separators=(",", ":"))
    parts = ["v1", user_id, job_id, timestamp, token, encoded_allowlist, base_branch]
    return "\n".join(parts).encode("utf-8")


def _signature(secret: str, canonical: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def sign_headers(secret: str, context: TrustedContext, timestamp: int | None = None) -> dict[str, str]:
    """Build the signed header set for one internal call."""
    if not secret:
        raise InternalAuthError("Internal call secret is not configured.")
    stamp = str(int(timestamp if timestamp is not None else time.time()))
    canonical = _canonical(
        context.user_id,
        context.job_id,
        stamp,
        context.token,
        context.repo_allowlist,
        context.base_branch,
    )
    headers = {
        HEADER_USER: context.user_id,
        HEADER_JOB: context.job_id,
        HEADER_TIMESTAMP: stamp,
        HEADER_SIGNATURE: _signature(secret, canonical),
        HEADER_ALLOWLIST: json.dumps(sorted(context.repo_allowlist), separators=(",", ":")),
        HEADER_BASE: context.base_branch,
    }
    if context.token:
        headers[HEADER_TOKEN] = context.token
    return headers


def verify_headers(
    headers: Mapping[str, str],
    secret: str,
    now: int | None = None,
) -> TrustedContext:
    """Verify a signed header set and return the trusted context."""
    if not secret:
        raise InternalAuthError("Internal call secret is not configured.")

    user_id = headers.get(HEADER_USER, "")
    job_id = headers.get(HEADER_JOB, "")
    stamp = headers.get(HEADER_TIMESTAMP, "")
    signature = headers.get(HEADER_SIGNATURE, "")
    if not user_id or not job_id or not stamp or not signature:
        raise InternalAuthError("Unsigned internal call.")
    if len(user_id) > 128 or len(job_id) > 128:
        raise InternalAuthError("Invalid internal identity.")

    try:
        stamp_value = int(stamp)
    except ValueError as err:
        raise InternalAuthError("Invalid internal timestamp.") from err
    current = int(time.time()) if now is None else now
    if abs(current - stamp_value) > MAX_CLOCK_SKEW_SECONDS:
        raise InternalAuthError("Stale internal call.")

    token = headers.get(HEADER_TOKEN, "")
    if len(token) > 4096:
        raise InternalAuthError("Invalid internal token.")
    base_branch = headers.get(HEADER_BASE, "main") or "main"
    if not _valid_branch(base_branch):
        raise InternalAuthError("Invalid base branch.")

    try:
        raw_allowlist = json.loads(headers.get(HEADER_ALLOWLIST, "[]"))
    except json.JSONDecodeError as err:
        raise InternalAuthError("Invalid repository allowlist.") from err
    if not isinstance(raw_allowlist, list) or len(raw_allowlist) > MAX_ALLOWLIST_ENTRIES:
        raise InternalAuthError("Invalid repository allowlist.")
    allowlist = tuple(entry for entry in raw_allowlist if isinstance(entry, str))
    if len(allowlist) != len(raw_allowlist):
        raise InternalAuthError("Invalid repository allowlist.")

    canonical = _canonical(user_id, job_id, stamp, token, allowlist, base_branch)
    if not hmac.compare_digest(signature, _signature(secret, canonical)):
        raise InternalAuthError("Invalid internal signature.")

    return TrustedContext(
        user_id=user_id,
        job_id=job_id,
        token=token,
        repo_allowlist=allowlist,
        base_branch=base_branch,
    )


def _valid_branch(value: str) -> bool:
    if not value or len(value) > 120:
        return False
    if value in {"main", "master"}:
        return True
    return all(ch.isalnum() or ch in "._/-" for ch in value) and ".." not in value


class InternalAuthMiddleware:
    """ASGI middleware rejecting unsigned calls to the MCP endpoint."""

    def __init__(self, app: Callable[..., Awaitable[Any]], secret: str) -> None:
        self.app = app
        self.secret = secret

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http" or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        try:
            context = verify_headers(headers, self.secret)
        except InternalAuthError:
            await _json_response(send, 401, {"error": "unauthorized"})
            return

        reset = _CONTEXT.set(context)
        try:
            await self.app(scope, receive, send)
        finally:
            _CONTEXT.reset(reset)


async def _json_response(send: Callable[..., Any], status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def build_http_app(mcp: Any, secret: str, service: str) -> Any:
    """Expose a FastMCP streamable-HTTP app with health and internal auth."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    app = mcp.streamable_http_app()

    async def health(_: Any) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": service})

    app.router.routes.append(Route("/health", health, methods=["GET"]))
    app.add_middleware(InternalAuthMiddleware, secret=secret)
    return app
