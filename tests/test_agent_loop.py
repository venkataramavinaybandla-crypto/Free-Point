"""Tests for agent loop schema retry, tool allowlist, caps, and end-to-end flow."""

import pytest

from harness.app.core.agent_loop import AgentLoop
from harness.app.core.state import JobStatus
from harness.app.core.tools import ToolRegistry
from harness.app.providers.mock_provider import MockLLMProvider
from harness.app.store.memory import Job


@pytest.mark.asyncio
async def test_schema_retry_recovers_on_good_json():
    provider = MockLLMProvider(
        scripted_responses=[
            "NOT_VALID_JSON",
            {"action": "finish", "final_response": "Recovered from retry"},
        ]
    )
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    job = Job(task="test retry", declared_tools=["echo"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.COMPLETED
    assert result_job.result == "Recovered from retry"


@pytest.mark.asyncio
async def test_schema_retry_exhaustion_fails():
    provider = MockLLMProvider(
        scripted_responses=[
            "BAD_1",
            "BAD_2",
            "BAD_3",
        ]
    )
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry(), max_json_retries=2)
    job = Job(task="test retry failure", declared_tools=["echo"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.FAILED
    assert "Model failed to produce valid JSON after 2 retries" in result_job.error


@pytest.mark.asyncio
async def test_allowlist_rejection_of_undeclared_tool():
    provider = MockLLMProvider(scripted_responses=[{"action": "call_tool", "tool_name": "utc_time", "tool_args": {}}])
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    job = Job(task="test allowlist", declared_tools=["echo"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.FAILED
    assert "Security policy violation" in result_job.error
    assert "not in declared tools allowlist" in result_job.error


@pytest.mark.asyncio
async def test_step_cap_enforcement():
    provider = MockLLMProvider(
        scripted_responses=[
            {"action": "call_tool", "tool_name": "echo", "tool_args": {"message": "1"}},
            {"action": "call_tool", "tool_name": "echo", "tool_args": {"message": "2"}},
            {"action": "call_tool", "tool_name": "echo", "tool_args": {"message": "3"}},
        ]
    )
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry(), max_steps=2)
    job = Job(task="test step cap", declared_tools=["echo"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.FAILED
    assert "exceeded step cap of 2 steps" in result_job.error


@pytest.mark.asyncio
async def test_time_cap_enforcement():
    provider = MockLLMProvider(latency_seconds=0.1)
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry(), wall_cap_seconds=0.05)
    job = Job(task="please sleep and timeout", declared_tools=["echo"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.TIMED_OUT
    assert "exceeded wall time cap" in result_job.error


@pytest.mark.asyncio
async def test_end_to_end_job_with_mock_llm():
    provider = MockLLMProvider()
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    job = Job(task="Please echo my message", declared_tools=["echo"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.COMPLETED
    assert result_job.steps == 1
    assert "Execution completed successfully" in result_job.result


@pytest.mark.asyncio
async def test_write_tool_pauses_for_approval():
    provider = MockLLMProvider(
        scripted_responses=[
            {
                "action": "call_tool",
                "tool_name": "create_draft_pr",
                "tool_args": {"repo": "free-point/core", "title": "fix", "body": "details", "diff": "+1"},
            }
        ]
    )
    loop = AgentLoop(provider=provider, tool_registry=ToolRegistry())
    job = Job(task="make PR", declared_tools=["create_draft_pr"])

    result_job = await loop.run(job)
    assert result_job.status == JobStatus.AWAITING_APPROVAL
    assert result_job.approval_artifact is not None
    assert result_job.approval_artifact["tool_name"] == "create_draft_pr"
    assert "artifact_hash" in result_job.approval_artifact
