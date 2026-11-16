"""GitHub MCP server tests: no model write, idempotency, protected branches."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from github_mcp.artifacts import ToolInputError, execute_artifact, prepare_artifact
from github_mcp.server import create_app

from mcp_common.branches import branch_for_job
from mcp_common.security import TrustedContext, sign_headers

CONTEXT = TrustedContext(
    user_id="usr-a",
    job_id="job-abc123",
    repo_allowlist=("acme/widgets",),
    base_branch="main",
)
SECRET = "test-internal-secret"


class FakeGitHub:
    def __init__(self) -> None:
        self.refs = {"main": "base-sha"}
        self.pulls: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    async def get_ref_sha(self, repo: str, branch: str) -> str | None:
        self.calls.append(f"get_ref:{branch}")
        return self.refs.get(branch)

    async def get_commit_tree(self, repo: str, sha: str) -> str:
        self.calls.append("get_tree")
        return "base-tree"

    async def create_blob(self, repo: str, content: str) -> str:
        self.calls.append("blob")
        return "blob-sha"

    async def create_tree(self, repo: str, base_tree: str, path: str, blob_sha: str) -> str:
        self.calls.append("tree")
        return "tree-sha"

    async def create_commit(self, repo: str, message: str, tree: str, parents: list[str]) -> str:
        self.calls.append("commit")
        return "commit-sha"

    async def create_ref(self, repo: str, branch: str, sha: str) -> None:
        self.calls.append(f"create_ref:{branch}")
        self.refs[branch] = sha

    async def find_pull(self, repo: str, head: str) -> dict[str, Any] | None:
        self.calls.append("find_pull")
        return self.pulls.get(head)

    async def create_pull(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        self.calls.append("create_pull")
        pull = {"number": 7, "url": "https://github.com/acme/widgets/pull/7", "draft": True, "state": "open"}
        self.pulls[head] = pull
        return pull


def _artifact(**overrides: Any) -> dict[str, Any]:
    artifact = prepare_artifact(
        CONTEXT,
        repo="acme/widgets",
        title="Fix the widget",
        body="Body",
        diff="--- a/x\n+++ b/x\n+fix\n",
    )
    artifact.update(overrides)
    return artifact


def test_prepare_never_calls_github(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_mcp.server as server

    def boom(*_: Any, **__: Any) -> None:
        raise AssertionError("create_draft_pr must not call the GitHub API")

    monkeypatch.setattr(server, "GitHubAPI", boom)
    artifact = _artifact()
    assert artifact["head"] == branch_for_job(CONTEXT.job_id)
    assert artifact["repo"] == "acme/widgets"
    assert artifact["base"] == "main"


@pytest.mark.asyncio
async def test_execute_is_idempotent() -> None:
    api = FakeGitHub()
    first = await execute_artifact(CONTEXT, _artifact(), api)
    second = await execute_artifact(CONTEXT, _artifact(), api)

    assert first["status"] == "created"
    assert second["status"] == "existing"
    assert first["number"] == second["number"] == 7
    assert api.calls.count("create_pull") == 1
    assert api.calls.count(f"create_ref:{branch_for_job(CONTEXT.job_id)}") == 1


@pytest.mark.asyncio
async def test_existing_branch_is_reused() -> None:
    api = FakeGitHub()
    api.refs[branch_for_job(CONTEXT.job_id)] = "existing-sha"
    result = await execute_artifact(CONTEXT, _artifact(), api)
    assert result["status"] == "created"
    assert "create_ref:freepoint/job-abc123" not in api.calls
    assert "commit" not in api.calls and "blob" not in api.calls


@pytest.mark.asyncio
async def test_protected_branch_never_targeted() -> None:
    api = FakeGitHub()
    with pytest.raises(ToolInputError):
        await execute_artifact(CONTEXT, _artifact(head="main"), api)
    with pytest.raises(ToolInputError):
        await execute_artifact(CONTEXT, _artifact(head="release/master"), api)
    assert api.calls == []


@pytest.mark.asyncio
async def test_repo_outside_allowlist_rejected() -> None:
    with pytest.raises(ToolInputError):
        prepare_artifact(CONTEXT, repo="evil/repo", title="t", body="b", diff="d")

    artifact = _artifact()
    artifact["repo"] = "evil/repo"
    with pytest.raises(ToolInputError):
        await execute_artifact(CONTEXT, artifact, FakeGitHub())


def test_internal_auth_rejects_unsigned_and_accepts_signed() -> None:
    with TestClient(create_app(SECRET)) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401

        signed = sign_headers(SECRET, CONTEXT, timestamp=int(time.time()))
        assert client.post("/mcp", headers=signed, json={}).status_code != 401

        stale = sign_headers(SECRET, CONTEXT, timestamp=int(time.time()) - 3600)
        assert client.post("/mcp", headers=stale, json={}).status_code == 401

        tampered = sign_headers(SECRET, CONTEXT)
        tampered["x-fp-job-id"] = "job-other"
        assert client.post("/mcp", headers=tampered, json={}).status_code == 401
