"""Gmail MCP server: inbox metadata and untrusted email text.

The server only reads. It has no send, draft, modify, delete, or attachment
code path, and the access token arrives only in the signed internal header.
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_common.security import build_http_app, require_context

from .client import MAX_RECENT, GmailClient, GmailError

mcp = FastMCP(
    "gmail_mcp",
    instructions="Read-only Gmail metadata and email text for human review.",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
    json_response=True,
    log_level="WARNING",
)


def _client(context_token: str) -> GmailClient:
    if not context_token:
        raise GmailError("Gmail is not connected for this user.")
    return GmailClient(context_token)


@mcp.tool(name="list_recent")
async def list_recent(max_results: int = MAX_RECENT) -> dict[str, Any]:
    """List recent inbox metadata (sender, subject, date). Bodies are never returned."""
    context = require_context()
    async with _client(context.token) as client:
        return await client.list_recent(max_results)


@mcp.tool(name="summarize_email")
async def summarize_email(id: str) -> dict[str, Any]:
    """Return one email's stripped text (max 2,000 chars) as untrusted data."""
    context = require_context()
    async with _client(context.token) as client:
        return await client.summarize_email(id)


def create_app(secret: str) -> Any:
    return build_http_app(mcp, secret, "gmail-mcp")


def main() -> None:
    secret = os.environ.get("INTERNAL_MCP_SECRET", "")
    if not secret:
        raise SystemExit("INTERNAL_MCP_SECRET is required")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
