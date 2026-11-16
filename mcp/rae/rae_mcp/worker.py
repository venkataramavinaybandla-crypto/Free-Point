"""RAE queue worker: S3 artifact in, S3 result out, no user secrets.

The process deliberately has no KMS or DynamoDB client. It receives only an
artifact key and job id, runs the same sandboxed analyzer, and writes chunked
results under the results prefix for the main worker to read.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from .service import RaeService


def _clients() -> tuple[Any, Any]:
    import boto3

    region = os.environ.get("AWS_REGION", "ap-south-1")
    return boto3.client("sqs", region_name=region), boto3.client("s3", region_name=region)


async def process_analysis(service: RaeService, s3: Any, message: dict[str, Any]) -> str:
    job_id = str(message.get("job_id", ""))
    key = str(message.get("s3_key", ""))
    if not job_id or not key:
        raise ValueError("RAE message is missing job_id or s3_key.")
    result = await service.analyze_s3_key("rae-worker", key)
    chunks = service.store.get_latest("rae-worker") or []
    payload = {"job_id": job_id, "analysis": result, "chunks": chunks}
    bucket = os.environ.get("RAE_ARTIFACTS_BUCKET", "free-point-artifacts")
    results_prefix = os.environ.get("RAE_RESULTS_PREFIX", "results/")
    results_key = f"{results_prefix}{job_id}.json"
    s3.put_object(
        Bucket=bucket,
        Key=results_key,
        Body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
    )
    return results_key


async def run_worker(max_messages: int | None = None) -> None:
    queue_url = os.environ.get("RAE_QUEUE_URL", "")
    if not queue_url:
        raise SystemExit("RAE_QUEUE_URL is required")
    sqs, s3 = _clients()
    service = RaeService()
    handled = 0
    while max_messages is None or handled < max_messages:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10,
            VisibilityTimeout=300,
        )
        for message in response.get("Messages", []):
            try:
                body = json.loads(message.get("Body", "{}"))
                await process_analysis(service, s3, body)
            except Exception:
                handled += 1
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            handled += 1


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
