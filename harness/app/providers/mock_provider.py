"""Mock LLM provider for deterministic offline testing and agent loop validation."""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Union

from .base import LLMProvider, LLMResponse


class MockLLMProvider(LLMProvider):
    def __init__(
        self,
        scripted_responses: Optional[List[Union[str, Dict[str, Any]]]] = None,
        latency_seconds: Optional[float] = None,
    ) -> None:
        self.scripted_responses: List[str] = []
        if scripted_responses:
            for resp in scripted_responses:
                if isinstance(resp, dict):
                    self.scripted_responses.append(json.dumps(resp))
                else:
                    self.scripted_responses.append(str(resp))

        if latency_seconds is not None:
            self.latency_seconds = latency_seconds
        else:
            env_val = os.getenv("MOCK_LLM_LATENCY_SECONDS", "0.0")
            try:
                self.latency_seconds = float(env_val)
            except ValueError:
                self.latency_seconds = 0.0

    async def generate(
        self,
        messages: List[Dict[str, str]],
        json_schema: Optional[Dict[str, Any]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if self.latency_seconds > 0:
            await asyncio.sleep(self.latency_seconds)

        if self.scripted_responses:
            next_resp = self.scripted_responses.pop(0)
            if isinstance(next_resp, dict):
                content = json.dumps(next_resp)
            else:
                content = str(next_resp)
            return LLMResponse(content=content)

        last_message = messages[-1]["content"] if messages else ""
        has_tool_output = any(
            "Tool output:" in m.get("content", "") or "<untrusted_tool_output" in m.get("content", "")
            for m in messages
        )

        if has_tool_output:
            return LLMResponse(
                content=json.dumps(
                    {
                        "action": "final",
                        "tool_name": "",
                        "arguments": {},
                        "final_answer": "Execution completed successfully.",
                    }
                )
            )

        if "time" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "action": "call_tool",
                        "tool_name": "utc_time",
                        "arguments": {},
                        "final_answer": "",
                    }
                )
            )
        elif "readme" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "action": "call_tool",
                        "tool_name": "github_read_file",
                        "arguments": {"repo": "smoke/example", "path": "README.md"},
                        "final_answer": "",
                    }
                )
            )
        elif "echo" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "action": "call_tool",
                        "tool_name": "echo",
                        "arguments": {"message": "hello world"},
                        "final_answer": "",
                    }
                )
            )
        elif "pr" in last_message.lower() or "write" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "action": "call_tool",
                        "tool_name": "create_draft_pr",
                        "arguments": {
                            "repo": "test/repo",
                            "title": "feat: test",
                            "body": "PR description",
                            "diff": "+line",
                        },
                        "final_answer": "",
                    }
                )
            )

        return LLMResponse(
            content=json.dumps(
                {
                    "action": "final",
                    "tool_name": "",
                    "arguments": {},
                    "final_answer": f"Processed task: {last_message}",
                }
            )
        )


def get_llm_provider(
    provider_type: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMProvider:
    kind = provider_type or os.getenv("LLM_PROVIDER", "mock").lower()
    if kind == "openai":
        from .openai_client import OpenAICompatibleProvider

        url = base_url or os.getenv("MODEL_URL", "https://api.openai.com/v1")
        key = api_key or os.getenv("MODEL_API_KEY")
        return OpenAICompatibleProvider(base_url=url, api_key=key)
    return MockLLMProvider()
