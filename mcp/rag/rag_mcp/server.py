"""RAG MCP server: tenant-scoped ingest and semantic query."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_common.security import build_http_app, require_context

from .embeddings import embedder_from_env
from .index_store import RagService, RagStore
from .mock_store import mock_clients

mcp = FastMCP(
    "rag_mcp",
    instructions="Free Point tenant-scoped retrieval over indexed text.",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
    json_response=True,
    log_level="WARNING",
)


@lru_cache(maxsize=1)
def _service() -> RagService:
    if os.environ.get("RAG_MOCK_BACKEND", "false").lower() == "true":
        s3_client, dynamo_client = mock_clients()
        return RagService(RagStore(s3_client=s3_client, dynamo_client=dynamo_client), embedder_from_env())
    return RagService(RagStore(), embedder_from_env())


@mcp.tool(name="ingest_text")
async def ingest_text(text: str, source: str = "manual") -> dict[str, Any]:
    """Chunk and index text into the calling user's private index."""
    context = require_context()
    return await _service().ingest(context.user_id, text, source)


@mcp.tool(name="query")
async def query(query: str, k: int = 5) -> dict[str, Any]:
    """Return the top-k chunks from the calling user's private index."""
    context = require_context()
    return await _service().query(context.user_id, query, k)


def create_app(secret: str) -> Any:
    return build_http_app(mcp, secret, "rag-mcp")


def main() -> None:
    secret = os.environ.get("INTERNAL_MCP_SECRET", "")
    if not secret:
        raise SystemExit("INTERNAL_MCP_SECRET is required")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
