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
    ) -> LLMResponse:
        if self.latency_seconds > 0:
            await asyncio.sleep(self.latency_seconds)

        if self.scripted_responses:
            content = self.scripted_responses.pop(0)
            return LLMResponse(content=content)

        last_message = messages[-1]["content"] if messages else ""
        has_tool_output = any("Tool output:" in m.get("content", "") for m in messages)

        if has_tool_output:
            return LLMResponse(
                content=json.dumps(
                    {
                        "thought": "Tool output consumed, completing execution.",
                        "action": "finish",
                        "final_response": "Execution completed successfully.",
                    }
                )
            )

        if "time" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "thought": "Invoking utc_time tool.",
                        "action": "call_tool",
                        "tool_name": "utc_time",
                        "tool_args": {},
                    }
                )
            )
        elif "echo" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "thought": "Invoking echo tool.",
                        "action": "call_tool",
                        "tool_name": "echo",
                        "tool_args": {"message": "hello world"},
                    }
                )
            )
        elif "pr" in last_message.lower() or "write" in last_message.lower():
            return LLMResponse(
                content=json.dumps(
                    {
                        "thought": "Creating PR artifact requiring human approval.",
                        "action": "call_tool",
                        "tool_name": "create_draft_pr",
                        "tool_args": {
                            "repo": "test/repo",
                            "title": "feat: test",
                            "body": "PR description",
                            "diff": "+line",
                        },
                    }
                )
            )

        return LLMResponse(
            content=json.dumps(
                {
                    "thought": "Direct final response.",
                    "action": "finish",
                    "final_response": f"Processed task: {last_message}",
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
