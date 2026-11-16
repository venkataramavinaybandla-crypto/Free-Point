"""RAE analysis orchestration and per-analysis chunk storage."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Protocol

from .archive import remove_tree
from .chunker import build_chunks
from .sandbox import (
    MAX_INPUT_BYTES,
    SandboxError,
    clone_repository,
    run_sandbox,
    staged_temp_dir,
)

MAX_STORED_ANALYSES = 64


class S3Reader(Protocol):
    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]: ...


Runner = Callable[[Path, str, str], dict[str, Any]]
Cloner = Callable[[str, Path], Path]


class AnalysisStore:
    def __init__(self) -> None:
        self._chunks: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()

    def put(self, user_id: str, chunks: list[dict[str, Any]]) -> None:
        self._chunks[user_id] = chunks
        self._chunks.move_to_end(user_id)
        while len(self._chunks) > MAX_STORED_ANALYSES:
            self._chunks.popitem(last=False)

    def get_latest(self, user_id: str) -> list[dict[str, Any]] | None:
        return self._chunks.get(user_id)


class RaeService:
    def __init__(
        self,
        s3_client: S3Reader | None = None,
        runner: Runner | None = None,
        cloner: Cloner | None = None,
        store: AnalysisStore | None = None,
    ) -> None:
        self._s3 = s3_client
        self._runner = runner or (lambda root, target, kind: run_sandbox(root, target, kind))
        self._cloner = cloner or clone_repository
        self.store = store or AnalysisStore()
        self.bucket = os.environ.get("RAE_ARTIFACTS_BUCKET", "free-point-artifacts")
        self.prefix = os.environ.get("RAE_ARTIFACTS_PREFIX", "artifacts/")
        self.image = os.environ.get("RAE_SANDBOX_IMAGE", "")

    def _s3_client(self) -> S3Reader:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        return self._s3

    @staticmethod
    def _run(runner: Runner, root: Path, target: str, kind: str) -> dict[str, Any]:
        return runner(root, target, kind)

    def _materialize_s3(self, key: str, root: Path) -> Path:
        if not key.startswith(self.prefix) or key.startswith("/") or ".." in Path(key).parts:
            raise SandboxError("S3 key must be inside the artifacts prefix.")
        response = self._s3_client().get_object(Bucket=self.bucket, Key=key)
        size = int(response.get("ContentLength", 0))
        if size > MAX_INPUT_BYTES:
            raise SandboxError("Artifact exceeds the input size cap.")
        body = response["Body"].read(MAX_INPUT_BYTES + 1)
        if len(body) > MAX_INPUT_BYTES:
            raise SandboxError("Artifact exceeds the input size cap.")
        target = root / "sample.bin"
        target.write_bytes(body)
        target.chmod(0o644)
        return target

    def _finish(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        sections = payload.get("sections")
        if not isinstance(sections, list) or not sections:
            raise SandboxError("Analysis returned no sections.")
        normalized = [
            {"title": str(item.get("title", "section"))[:120], "text": str(item.get("text", ""))}
            for item in sections
            if isinstance(item, dict)
        ]
        if not normalized:
            raise SandboxError("Analysis returned no sections.")
        chunks = build_chunks(normalized)
        analysis_id = f"rae-{uuid.uuid4().hex[:12]}"
        self.store.put(user_id, chunks)
        summary = f"{normalized[0]['title']}: {normalized[0]['text'][:1200]}"
        return {
            "analysis_id": analysis_id,
            "summary": summary,
            "chunk_count": len(chunks),
            "chunks": [{"index": 0, **chunks[0]}],
        }

    async def analyze_s3_key(self, user_id: str, key: str) -> dict[str, Any]:
        root = staged_temp_dir()
        try:
            self._materialize_s3(key, root)
            payload = await asyncio.to_thread(self._run, self._runner, root, "sample.bin", "file")
            return self._finish(user_id, payload)
        finally:
            remove_tree(root)

    async def analyze_repository(self, user_id: str, repo_url: str) -> dict[str, Any]:
        root = staged_temp_dir()
        try:
            await asyncio.to_thread(self._cloner, repo_url, root)
            payload = await asyncio.to_thread(self._run, self._runner, root, "repo", "repo")
            return self._finish(user_id, payload)
        finally:
            remove_tree(root)

    def get_chunk(self, user_id: str, index: int) -> dict[str, Any]:
        chunks = self.store.get_latest(user_id)
        if chunks is None:
            raise SandboxError("No analysis is available for this user.")
        if index < 0 or index >= len(chunks):
            raise SandboxError("Chunk index is out of range.")
        return {"index": index, "total_chunks": len(chunks), "title": chunks[index]["title"], "text": chunks[index]["text"]}
