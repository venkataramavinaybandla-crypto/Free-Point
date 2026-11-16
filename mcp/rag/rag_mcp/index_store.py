"""Per-user versioned FAISS indexes backed by S3 and a DynamoDB pointer.

Object keys always start with ``index/{user_id}/`` where the id comes from the
signed call context. The pointer is flipped with a conditional write; version
objects are created with ``If-None-Match: *`` so concurrent writers cannot
overwrite the same version.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Sequence

import boto3
import numpy as np
from botocore.exceptions import ClientError

from .chunking import MAX_STORED_CHUNKS, ChunkingError, chunk_text, clean_k, clean_query, clean_source
from .embeddings import Embedder

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_WRITE_ATTEMPTS = 8
DEFAULT_BUCKET = "free-point-rag-indexes"
DEFAULT_TABLE = "free-point-rag_pointers"


def valid_user_id(user_id: str) -> str:
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise ChunkingError("Invalid tenant identity.")
    return user_id


class FaissIndex:
    """Thin wrapper around a FAISS inner-product index."""

    def __init__(self, dimension: int) -> None:
        import faiss

        self.dimension = dimension
        self._index = faiss.IndexFlatIP(dimension)

    @classmethod
    def from_bytes(cls, data: bytes) -> "FaissIndex":
        import faiss

        raw = np.frombuffer(data, dtype=np.uint8).copy()
        index = faiss.deserialize_index(raw)
        obj = cls.__new__(cls)
        obj.dimension = index.d
        obj._index = index
        return obj

    @property
    def count(self) -> int:
        return int(self._index.ntotal)

    def add(self, vectors: Sequence[Sequence[float]]) -> None:
        import faiss

        array = np.asarray(vectors, dtype="float32")
        if array.ndim != 2 or array.shape[1] != self.dimension:
            raise ChunkingError("Embedding dimension does not match the index.")
        faiss.normalize_L2(array)
        self._index.add(array)

    def search(self, vector: Sequence[float], k: int) -> list[tuple[int, float]]:
        import faiss

        array = np.asarray([vector], dtype="float32")
        faiss.normalize_L2(array)
        distances, indices = self._index.search(array, k)
        return [(int(index), float(score)) for index, score in zip(indices[0], distances[0]) if index >= 0]

    def to_bytes(self) -> bytes:
        import faiss

        return faiss.serialize_index(self._index).tobytes()


class VersionConflict(Exception):
    """The requested index version already exists."""


class RagStore:
    def __init__(
        self,
        bucket: str | None = None,
        table: str | None = None,
        region: str | None = None,
        s3_client: Any = None,
        dynamo_client: Any = None,
    ) -> None:
        region = region or os.environ.get("AWS_REGION", "ap-south-1")
        self.bucket = bucket or os.environ.get("RAG_S3_BUCKET", DEFAULT_BUCKET)
        self.table = table or os.environ.get("RAG_POINTERS_TABLE", DEFAULT_TABLE)
        self._s3 = s3_client or boto3.client("s3", region_name=region)
        self._dynamo = dynamo_client or boto3.client("dynamodb", region_name=region)

    async def get_pointer(self, user_id: str) -> int:
        response = await asyncio.to_thread(
            self._dynamo.get_item,
            TableName=self.table,
            Key={"user_id": {"S": user_id}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return int(item["version"]["N"]) if item and "version" in item else 0

    async def cas_pointer(self, user_id: str, expected: int, new: int) -> bool:
        condition = "attribute_not_exists(user_id)" if expected == 0 else "#v = :expected"
        values: dict[str, dict[str, str]] = {":new": {"N": str(new)}, ":now": {"N": str(int(time.time()))}}
        if expected:
            values[":expected"] = {"N": str(expected)}
        try:
            await asyncio.to_thread(
                self._dynamo.update_item,
                TableName=self.table,
                Key={"user_id": {"S": user_id}},
                ConditionExpression=condition,
                UpdateExpression="SET #v = :new, updated_at = :now",
                ExpressionAttributeNames={"#v": "version"},
                ExpressionAttributeValues=values,
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    async def _load_objects(self, user_id: str, version: int) -> tuple[bytes, list[dict[str, Any]]]:
        prefix = f"index/{user_id}/v{version}"
        index_obj, chunks_obj = await asyncio.gather(
            asyncio.to_thread(self._s3.get_object, Bucket=self.bucket, Key=f"{prefix}/index.faiss"),
            asyncio.to_thread(self._s3.get_object, Bucket=self.bucket, Key=f"{prefix}/chunks.json"),
        )
        index_bytes = await asyncio.to_thread(index_obj["Body"].read)
        raw = await asyncio.to_thread(chunks_obj["Body"].read)
        return index_bytes, json.loads(raw.decode("utf-8"))

    async def load(self, user_id: str, version: int) -> tuple[FaissIndex, list[dict[str, Any]]]:
        index_bytes, chunks = await self._load_objects(user_id, version)
        return FaissIndex.from_bytes(index_bytes), chunks

    async def create_version(
        self, user_id: str, version: int, index: FaissIndex, chunks: list[dict[str, Any]]
    ) -> bool:
        prefix = f"index/{user_id}/v{version}"
        try:
            await asyncio.to_thread(
                self._s3.put_object,
                Bucket=self.bucket,
                Key=f"{prefix}/index.faiss",
                Body=index.to_bytes(),
                ContentType="application/octet-stream",
                IfNoneMatch="*",
            )
        except ClientError as err:
            code = err.response["Error"]["Code"]
            if code in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
                return False
            raise
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self.bucket,
            Key=f"{prefix}/chunks.json",
            Body=json.dumps(chunks, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        return True


class RagService:
    def __init__(self, store: RagStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    async def ingest(self, user_id: str, text: str, source: str = "manual") -> dict[str, Any]:
        valid_user_id(user_id)
        pieces = chunk_text(text)
        label = clean_source(source)
        documents = [{"text": piece, "source": label, "chunk": index} for index, piece in enumerate(pieces)]
        vectors = await asyncio.to_thread(self.embedder.embed, pieces)

        for attempt in range(MAX_WRITE_ATTEMPTS):
            base = await self.store.get_pointer(user_id)
            index, chunks = await self.store.load(user_id, base) if base else (FaissIndex(self.embedder.dimension), [])
            if len(chunks) + len(documents) > MAX_STORED_CHUNKS:
                raise ChunkingError(f"Index exceeds the {MAX_STORED_CHUNKS} chunk cap.")
            for vector in vectors:
                index.add([vector])
            version = base + 1
            if not await self.store.create_version(user_id, version, index, chunks + documents):
                await asyncio.sleep(0.01 * (attempt + 1))
                continue
            if not await self.store.cas_pointer(user_id, base, version):
                await asyncio.sleep(0.01 * (attempt + 1))
                continue
            return {"ingested_chunks": len(documents), "version": version, "total_chunks": len(chunks) + len(documents)}
        raise RuntimeError("Index update conflict, retry the request.")

    async def query(self, user_id: str, query: str, k: int = 5) -> dict[str, Any]:
        valid_user_id(user_id)
        text = clean_query(query)
        limit = clean_k(k)
        version = await self.store.get_pointer(user_id)
        if version == 0:
            return {"version": 0, "results": []}
        try:
            index, chunks = await self.store.load(user_id, version)
        except ClientError as err:
            if err.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return {"version": version, "results": []}
            raise
        vector = (await asyncio.to_thread(self.embedder.embed, [text]))[0]
        hits = index.search(vector, min(limit, len(chunks)))
        results = []
        for position, score in hits:
            if position >= len(chunks):
                continue
            result = dict(chunks[position])
            result["score"] = round(score, 4)
            results.append(result)
        return {"version": version, "results": results}
