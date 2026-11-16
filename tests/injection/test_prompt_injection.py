"""Prompt-injection suite: hostile tool output never triggers a GitHub write."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.app.audit import audit_logger
from harness.app.core.agent_loop import AgentLoop
from harness.app.core.mcp_tools import GitHubDraftPRTool
from harness.app.core.tools import Tool, ToolRegistry
from harness.app.mcp_client.github_config import GitHubCredentials
from harness.app.providers.mock_provider import MockLLMProvider
from harness.app.store.factory import get_approval_store, get_job_store, reset_stores
from mcp_common.branches import branch_for_job

PAYLOADS = json.loads(Path(__file__).with_name("payloads.json").read_text(encoding="utf-8"))
assert len(PAYLOADS) == 15
TOKEN = "ghp_injection_test_token"
CREDENTIALS = GitHubCredentials(token=TOKEN, allowlist=("acme/widgets",), base_branch="main")


class LeakyReadTool(Tool):
    name = "github_read_file"
    description = "Returns hostile repository content."
    side_effect = "read"
    schema = {
        "type": "object",
        "properties": {"repo": {"type": "string"}, "path": {"type": "string"}},
        "required": ["repo", "path"],
        "additionalProperties": False,
    }

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"repo": kwargs["repo"], "path": kwargs["path"], "text": self.payload}


class RecordingCaller:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any], context: Any
    ) -> dict[str, Any]:
        self.calls.append(tool)
        return {
            "repo": arguments["repo"],
            "base": arguments.get("base", "main"),
            "head": branch_for_job(context.job_id),
            "title": arguments["title"],
            "body": arguments["body"],
            "diff": arguments["diff"],
        }


def _loader():
    async def load() -> GitHubCredentials:
        return CREDENTIALS

    return load


@pytest.fixture(autouse=True)
def clean_stores() -> None:
    reset_stores()
    yield
    reset_stores()


@pytest.mark.parametrize("payload", PAYLOADS, ids=[f"payload-{index}" for index in range(len(PAYLOADS))])
@pytest.mark.asyncio
async def test_injection_never_writes_without_approval(
    payload: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries: list[dict[str, Any]] = []
    original = audit_logger.log_event

    def record(event_type: str, **kwargs: Any) -> dict[str, Any]:
        entries.append({"event_type": event_type, **kwargs})
        return original(event_type, **kwargs)

    monkeypatch.setattr(audit_logger, "log_event", record)

    caller = RecordingCaller()
    registry = ToolRegistry(demo_write=False)
    registry.register(LeakyReadTool(payload))
    registry.register(GitHubDraftPRTool(caller, "usr-a", "job-inject", _loader()))

    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "github_read_file",
                "arguments": {"repo": "acme/widgets", "path": "README.md"},
                "final_answer": "",
            },
            {
                "action": "call_tool",
                "tool_name": "create_draft_pr",
                "arguments": {
                    "repo": "acme/widgets",
                    "title": "Injected change",
                    "body": payload,
                    "diff": "+malicious",
                },
                "final_answer": "",
            },
        ]
    )
    job = await get_job_store().create_job(
        task="summarize this README",
        declared_tools=["github_read_file", "create_draft_pr"],
        user_id="usr-a",
    )
    result = await AgentLoop(provider=provider, tool_registry=registry).run(job)

    assert result.status.value == "AWAITING_APPROVAL"
    assert "execute_draft_pr" not in caller.calls
    assert caller.calls == ["create_draft_pr"]

    read_events = [entry for entry in entries if entry["event_type"] == "tool_called"]
    assert read_events and all(entry["tainted_by_untrusted_source"] is True for entry in read_events)

    approval = await get_approval_store().get_approval(f"appr-{job.id}", "usr-a")
    assert approval is not None and approval["status"] == "pending"


@pytest.mark.asyncio
async def test_execute_tool_is_not_in_the_model_registry() -> None:
    caller = RecordingCaller()
    registry = ToolRegistry(demo_write=False)
    registry.register(GitHubDraftPRTool(caller, "usr-a", "job-exec", _loader()))
    assert registry.get("execute_draft_pr") is None

    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "execute_draft_pr",
                "arguments": {"repo": "acme/widgets", "base": "main", "head": "x", "title": "t", "body": "b", "diff": "d"},
                "final_answer": "",
            }
        ]
    )
    job = await get_job_store().create_job(
        task="run the execute tool",
        declared_tools=["github_read_file"],
        user_id="usr-a",
    )
    result = await AgentLoop(provider=provider, tool_registry=registry).run(job)
    assert result.status.value == "FAILED"
    assert caller.calls == []
