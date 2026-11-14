"""Tests for tool registry, demo read tools, and write tool approval artifacts."""

import pytest

from harness.app.core.tools import DemoWriteTool, EchoTool, ToolRegistry, UtcTimeTool


@pytest.mark.asyncio
async def test_echo_tool():
    tool = EchoTool()
    res = await tool.run(message="hello world")
    assert res == {"echo": "hello world"}
    assert tool.side_effect == "read"


@pytest.mark.asyncio
async def test_utc_time_tool():
    tool = UtcTimeTool()
    res = await tool.run()
    assert "utc_time" in res
    assert "T" in res["utc_time"]
    assert tool.side_effect == "read"


@pytest.mark.asyncio
async def test_write_tool_returns_approval_artifact():
    tool = DemoWriteTool()
    assert tool.side_effect == "write"
    artifact = await tool.run(repo="owner/repo", title="Test PR", body="Description", diff="+code")
    assert artifact.tool_name == "create_draft_pr"
    assert artifact.action == "draft_pr"
    assert len(artifact.artifact_hash) == 64
    assert artifact.artifact_id.startswith("art-")
    assert artifact.payload["repo"] == "owner/repo"


def test_tool_registry():
    registry = ToolRegistry()
    assert registry.get("echo") is not None
    assert registry.get("utc_time") is not None
    assert registry.get("create_draft_pr") is not None
    assert registry.get("non_existent") is None
