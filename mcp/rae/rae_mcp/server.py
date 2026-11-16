"""RAE MCP server: static analysis only, with chunked untrusted output."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_common.security import build_http_app, require_context

from .sandbox import SandboxError
from .service import RaeService

mcp = FastMCP(
    "rae_mcp",
    instructions="Static analysis of binaries and repositories. Never executes the target.",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
    json_response=True,
    log_level="WARNING",
)


@lru_cache(maxsize=1)
def _service() -> RaeService:
    return RaeService()


def _wrap(payload: dict[str, Any]) -> dict[str, Any]:
    return {"untrusted_data": payload}


@mcp.tool(name="analyze_binary")
async def analyze_binary(key: str) -> dict[str, Any]:
    """Analyze an S3 artifact: type, hashes, strings, headers, imports, entropy."""
    context = require_context()
    try:
        result = await _service().analyze_s3_key(context.user_id, key)
    except SandboxError as err:
        raise ValueError(str(err)) from err
    return _wrap(result)


@mcp.tool(name="analyze_repo")
async def analyze_repo(repo_url: str) -> dict[str, Any]:
    """Clone depth-1 safely and map languages, files, and dependency manifests."""
    context = require_context()
    try:
        result = await _service().analyze_repository(context.user_id, repo_url)
    except SandboxError as err:
        raise ValueError(str(err)) from err
    return _wrap(result)


@mcp.tool(name="get_chunk")
async def get_chunk(n: int) -> dict[str, Any]:
    """Return one analysis chunk by index; chunks are capped for a 4k context."""
    context = require_context()
    try:
        result = _service().get_chunk(context.user_id, n)
    except SandboxError as err:
        raise ValueError(str(err)) from err
    return _wrap(result)


def create_app(secret: str) -> Any:
    return build_http_app(mcp, secret, "rae-mcp")


def main() -> None:
    secret = os.environ.get("INTERNAL_MCP_SECRET", "")
    if not secret:
        raise SystemExit("INTERNAL_MCP_SECRET is required")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
