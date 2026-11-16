"""Internal MCP client.

Tool servers are reachable only on the compose backend network. Every call is
signed with ``INTERNAL_MCP_SECRET`` and carries the tenant, job, repo allowlist
and (for GitHub only) the decrypted user token out of band.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp_common.security import TrustedContext, sign_headers


class ToolCallError(RuntimeError):
    """A tool server call failed. Messages never contain credentials."""


class ToolCaller(Protocol):
    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        context: TrustedContext,
    ) -> dict[str, Any]: ...


class MCPToolCaller:
    def __init__(
        self,
        github_url: str = "",
        rag_url: str = "",
        secret: str = "",
        timeout: float = 30.0,
        gmail_url: str = "",
        rae_url: str = "",
    ) -> None:
        if not secret:
            raise ValueError("INTERNAL_MCP_SECRET is required.")
        self._urls = {"github": github_url, "rag": rag_url, "gmail": gmail_url, "rae": rae_url}
        self._secret = secret
        self._timeout = timeout

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        context: TrustedContext,
    ) -> dict[str, Any]:
        url = self._urls.get(server)
        if not url:
            raise ToolCallError(f"No MCP endpoint is configured for '{server}'.")
        headers = sign_headers(self._secret, context)
        try:
            async with streamablehttp_client(url, headers=headers, timeout=self._timeout) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
        except Exception as err:
            raise ToolCallError(f"Tool '{server}.{tool}' is unavailable.") from err

        if result.isError:
            raise ToolCallError(f"Tool '{server}.{tool}' rejected the request.")

        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            return structured

        text = "".join(getattr(item, "text", "") for item in result.content)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as err:
            raise ToolCallError(f"Tool '{server}.{tool}' returned an invalid payload.") from err
        if not isinstance(decoded, dict):
            raise ToolCallError(f"Tool '{server}.{tool}' returned an invalid payload.")
        return decoded


def build_mcp_caller() -> MCPToolCaller | None:
    urls = {
        "github": os.environ.get("GITHUB_MCP_URL", ""),
        "rag": os.environ.get("RAG_MCP_URL", ""),
        "gmail": os.environ.get("GMAIL_MCP_URL", ""),
        "rae": os.environ.get("RAE_MCP_URL", ""),
    }
    secret = os.environ.get("INTERNAL_MCP_SECRET", "")
    if not secret or not any(urls.values()):
        return None
    return MCPToolCaller(
        github_url=urls["github"],
        rag_url=urls["rag"],
        secret=secret,
        gmail_url=urls["gmail"],
        rae_url=urls["rae"],
    )
