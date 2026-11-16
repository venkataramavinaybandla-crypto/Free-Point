"""Gmail refresh-token storage and per-call access-token minting.

The refresh token is encrypted with EncryptionContext ``user_id`` + ``gmail``.
The harness decrypts it for a single tool call, exchanges it for an access
token, and puts only the access token in the signed header. Nothing is exposed
as a model argument or written to logs.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..crypto.envelope import EnvelopeEncryptor
from ..store.factory import get_secret_store

SECRET_NAME = "gmail"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailAuthError(RuntimeError):
    """Gmail authorization could not be loaded or refreshed."""


async def store_refresh_token(user_id: str, refresh_token: str) -> None:
    if not refresh_token:
        raise GmailAuthError("Google did not return a refresh token.")
    encrypted = EnvelopeEncryptor().encrypt(refresh_token, user_id=user_id, tool=SECRET_NAME)
    await get_secret_store().set_secret(user_id, SECRET_NAME, encrypted, updated_at=_now())


async def load_refresh_token(
    user_id: str,
    encryptor: EnvelopeEncryptor | None = None,
    secret_store: Any = None,
) -> str:
    store = secret_store or get_secret_store()
    encrypted = await store.get_secret(user_id, SECRET_NAME)
    if not encrypted:
        raise GmailAuthError("Gmail is not connected for this user.")
    try:
        token = (encryptor or EnvelopeEncryptor()).decrypt(encrypted, user_id=user_id, tool=SECRET_NAME)
    except ValueError as err:
        raise GmailAuthError("Gmail authorization could not be read.") from err
    if not token:
        raise GmailAuthError("Gmail authorization is empty.")
    return token


async def refresh_access_token(refresh_token: str) -> str:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise GmailAuthError("Google OAuth is not configured.")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
    except httpx.HTTPError as err:
        raise GmailAuthError("Google token refresh failed.") from err
    if response.status_code >= 400:
        raise GmailAuthError("Google token refresh was rejected.")
    data = response.json()
    token = str(data.get("access_token", "")) if isinstance(data, dict) else ""
    if not token:
        raise GmailAuthError("Google returned no access token.")
    return token


async def load_gmail_access_token(user_id: str) -> str:
    """Decrypt the refresh token for this call, then mint an access token."""
    return await refresh_access_token(await load_refresh_token(user_id))


async def exchange_authorization_code(code: str, code_verifier: str, redirect_uri: str) -> str:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise GmailAuthError("Google OAuth is not configured.")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
    except httpx.HTTPError as err:
        raise GmailAuthError("Google authorization exchange failed.") from err
    if response.status_code >= 400:
        raise GmailAuthError("Google authorization exchange was rejected.")
    data = response.json()
    refresh_token = str(data.get("refresh_token", "")) if isinstance(data, dict) else ""
    if not refresh_token:
        raise GmailAuthError("Google returned no refresh token. Grant offline access again.")
    return refresh_token


def _now() -> int:
    import time

    return int(time.time())
