"""Canonical hashing for human approval payloads."""

import hashlib
import json
import time
from typing import Any, Mapping

APPROVAL_FIELDS = (
    "user_id",
    "job_id",
    "repo",
    "base",
    "head",
    "title",
    "body",
    "diff",
)


def approval_hash(payload: Mapping[str, Any]) -> str:
    """Hash every field of a PR payload in a fixed order.

    The digest binds an approval to the exact user, job and diff content. It is
    recomputed at approve time and compared with the stored value before the
    execute message is enqueued.
    """
    canonical = json.dumps(
        [str(payload.get(field, "")) for field in APPROVAL_FIELDS],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


APPROVAL_TTL_SECONDS = 900
MAX_APPROVAL_RECORD_BYTES = 350_000


def build_approval_record(
    user_id: str,
    job_id: str,
    payload: Mapping[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    """Build the single-use, expiring record for one PR artifact."""
    timestamp = int(time.time()) if now is None else now
    record: dict[str, Any] = {
        "id": f"appr-{job_id}",
        "user_id": user_id,
        "job_id": job_id,
        "repo": str(payload.get("repo", "")),
        "base": str(payload.get("base", "")),
        "head": str(payload.get("head", "")),
        "title": str(payload.get("title", "")),
        "body": str(payload.get("body", "")),
        "diff": str(payload.get("diff", "")),
        "status": "pending",
        "used": False,
        "created_at": timestamp,
        "expires_at": timestamp + APPROVAL_TTL_SECONDS,
    }
    record["payload_hash"] = approval_hash(record)
    return record


async def record_approval(job: Any, artifact: Any) -> dict[str, Any] | None:
    """Persist an approval record for a gated write artifact.

    Jobs without a tenant (legacy local runs) cannot produce an approvable
    record; production always sets the user id before the loop starts.
    """
    if not getattr(job, "user_id", None):
        return None
    payload = artifact.payload if hasattr(artifact, "payload") else dict(artifact)
    record = build_approval_record(job.user_id, job.id, payload)
    if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_APPROVAL_RECORD_BYTES:
        raise ValueError("Approval payload exceeds the storage limit.")

    from ..store.factory import get_approval_store

    await get_approval_store().create_approval(record)
    return record
