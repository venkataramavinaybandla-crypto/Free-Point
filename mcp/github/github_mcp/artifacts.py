"""Draft-PR artifacts and the approved execute path.

``prepare_artifact`` performs no network I/O: a model can only ever produce a
reviewable artifact. ``execute_artifact`` is reachable through a separate MCP
tool that the model registry never exposes; it creates a draft PR from the
stored artifact and is safe to retry.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from mcp_common.branches import PROTECTED_BRANCHES, branch_for_job
from mcp_common.security import TrustedContext

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
MAX_TITLE = 200
MAX_BODY = 20_000
MAX_DIFF = 200_000


class ToolInputError(ValueError):
    """Invalid or policy-violating tool input."""


class GitHubExecutorAPI(Protocol):
    async def get_ref_sha(self, repo: str, branch: str) -> str | None: ...

    async def get_commit_tree(self, repo: str, sha: str) -> str: ...

    async def create_blob(self, repo: str, content: str) -> str: ...

    async def create_tree(self, repo: str, base_tree: str, path: str, blob_sha: str) -> str: ...

    async def create_commit(self, repo: str, message: str, tree: str, parents: list[str]) -> str: ...

    async def create_ref(self, repo: str, branch: str, sha: str) -> None: ...

    async def find_pull(self, repo: str, head: str) -> dict[str, Any] | None: ...

    async def create_pull(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]: ...


def validate_repo(repo: str) -> str:
    if len(repo) > 140 or not REPO_PATTERN.fullmatch(repo):
        raise ToolInputError("Repository must be in owner/name form.")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ToolInputError("Invalid repository.")
    return repo


def validate_path(path: str) -> str:
    if len(path) > 400 or path.startswith("/") or "\\" in path:
        raise ToolInputError("Invalid repository path.")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ToolInputError("Invalid repository path.")
    return path


def validate_branch(branch: str) -> str:
    if (
        not branch
        or len(branch) > 120
        or not BRANCH_PATTERN.fullmatch(branch)
        or ".." in branch
        or branch.startswith("/")
        or branch.endswith("/")
        or "//" in branch
    ):
        raise ToolInputError("Invalid branch name.")
    return branch


def enforce_repo_allowlist(repo: str, allowlist: tuple[str, ...]) -> None:
    if repo not in allowlist:
        raise ToolInputError("Repository is not in the user's allowlist.")


def prepare_artifact(
    context: TrustedContext,
    repo: str,
    title: str,
    body: str,
    diff: str,
    base: str | None = None,
) -> dict[str, Any]:
    """Create the reviewable payload without calling GitHub."""
    validate_repo(repo)
    enforce_repo_allowlist(repo, context.repo_allowlist)
    base_branch = validate_branch(base or context.base_branch)
    if not title.strip() or len(title) > MAX_TITLE:
        raise ToolInputError(f"Title must be 1-{MAX_TITLE} characters.")
    if len(body) > MAX_BODY:
        raise ToolInputError(f"Body must be at most {MAX_BODY} characters.")
    if not diff.strip() or len(diff) > MAX_DIFF:
        raise ToolInputError(f"Diff must be 1-{MAX_DIFF} characters.")
    if base_branch in PROTECTED_BRANCHES and base_branch != context.base_branch:
        raise ToolInputError("Base branch is not the configured target branch.")
    head = branch_for_job(context.job_id)
    if base_branch == head:
        raise ToolInputError("Base branch cannot equal the artifact branch.")
    return {
        "repo": repo,
        "base": base_branch,
        "head": head,
        "title": title,
        "body": body,
        "diff": diff,
    }


async def execute_artifact(
    context: TrustedContext,
    artifact: dict[str, Any],
    api: GitHubExecutorAPI,
) -> dict[str, Any]:
    """Create the branch and draft PR from an approved artifact, idempotently."""
    repo = validate_repo(str(artifact.get("repo", "")))
    enforce_repo_allowlist(repo, context.repo_allowlist)
    base = validate_branch(str(artifact.get("base", "")))
    head = validate_branch(str(artifact.get("head", "")))
    title = str(artifact.get("title", ""))
    body = str(artifact.get("body", ""))
    diff = str(artifact.get("diff", ""))

    expected_head = branch_for_job(context.job_id)
    if head != expected_head:
        raise ToolInputError("Artifact branch does not match this job.")
    if head in PROTECTED_BRANCHES or head.split("/")[-1] in PROTECTED_BRANCHES:
        raise ToolInputError("Refusing to target a protected branch.")
    if not title or len(title) > MAX_TITLE or len(body) > MAX_BODY or not diff or len(diff) > MAX_DIFF:
        raise ToolInputError("Artifact payload is invalid.")

    existing = await api.find_pull(repo, head)
    if existing:
        return {
            "status": "existing",
            "repo": repo,
            "head": head,
            "base": base,
            "draft": bool(existing.get("draft")),
            "number": existing.get("number"),
            "url": existing.get("url", ""),
        }

    branch_sha = await api.get_ref_sha(repo, head)
    if branch_sha is None:
        base_sha = await api.get_ref_sha(repo, base)
        if base_sha is None:
            raise ToolInputError("Base branch does not exist.")
        base_tree = await api.get_commit_tree(repo, base_sha)
        blob_sha = await api.create_blob(repo, diff)
        tree_sha = await api.create_tree(repo, base_tree, f".freepoint/patches/{context.job_id}.patch", blob_sha)
        branch_sha = await api.create_commit(repo, f"Free Point: {title}", tree_sha, [base_sha])
        await api.create_ref(repo, head, branch_sha)

    pull = await api.create_pull(repo, head, base, title, body)
    return {
        "status": "created",
        "repo": repo,
        "head": head,
        "base": base,
        "draft": bool(pull.get("draft")),
        "number": pull.get("number"),
        "url": pull.get("url", ""),
    }
