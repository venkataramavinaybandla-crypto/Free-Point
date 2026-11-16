"""GitHub MCP server: read-only repository tools and a draft-PR artifact.

Only ``execute_draft_pr`` touches GitHub write APIs, and the harness registers it
exclusively for the post-approval consumer. The model-facing registry never
contains it.
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_common.security import TrustedContext, build_http_app, require_context

from .artifacts import (
    ToolInputError,
    enforce_repo_allowlist,
    execute_artifact,
    prepare_artifact,
    validate_path,
    validate_repo,
)
from .github_api import GitHubAPI

mcp = FastMCP(
    "github_mcp",
    instructions="Free Point GitHub tools. Repository writes require human approval.",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
    json_response=True,
    log_level="WARNING",
)


def _api(context: TrustedContext) -> GitHubAPI:
    if not context.token:
        raise ToolInputError("A GitHub token is not configured for this user.")
    base_url = os.environ.get("GITHUB_API_BASE_URL", "https://api.github.com")
    return GitHubAPI(context.token, base_url=base_url)


@mcp.tool(name="list_files")
async def list_files(repo: str, path: str = "", ref: str = "") -> dict[str, Any]:
    """List files and directories in a repository the user has allowlisted."""
    context = require_context()
    validate_repo(repo)
    enforce_repo_allowlist(repo, context.repo_allowlist)
    clean_path = validate_path(path) if path else ""
    async with _api(context) as api:
        return await api.list_files(repo, clean_path, ref)


@mcp.tool(name="read_file")
async def read_file(repo: str, path: str, ref: str = "") -> dict[str, Any]:
    """Read a size-capped UTF-8 file from an allowlisted repository."""
    context = require_context()
    validate_repo(repo)
    enforce_repo_allowlist(repo, context.repo_allowlist)
    async with _api(context) as api:
        return await api.read_file(repo, validate_path(path), ref)


@mcp.tool(name="create_draft_pr")
async def create_draft_pr(
    repo: str,
    title: str,
    body: str,
    diff: str,
    base: str = "",
) -> dict[str, Any]:
    """Prepare a draft PR artifact. Never calls GitHub and never writes a branch."""
    return prepare_artifact(require_context(), repo, title, body, diff, base or None)


@mcp.tool(name="execute_draft_pr")
async def execute_draft_pr(
    repo: str,
    base: str,
    head: str,
    title: str,
    body: str,
    diff: str,
) -> dict[str, Any]:
    """Create the branch and draft PR from an already-approved artifact."""
    context = require_context()
    if not context.token:
        raise ToolInputError("A GitHub token is not configured for this user.")
    artifact = {"repo": repo, "base": base, "head": head, "title": title, "body": body, "diff": diff}
    async with _api(context) as api:
        return await execute_artifact(context, artifact, api)


def create_app(secret: str) -> Any:
    return build_http_app(mcp, secret, "github-mcp")


def main() -> None:
    secret = os.environ.get("INTERNAL_MCP_SECRET", "")
    if not secret:
        raise SystemExit("INTERNAL_MCP_SECRET is required")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
