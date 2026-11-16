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
            {"action": "final", "tool_name": "", "arguments": {}, "final_answer": "Recovered from retry"},
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
    assert "Model failed after 2 retries" in result_job.error


@pytest.mark.asyncio
async def test_allowlist_rejection_of_undeclared_tool():
    provider = MockLLMProvider(
        scripted_responses=[
            {"action": "call_tool", "tool_name": "utc_time", "arguments": {}, "final_answer": ""}
        ]
    )
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
            {"action": "call_tool", "tool_name": "echo", "arguments": {"message": "1"}, "final_answer": ""},
            {"action": "call_tool", "tool_name": "echo", "arguments": {"message": "2"}, "final_answer": ""},
            {"action": "call_tool", "tool_name": "echo", "arguments": {"message": "3"}, "final_answer": ""},
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
                "arguments": {"repo": "free-point/core", "title": "fix", "body": "details", "diff": "+1"},
                "final_answer": "",
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


@pytest.mark.asyncio
async def test_ollama_key_normalization_and_preprocessing():
    """Covers: key aliasing (tool→tool_name), think-block stripping, json-fence stripping,
    undeclared tool rejection."""
    registry = ToolRegistry()

    # 1. Key "tool" instead of "tool_name" is normalized, then tool runs successfully.
    provider = MockLLMProvider(
        scripted_responses=[
            # tool_name missing; "tool" should be mapped to tool_name
            '{"action":"call_tool","tool":"echo","arguments":{"message":"hi"},"final_answer":""}',
            {"action": "final", "tool_name": "", "arguments": {}, "final_answer": "done"},
        ]
    )
    loop = AgentLoop(provider=provider, tool_registry=registry)
    job = Job(task="normalize key test", declared_tools=["echo"])
    result = await loop.run(job)
    assert result.status == JobStatus.COMPLETED
    assert result.result == "done"

    # 2. <think> block stripped before parse.
    provider2 = MockLLMProvider(
        scripted_responses=[
            '<think>reasoning here</think>{"action":"final","tool_name":"","arguments":{},"final_answer":"think-stripped"}',
        ]
    )
    loop2 = AgentLoop(provider=provider2, tool_registry=registry)
    job2 = Job(task="think block test", declared_tools=["echo"])
    result2 = await loop2.run(job2)
    assert result2.status == JobStatus.COMPLETED
    assert result2.result == "think-stripped"

    # 3. ```json fence stripped before parse.
    provider3 = MockLLMProvider(
        scripted_responses=[
            '```json\n{"action":"final","tool_name":"","arguments":{},"final_answer":"fence-stripped"}\n```',
        ]
    )
    loop3 = AgentLoop(provider=provider3, tool_registry=registry)
    job3 = Job(task="fence test", declared_tools=["echo"])
    result3 = await loop3.run(job3)
    assert result3.status == JobStatus.COMPLETED
    assert result3.result == "fence-stripped"

    # 4. Undeclared tool is rejected with security policy violation.
    provider4 = MockLLMProvider(
        scripted_responses=[
            {"action": "call_tool", "tool_name": "banned_tool", "arguments": {}, "final_answer": ""},
        ]
    )
    loop4 = AgentLoop(provider=provider4, tool_registry=registry)
    job4 = Job(task="undeclared tool test", declared_tools=["echo"])
    result4 = await loop4.run(job4)
    assert result4.status == JobStatus.FAILED
    assert "Security policy violation" in result4.error
