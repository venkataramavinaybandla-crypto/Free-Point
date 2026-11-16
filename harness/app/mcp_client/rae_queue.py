"""Main-worker side of the RAE queue contract.

The harness submits only ``job_id`` + ``s3_key`` and polls the results prefix.
The RAE host never receives a user token, KMS grant, or DynamoDB table.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any


class RaeQueueError(RuntimeError):
    """RAE submission or result polling failed."""


class RaeQueueClient:
    def __init__(
        self,
        queue_url: str,
        bucket: str,
        results_prefix: str = "results/",
        sqs_client: Any = None,
        s3_client: Any = None,
        timeout: float = 60.0,
    ) -> None:
        if not queue_url or not bucket:
            raise ValueError("RAE queue URL and bucket are required.")
        self.queue_url = queue_url
        self.bucket = bucket
        self.results_prefix = results_prefix
        self._sqs = sqs_client
        self._s3 = s3_client
        self._timeout = timeout
        self._chunks: list[dict[str, Any]] = []

    def _sqs_client(self) -> Any:
        if self._sqs is None:
            import boto3

            self._sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        return self._sqs

    def _s3_client(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        return self._s3

    async def analyze(self, job_id: str, s3_key: str) -> dict[str, Any]:
        if not s3_key.startswith("artifacts/"):
            raise RaeQueueError("RAE key must be inside the artifacts prefix.")
        await asyncio.to_thread(
            self._sqs_client().send_message,
            QueueUrl=self.queue_url,
            MessageBody=json.dumps({"job_id": job_id, "s3_key": s3_key}),
        )
        key = f"{self.results_prefix}{job_id}.json"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                response = await asyncio.to_thread(self._s3_client().get_object, Bucket=self.bucket, Key=key)
            except Exception:
                await asyncio.sleep(0.5)
                continue
            body = await asyncio.to_thread(response["Body"].read)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as err:
                raise RaeQueueError("RAE result was invalid.") from err
            if not isinstance(payload, dict):
                raise RaeQueueError("RAE result was invalid.")
            chunks = payload.get("chunks")
            self._chunks = [item for item in chunks if isinstance(item, dict)] if isinstance(chunks, list) else []
            return payload
        raise RaeQueueError("RAE analysis timed out.")

    def get_chunk(self, index: int) -> dict[str, Any]:
        if index < 0 or index >= len(self._chunks):
            raise RaeQueueError("RAE chunk index is out of range.")
        chunk = self._chunks[index]
        return {
            "index": index,
            "total_chunks": len(self._chunks),
            "title": str(chunk.get("title", "chunk")),
            "text": str(chunk.get("text", "")),
        }


def build_rae_queue() -> RaeQueueClient | None:
    queue_url = os.environ.get("RAE_QUEUE_URL", "")
    bucket = os.environ.get("RAE_ARTIFACTS_BUCKET", "")
    if not queue_url or not bucket:
        return None
    return RaeQueueClient(queue_url, bucket, os.environ.get("RAE_RESULTS_PREFIX", "results/"))
