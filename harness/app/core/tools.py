"""Tool registry and demo tools."""

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel


class ToolArtifact(BaseModel):
    artifact_id: str
    artifact_hash: str
    tool_name: str
    action: str
    payload: Dict[str, Any]


class Tool(ABC):
    name: str
    description: str
    schema: Dict[str, Any]
    side_effect: Literal["read", "write"]

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        pass


class EchoTool(Tool):
    name = "echo"
    description = "Echoes back the input message."
    side_effect = "read"
    schema = {
        "type": "object",
        "properties": {"message": {"type": "string", "description": "The message to echo"}},
        "required": ["message"],
    }

    async def run(self, message: str = "", **kwargs: Any) -> Dict[str, Any]:
        return {"echo": message}


class UtcTimeTool(Tool):
    name = "utc_time"
    description = "Returns current UTC timestamp in ISO 8601 format."
    side_effect = "read"
    schema = {
        "type": "object",
        "properties": {},
    }

    async def run(self, **kwargs: Any) -> Dict[str, Any]:
        return {"utc_time": datetime.now(timezone.utc).isoformat()}


class DemoWriteTool(Tool):
    """Stub write tool for P4. Write tools never execute directly; they return a hash-bound artifact."""

    name = "create_draft_pr"
    description = "Prepares a draft pull request artifact awaiting human approval."
    side_effect = "write"
    schema = {
        "type": "object",
        "properties": {
            "repo": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "diff": {"type": "string"},
        },
        "required": ["repo", "title", "body", "diff"],
    }

    async def run(self, **kwargs: Any) -> ToolArtifact:
        content_bytes = json.dumps(kwargs, sort_keys=True).encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        artifact_id = f"art-{content_hash[:12]}"
        return ToolArtifact(
            artifact_id=artifact_id,
            artifact_hash=content_hash,
            tool_name=self.name,
            action="draft_pr",
            payload=kwargs,
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self.register(EchoTool())
        self.register(UtcTimeTool())
        self.register(DemoWriteTool())

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.schema,
                "side_effect": tool.side_effect,
            }
            for name, tool in self._tools.items()
        }
