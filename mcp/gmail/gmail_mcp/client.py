"""Minimal read-only Gmail REST client.

Only ``messages.list`` and ``messages.get`` are implemented. There is no send,
draft, modify, delete, or attachment path anywhere in this package.
"""

from __future__ import annotations

import base64
import binascii
import html
import os
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

MAX_RECENT = 10
MAX_TEXT_CHARS = 2000
MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
DEFAULT_BASE_URL = "https://gmail.googleapis.com/gmail/v1"


class GmailError(RuntimeError):
    """A sanitized Gmail API failure."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return html.unescape("".join(self.parts))


def strip_html(value: str) -> str:
    """Convert HTML to normalized plain text without scripts or styles."""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", parser.text())).strip()


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, binascii.Error):
        return ""


def _payload_text(payload: dict[str, Any]) -> tuple[str, bool]:
    """Prefer text/plain, fall back to stripped text/html."""
    plain: list[str] = []
    html_parts: list[str] = []
    stack = [payload]
    while stack:
        part = stack.pop()
        mime = str(part.get("mimeType", ""))
        body = part.get("body") or {}
        data = str(body.get("data", "")) if isinstance(body, dict) else ""
        if data and mime == "text/plain":
            plain.append(_decode_body(data))
        elif data and mime == "text/html":
            html_parts.append(strip_html(_decode_body(data)))
        parts = part.get("parts") or []
        if isinstance(parts, list):
            stack.extend(item for item in parts if isinstance(item, dict))
    if plain:
        return "\n".join(plain), False
    if html_parts:
        return "\n".join(html_parts), True
    return "", False


class GmailClient:
    def __init__(self, token: str, base_url: str | None = None, timeout: float = 15.0) -> None:
        if not token:
            raise GmailError("Gmail is not connected for this user.")
        self._client = httpx.AsyncClient(
            base_url=base_url or os.environ.get("GMAIL_API_BASE_URL", DEFAULT_BASE_URL),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def __aenter__(self) -> "GmailClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        if response.status_code == 401:
            raise GmailError("Gmail authorization expired. Reconnect the account.")
        if response.status_code == 403:
            raise GmailError("Gmail refused the read-only request.")
        if response.status_code == 404:
            raise GmailError("Email not found.")
        if response.status_code >= 400:
            raise GmailError(f"Gmail request failed ({response.status_code}).")
        data = response.json()
        if not isinstance(data, dict):
            raise GmailError("Gmail returned an invalid response.")
        return data

    async def list_recent(self, max_results: int = MAX_RECENT) -> dict[str, Any]:
        """Return metadata only, never bodies, capped at ten messages."""
        limit = max(1, min(int(max_results), MAX_RECENT))
        listing = await self._get(
            "/users/me/messages",
            params={"labelIds": "INBOX", "maxResults": limit},
        )
        messages = listing.get("messages") or []
        if not isinstance(messages, list):
            raise GmailError("Gmail returned an invalid message list.")
        entries: list[dict[str, str]] = []
        for item in messages[:limit]:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("id", ""))
            if not MESSAGE_ID_PATTERN.fullmatch(message_id):
                continue
            message = await self._get(
                f"/users/me/messages/{quote(message_id, safe='')}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            headers = {
                str(header.get("name", "")).lower(): str(header.get("value", ""))
                for header in (message.get("payload", {}).get("headers") or [])
                if isinstance(header, dict)
            }
            entries.append(
                {
                    "id": message_id,
                    "thread_id": str(message.get("threadId", "")),
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "date": headers.get("date", ""),
                }
            )
        return {"messages": entries, "count": len(entries), "truncated": len(messages) > limit}

    async def summarize_email(self, message_id: str) -> dict[str, Any]:
        """Fetch one message, strip HTML, and truncate to 2,000 characters."""
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            raise GmailError("Invalid message id.")
        message = await self._get(
            f"/users/me/messages/{quote(message_id, safe='')}",
            params={"format": "full"},
        )
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise GmailError("Gmail returned an invalid message.")
        text, _ = _payload_text(payload)
        truncated = len(text) > MAX_TEXT_CHARS
        return {
            "id": message_id,
            "untrusted_data": text[:MAX_TEXT_CHARS],
            "truncated": truncated,
        }
