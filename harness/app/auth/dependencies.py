"""Authentication dependencies for API keys and web sessions."""

import os
import time
from typing import Any, Dict, NamedTuple, Optional

from fastapi import Header, HTTPException, Request, Response, status

from ..store.factory import get_api_key_store, get_session_store, get_usage_store, get_user_store
from .passwords import constant_time_compare
from .tokens import hash_sha256


class AuthenticatedKey(NamedTuple):
    user_id: str
    key_id: str
    prefix: str


class AuthenticatedUser(NamedTuple):
    user_id: str
    email: str
    session_hash: str
    csrf_token: str


_IP_RATE_TRACKER: Dict[str, Dict[str, Any]] = {}


def reset_ip_tracker() -> None:
    """Clear IP rate tracking records (for test isolation)."""
    _IP_RATE_TRACKER.clear()


def check_ip_rate_limit(request: Request, action: str, max_attempts: int = 10, window_seconds: int = 60) -> None:
    """Enforce rate limits per client IP for signup and login."""
    client_ip = "unknown"
    if request.client and request.client.host:
        client_ip = request.client.host

    tracker_key = f"{action}:{client_ip}"
    now = time.time()
    record = _IP_RATE_TRACKER.get(tracker_key)

    if record is None or (now - record["start_time"]) > window_seconds:
        _IP_RATE_TRACKER[tracker_key] = {"count": 1, "start_time": now}
        return

    record["count"] += 1
    if record["count"] > max_attempts:
        retry_after = max(1, int(window_seconds - (now - record["start_time"])))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many {action} attempts from this IP. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


async def require_api_key(
    request: Request,
    response: Response,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> AuthenticatedKey:
    """FastAPI dependency for /v1 routes requiring a valid API key.

    Enforces:
    - Format: fp_<prefix>_<secret>
    - Rejects keys on session routes and sessions on key routes
    - Prefix lookup + constant-time hash verification
    - Revocation status
    - Per-key fixed-window rate limit and daily quota with headers
    """
    raw_key = x_api_key
    if not raw_key and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_key = parts[1]
        else:
            raw_key = authorization

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required for /v1 endpoints. Provide via Authorization: Bearer or X-API-Key header.",
        )

    # Format check: fp_<prefix>_<secret>
    if not raw_key.startswith("fp_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format. Expected fp_<prefix>_<secret>.",
        )

    parts = raw_key.split("_")
    if len(parts) < 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key structure.",
        )

    prefix = parts[1]
    api_key_store = get_api_key_store()
    key_doc = await api_key_store.get_key_by_prefix(prefix)

    if not key_doc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )

    if key_doc.get("revoked", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has been revoked.",
        )

    computed_hash = hash_sha256(raw_key)
    if not constant_time_compare(key_doc["key_hash"], computed_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )

    # Update last_used
    now_ts = int(time.time())
    await api_key_store.update_last_used(key_doc["key_id"], now_ts)

    # Rate limiting & quota
    rate_limit = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    daily_quota = int(os.environ.get("DAILY_QUOTA", "1000"))
    usage_store = get_usage_store()

    rate_exceeded, quota_exceeded, info = await usage_store.record_and_check(
        key_id=key_doc["key_id"], rate_limit=rate_limit, daily_quota=daily_quota
    )

    # Prepare standard rate limit and quota headers
    headers = {
        "X-RateLimit-Limit": str(info["rate_limit"]),
        "X-RateLimit-Remaining": str(info["rate_remaining"]),
        "X-RateLimit-Reset": str(info["rate_reset"]),
        "X-Quota-Remaining": str(info["quota_remaining"]),
    }

    if rate_exceeded:
        headers["Retry-After"] = str(info["retry_after"])
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded for this API key.",
            headers=headers,
        )

    if quota_exceeded:
        headers["Retry-After"] = str(info["retry_after"])
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily quota exceeded for this API key.",
            headers=headers,
        )

    # Apply headers to outgoing response
    for k, v in headers.items():
        response.headers[k] = v

    auth_key = AuthenticatedKey(user_id=key_doc["user_id"], key_id=key_doc["key_id"], prefix=prefix)
    request.state.api_key = auth_key
    return auth_key


async def require_session(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token"),
) -> AuthenticatedUser:
    """FastAPI dependency for web session routes.

    Enforces:
    - Rejects API keys on session routes
    - Requires session_id cookie
    - Constant-time validation of session and CSRF double-submit token
    """
    # Strict isolation: Keys never work on session routes
    raw_key = x_api_key
    if not raw_key and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_key = parts[1]
        else:
            raw_key = authorization

    if raw_key and raw_key.startswith("fp_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API keys are not permitted on session routes. Web session required.",
        )

    raw_session_id = request.cookies.get("session_id")
    if not raw_session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication session required.",
        )

    session_hash = hash_sha256(raw_session_id)
    session_store = get_session_store()
    session = await session_store.get_session(session_hash)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid.",
        )

    user_store = get_user_store()
    user = await user_store.get_user_by_id(session["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with session not found.",
        )

    # CSRF double-submit validation on state-changing routes
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if not x_csrf_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing CSRF token in X-CSRF-Token header.",
            )
        cookie_csrf = request.cookies.get("csrf_token")
        if cookie_csrf and not constant_time_compare(x_csrf_token, cookie_csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token mismatch with cookie.",
            )
        if not constant_time_compare(x_csrf_token, session["csrf_token"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token mismatch with session.",
            )

    auth_user = AuthenticatedUser(
        user_id=user["id"],
        email=user["email"],
        session_hash=session_hash,
        csrf_token=session["csrf_token"],
    )
    request.state.user = auth_user
    return auth_user


async def get_api_key(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[str]:
    """Backward-compatible helper for legacy callers."""
    key = x_api_key
    if not key and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            key = parts[1]
        else:
            key = authorization
    return key
