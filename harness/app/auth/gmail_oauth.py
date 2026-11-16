"""Session-protected Google OAuth consent flow with state and PKCE.

Both routes require a signed-in user. The pending state + verifier live only in
an encrypted, HttpOnly, short-lived cookie. The refresh token is KMS-envelope
encrypted with context ``user_id`` + ``gmail`` and never returned to the
browser or the model.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from ..crypto.envelope import EnvelopeEncryptor
from ..mcp_client.gmail_config import (
    GmailAuthError,
    exchange_authorization_code,
    store_refresh_token,
)
from .dependencies import AuthenticatedUser, require_session

gmail_oauth_router = APIRouter(tags=["Gmail OAuth"])
OAUTH_COOKIE = "gmail_oauth"
OAUTH_TTL_SECONDS = 600
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class OAuthStateError(RuntimeError):
    """The OAuth state cookie is missing, stale, or mismatched."""


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _encrypt_state(user_id: str, payload: dict[str, Any]) -> str:
    return EnvelopeEncryptor().encrypt(
        json.dumps(payload, separators=(",", ":")),
        user_id=user_id,
        tool="google_oauth_state",
    )


def _decrypt_state(user_id: str, cookie: str, expected_state: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            EnvelopeEncryptor().decrypt(cookie, user_id=user_id, tool="google_oauth_state")
        )
    except ValueError as err:
        raise OAuthStateError("OAuth state could not be read.") from err
    if not isinstance(payload, dict):
        raise OAuthStateError("OAuth state is invalid.")
    state = str(payload.get("state", ""))
    verifier = str(payload.get("verifier", ""))
    created_at = int(payload.get("created_at", 0))
    if not state or not verifier or abs(int(time.time()) - created_at) > OAUTH_TTL_SECONDS:
        raise OAuthStateError("OAuth state expired.")
    if not hmac.compare_digest(state, expected_state):
        raise OAuthStateError("OAuth state mismatch.")
    return payload


def _oauth_settings() -> tuple[str, str, str]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
    if not client_id or not redirect_uri or not os.environ.get("GOOGLE_CLIENT_SECRET", ""):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured.",
        )
    return client_id, redirect_uri, os.environ.get("GOOGLE_CLIENT_SECRET", "")


@gmail_oauth_router.get("/oauth/google/start")
async def google_oauth_start(current_user: AuthenticatedUser = Depends(require_session)) -> RedirectResponse:
    """Start consent with an encrypted state cookie and an S256 PKCE verifier."""
    client_id, redirect_uri, _ = _oauth_settings()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    cookie = _encrypt_state(
        current_user.user_id,
        {"state": state, "verifier": verifier, "created_at": int(time.time())},
    )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=302)
    response.set_cookie(
        key=OAUTH_COOKIE,
        value=cookie,
        max_age=OAUTH_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/oauth/google",
    )
    return response


@gmail_oauth_router.get("/oauth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    current_user: AuthenticatedUser = Depends(require_session),
) -> RedirectResponse:
    """Verify state, exchange the code with PKCE, and store the refresh token."""
    if error or not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google consent was cancelled.")
    cookie = request.cookies.get(OAUTH_COOKIE, "")
    if not cookie:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state is missing.")
    try:
        payload = _decrypt_state(current_user.user_id, cookie, state)
        _, redirect_uri, _ = _oauth_settings()
        refresh_token = await exchange_authorization_code(code, str(payload["verifier"]), redirect_uri)
        await store_refresh_token(current_user.user_id, refresh_token)
    except OAuthStateError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except GmailAuthError as err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(err)) from err

    response = RedirectResponse("/settings?gmail=connected", status_code=303)
    response.delete_cookie(OAUTH_COOKIE, path="/oauth/google")
    return response
