"""Harness/MCP integration tests."""

from __future__ import annotations

import time
from typing import Any

import jsonschema
import pytest

from harness.app.core.agent_loop import AgentLoop
from harness.app.core.approvals import approval_hash
from harness.app.core.mcp_tools import build_tool_registry
from harness.app.core.worker import ExecutionError, execute_approved_job
from harness.app.mcp_client.github_config import GitHubCredentials
from harness.app.providers.mock_provider import MockLLMProvider
from harness.app.store.factory import (
    get_approval_store,
    get_job_store,
    reset_stores,
)
from mcp_common.branches import branch_for_job
from mcp_common.security import TrustedContext

TOKEN = "ghp_SUPER_SECRET_TOKEN_123456"
CREDENTIALS = GitHubCredentials(token=TOKEN, allowlist=("acme/widgets",), base_branch="main")


@pytest.fixture(autouse=True)
def clean_stores() -> None:
    reset_stores()
    yield
    reset_stores()


class FakeCaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], TrustedContext]] = []

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        context: TrustedContext,
    ) -> dict[str, Any]:
        self.calls.append((server, tool, dict(arguments), context))
        if tool == "create_draft_pr":
            return {
                "repo": arguments["repo"],
                "base": arguments.get("base", "main"),
                "head": branch_for_job(context.job_id),
                "title": arguments["title"],
                "body": arguments["body"],
                "diff": arguments["diff"],
            }
        if tool == "read_file":
            return {"path": arguments["path"], "text": "untrusted readme text"}
        if tool == "execute_draft_pr":
            return {
                "status": "created",
                "number": 42,
                "url": "https://github.com/acme/widgets/pull/42",
                "head": arguments["head"],
                "base": arguments["base"],
                "draft": True,
            }
        return {}


def _loader(counter: list[int]):
    async def load() -> GitHubCredentials:
        counter[0] += 1
        return CREDENTIALS

    return load


async def _job_with_registry(caller: FakeCaller, job_id: str = "job-test-approval"):
    job = await get_job_store().create_job(
        task="open a draft pr",
        declared_tools=["create_draft_pr"],
        user_id="usr-a",
    )
    registry = build_tool_registry(job.user_id or "", job.id, caller, _loader([0]))
    return job, registry


@pytest.mark.asyncio
async def test_model_args_cannot_override_tenant() -> None:
    caller = FakeCaller()
    registry = build_tool_registry("usr-a", "job-x", caller, _loader([0]))
    draft = registry.get("create_draft_pr")
    assert draft is not None

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"repo": "acme/widgets", "title": "t", "body": "b", "diff": "d", "user_id": "usr-b"},
            draft.schema,
        )

    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "create_draft_pr",
                "arguments": {
                    "repo": "acme/widgets",
                    "title": "t",
                    "body": "b",
                    "diff": "d",
                    "user_id": "usr-b",
                },
                "final_answer": "",
            }
        ]
    )
    job = await get_job_store().create_job(task="escalate", declared_tools=["create_draft_pr"], user_id="usr-a")
    result = await AgentLoop(provider=provider, tool_registry=registry).run(job)
    assert result.status.value == "FAILED"
    assert "schema validation" in (result.error or "")
    assert caller.calls == []


@pytest.mark.asyncio
async def test_token_is_out_of_band_and_per_call() -> None:
    caller = FakeCaller()
    counter = [0]
    registry = build_tool_registry("usr-a", "job-abc", caller, _loader(counter))
    draft = registry.get("create_draft_pr")
    assert draft is not None

    args = {"repo": "acme/widgets", "title": "t", "body": "b", "diff": "d"}
    await draft.run(**args)
    await draft.run(**args)

    assert counter[0] == 2
    assert len(caller.calls) == 2
    for _, _, sent, context in caller.calls:
        assert "user_id" not in sent and "token" not in sent and "job_id" not in sent
        assert context.token == TOKEN
        assert context.user_id == "usr-a"


@pytest.mark.asyncio
async def test_create_draft_pr_records_approval_and_does_not_execute() -> None:
    caller = FakeCaller()
    job, registry = await _job_with_registry(caller)
    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "create_draft_pr",
                "arguments": {"repo": "acme/widgets", "title": "Fix", "body": "Body", "diff": "+x"},
                "final_answer": "",
            }
        ]
    )
    result = await AgentLoop(provider=provider, tool_registry=registry).run(job)
    assert result.status.value == "AWAITING_APPROVAL"
    await get_job_store().update_job(result)

    approval = await get_approval_store().get_approval(f"appr-{job.id}", "usr-a")
    assert approval is not None
    assert approval["payload_hash"] == approval_hash(approval)
    assert approval["head"] == branch_for_job(job.id)
    assert not any(call[1] == "execute_draft_pr" for call in caller.calls)


@pytest.mark.asyncio
async def test_execute_consumer_completes_once() -> None:
    caller = FakeCaller()
    job, registry = await _job_with_registry(caller)
    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "create_draft_pr",
                "arguments": {"repo": "acme/widgets", "title": "Fix", "body": "Body", "diff": "+x"},
                "final_answer": "",
            }
        ]
    )
    result = await AgentLoop(provider=provider, tool_registry=registry).run(job)
    await get_job_store().update_job(result)

    approval = await get_approval_store().get_approval(f"appr-{job.id}", "usr-a")
    assert approval is not None
    decided = await get_approval_store().decide(
        f"appr-{job.id}", "usr-a", "approved", approval_hash(approval), int(time.time())
    )
    assert decided == "ok"

    completed = await execute_approved_job(job.id, caller=caller, credentials_loader=_loader([0]))
    assert completed["pr_number"] == 42
    stored = await get_job_store().get_job(job.id)
    assert stored is not None and stored.status.value == "COMPLETED"
    executes = [call for call in caller.calls if call[1] == "execute_draft_pr"]
    assert len(executes) == 1

    again = await execute_approved_job(job.id, caller=caller, credentials_loader=_loader([0]))
    assert again["pr_number"] == 42
    assert len([call for call in caller.calls if call[1] == "execute_draft_pr"]) == 1


@pytest.mark.asyncio
async def test_execute_rejects_tampered_artifact() -> None:
    caller = FakeCaller()
    job, registry = await _job_with_registry(caller)
    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "create_draft_pr",
                "arguments": {"repo": "acme/widgets", "title": "Fix", "body": "Body", "diff": "+x"},
                "final_answer": "",
            }
        ]
    )
    result = await AgentLoop(provider=provider, tool_registry=registry).run(job)
    approval = await get_approval_store().get_approval(f"appr-{job.id}", "usr-a")
    assert approval is not None
    await get_approval_store().decide(
        f"appr-{job.id}", "usr-a", "approved", approval_hash(approval), int(time.time())
    )
    assert result.approval_artifact is not None
    result.approval_artifact["payload"]["diff"] = "+evil"
    await get_job_store().update_job(result)

    with pytest.raises(ExecutionError):
        await execute_approved_job(job.id, caller=caller, credentials_loader=_loader([0]))
    assert not any(call[1] == "execute_draft_pr" for call in caller.calls)


@pytest.mark.asyncio
async def test_token_never_appears_in_results_or_logs(capsys: pytest.CaptureFixture[str]) -> None:
    caller = FakeCaller()
    registry = build_tool_registry("usr-a", "job-read", caller, _loader([0]))
    tool = registry.get("github_read_file")
    assert tool is not None
    result = await tool.run(repo="acme/widgets", path="README.md")
    output = capsys.readouterr()
    assert TOKEN not in str(result)
    assert TOKEN not in output.out
    assert TOKEN not in output.err
