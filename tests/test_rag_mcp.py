"""RAG MCP tests using moto for S3 and DynamoDB."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
import jsonschema
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from rag_mcp.chunking import ChunkingError, chunk_text
from rag_mcp.embeddings import HashingEmbedder
from rag_mcp.index_store import RagService, RagStore
from rag_mcp.server import create_app

from harness.app.core.mcp_tools import RAGIngestTool, RAGQueryTool

BUCKET = "free-point-rag-indexes"
TABLE = "free-point-rag_pointers"


@pytest.fixture()
def aws() -> Any:
    with mock_aws():
        yield


def _clients() -> tuple[Any, Any]:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    dynamo.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return s3, dynamo


def _service(s3: Any, dynamo: Any) -> RagService:
    return RagService(
        RagStore(bucket=BUCKET, table=TABLE, region="us-east-1", s3_client=s3, dynamo_client=dynamo),
        HashingEmbedder(),
    )


def test_chunking_has_overlap_and_caps() -> None:
    text = " ".join(f"word{index}" for index in range(500))
    chunks = chunk_text(text, chunk_chars=400, overlap=80)
    assert len(chunks) > 1
    assert all(len(chunk) <= 400 for chunk in chunks)
    assert chunks[0][-20:] in chunks[1]

    with pytest.raises(ChunkingError):
        chunk_text("x" * 200_001)


@pytest.mark.asyncio
async def test_cross_tenant_zero_hits(aws: None) -> None:
    s3, dynamo = _clients()
    service = _service(s3, dynamo)
    await service.ingest("usr-a", "The launch code is alpha-seven.", "readme")

    own = await service.query("usr-a", "launch code", 3)
    other = await service.query("usr-b", "launch code", 3)
    assert own["results"]
    assert other["results"] == []
    assert other["version"] == 0

    keys = [item["Key"] for item in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert keys and all(key.startswith("index/usr-a/") for key in keys)


class RacingStore(RagStore):
    """Force both writers to observe the same pointer version."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reads = 0
        self.ready = asyncio.Event()
        self.lock = asyncio.Lock()

    async def get_pointer(self, user_id: str) -> int:
        async with self.lock:
            self.reads += 1
            read_number = self.reads
            if read_number == 2:
                self.ready.set()
        if read_number <= 2:
            await asyncio.wait_for(self.ready.wait(), timeout=5)
        return await super().get_pointer(user_id)


@pytest.mark.asyncio
async def test_concurrent_writes_converge_without_loss(aws: None) -> None:
    s3, dynamo = _clients()
    store = RacingStore(bucket=BUCKET, table=TABLE, region="us-east-1", s3_client=s3, dynamo_client=dynamo)
    service = RagService(store, HashingEmbedder())

    results = await asyncio.gather(
        service.ingest("usr-a", "alpha unique first chunk", "one"),
        service.ingest("usr-a", "omega unique second chunk", "two"),
    )
    assert sorted(result["version"] for result in results) == [1, 2]
    assert await store.get_pointer("usr-a") == 2

    hits = await service.query("usr-a", "alpha omega unique", 5)
    texts = " ".join(item["text"] for item in hits["results"])
    assert "alpha unique first" in texts
    assert "omega unique second" in texts

    keys = [item["Key"] for item in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert "index/usr-a/v1/index.faiss" in keys
    assert "index/usr-a/v2/index.faiss" in keys


@pytest.mark.asyncio
async def test_k_and_query_caps(aws: None) -> None:
    s3, dynamo = _clients()
    service = _service(s3, dynamo)
    await service.ingest("usr-a", "one two three", "x")
    with pytest.raises(ChunkingError):
        await service.query("usr-a", "one", 9)
    with pytest.raises(ChunkingError):
        await service.query("usr-a", "", 3)


def test_rag_mcp_rejects_unsigned_calls() -> None:
    with TestClient(create_app("test-secret")) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401


def test_model_args_cannot_override_rag_tenant() -> None:
    ingest_schema = RAGIngestTool(None, "usr-a", "job-r").schema  # type: ignore[arg-type]
    query_schema = RAGQueryTool(None, "usr-a", "job-r").schema  # type: ignore[arg-type]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"text": "x", "user_id": "usr-b"}, ingest_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"query": "x", "user_id": "usr-b"}, query_schema)
