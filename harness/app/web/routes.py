"""Session and CSRF protected web API routes.

These routes exist for the browser UI only. They are never registered as model
tools, and every state-changing request passes through ``require_session``
which validates the CSRF double-submit token.
"""

import asyncio
import hmac
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.dependencies import AuthenticatedUser, require_session
from ..core.approvals import approval_hash
from ..crypto.envelope import EnvelopeEncryptor
from ..security.ssrf import SSRFError, SSRFValidator, create_safe_client
from ..store.factory import (
    get_api_key_store,
    get_approval_store,
    get_execute_queue,
    get_secret_store,
    get_usage_store,
)
from .pages import page_response

web_router = APIRouter(tags=["Web UI"], include_in_schema=False)


class ApprovalDecisionRequest(BaseModel):
    """Optional hash the reviewer saw; binding approval to the reviewed payload."""

    payload_hash: Optional[str] = Field(None, min_length=64, max_length=64)


class ApprovalSummaryResponse(BaseModel):
    """Documented response schema for pending approval listings."""

    id: str
    job_id: str
    repo: str
    base: str
    head: str
    title: str
    payload_hash: str
    created_at: int
    expires_at: int
    status: str = "pending"


class ApprovalDetailResponse(ApprovalSummaryResponse):
    """Documented response schema for full approval details."""

    body: str
    diff: str


def _generic(detail: str, code: int) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _is_valid_record(record: Any, user_id: str, now: int) -> bool:
    """Validate that record is a real, unexpired, pending approval for this user."""
    if not isinstance(record, dict):
        return False
    if record.get("user_id") != user_id:
        return False
    if record.get("status") != "pending":
        return False
    if bool(record.get("used", False)):
        return False
    expires_at = record.get("expires_at")
    if not isinstance(expires_at, (int, float)) or int(expires_at) <= now:
        return False
    created_at = record.get("created_at")
    if not isinstance(created_at, (int, float)) or int(created_at) <= 0:
        return False
    for field in ("id", "job_id", "repo", "base", "head", "title"):
        val = record.get(field)
        if not val or not isinstance(val, str) or not val.strip():
            return False
    return True


def _summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(record["id"]),
        "job_id": str(record["job_id"]),
        "repo": str(record["repo"]),
        "base": str(record["base"]),
        "head": str(record["head"]),
        "title": str(record["title"]),
        "payload_hash": str(record.get("payload_hash") or approval_hash(record)),
        "created_at": int(record["created_at"]),
        "expires_at": int(record["expires_at"]),
        "status": str(record.get("status", "pending")),
    }


async def _page_or_session(request: Request) -> Optional[AuthenticatedUser]:
    """Serve the approvals HTML page to browsers and JSON to the UI module."""
    if "text/html" in request.headers.get("accept", ""):
        return None
    # Called directly (not through Depends), so pass the plain header defaults.
    return await require_session(request, authorization=None, x_api_key=None, x_csrf_token=None)


@web_router.get("/approvals")
async def approvals_index(
    current_user: Optional[AuthenticatedUser] = Depends(_page_or_session),
) -> Any:
    if current_user is None:
        return page_response("approvals.html")

    now = int(time.time())
    records = await get_approval_store().list_pending(current_user.user_id, now)
    valid_summaries: List[Dict[str, Any]] = []
    for r in records:
        if _is_valid_record(r, current_user.user_id, now):
            try:
                summary = ApprovalSummaryResponse(**_summary(r))
                valid_summaries.append(summary.model_dump())
            except Exception:
                continue
    return valid_summaries


@web_router.get("/web/usage")
async def web_usage(current_user: AuthenticatedUser = Depends(require_session)) -> Dict[str, Any]:
    """Per-key rate limit and daily quota counters for the dashboard."""
    rate_limit = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    daily_quota = int(os.environ.get("DAILY_QUOTA", "1000"))
    api_key_store = get_api_key_store()
    usage_store = get_usage_store()

    keys = await api_key_store.list_keys_for_user(current_user.user_id)
    usage_keys: List[Dict[str, Any]] = []
    for key in sorted(keys, key=lambda k: k["created_at"], reverse=True):
        counters = await usage_store.get_usage(key["key_id"], rate_limit, daily_quota)
        usage_keys.append(
            {
                "id": key["key_id"],
                "prefix": key["prefix"],
                "created_at": key["created_at"],
                "last_used": key.get("last_used"),
                "revoked": bool(key.get("revoked", False)),
                "rate_limit": counters["rate_limit"],
                "rate_remaining": counters["rate_remaining"],
                "daily_quota": counters["daily_quota"],
                "quota_remaining": counters["quota_remaining"],
            }
        )
    return {"keys": usage_keys}


@web_router.post("/web/model/test")
async def web_model_test(current_user: AuthenticatedUser = Depends(require_session)) -> Dict[str, str]:
    """Reachability test for the stored model endpoint, reusing the P1 SSRF guard."""
    secret_store = get_secret_store()
    encryptor = EnvelopeEncryptor()

    encrypted_url = await secret_store.get_secret(current_user.user_id, "model_url")
    if not encrypted_url:
        return {
            "status": "failed",
            "result": "unreachable",
            "message": "Configure a model URL first.",
        }
    try:
        target_url = encryptor.decrypt(encrypted_url, user_id=current_user.user_id, tool="model_url")
    except ValueError:
        return {
            "status": "failed",
            "result": "unreachable",
            "message": "Stored model configuration could not be read.",
        }

    bearer_token: Optional[str] = None
    encrypted_token = await secret_store.get_secret(current_user.user_id, "model_bearer_token")
    if encrypted_token:
        try:
            bearer_token = encryptor.decrypt(
                encrypted_token, user_id=current_user.user_id, tool="model_bearer_token"
            )
        except ValueError:
            bearer_token = None

    validator = SSRFValidator()
    try:
        validator.validate_url(target_url)
    except SSRFError:
        return {
            "status": "failed",
            "result": "invalid URL blocked by SSRF guard",
            "message": "invalid URL blocked by SSRF guard",
        }

    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    try:
        async with create_safe_client(validator=validator, timeout=5.0) as client:
            response = await client.get(f"{target_url.rstrip('/')}/models", headers=headers)
        if response.status_code in (401, 403):
            return {
                "status": "failed",
                "result": "unauthorized",
                "message": "unauthorized",
            }
        if 200 <= response.status_code < 400:
            return {
                "status": "ok",
                "result": "passed",
                "message": "passed",
            }
        return {
            "status": "failed",
            "result": "unreachable",
            "message": "unreachable",
        }
    except SSRFError:
        return {
            "status": "failed",
            "result": "invalid URL blocked by SSRF guard",
            "message": "invalid URL blocked by SSRF guard",
        }
    except (httpx.TimeoutException, asyncio.TimeoutError):
        return {
            "status": "failed",
            "result": "timeout",
            "message": "timeout",
        }
    except (httpx.ConnectError, httpx.NetworkError, httpx.RequestError, Exception):
        return {
            "status": "failed",
            "result": "unreachable",
            "message": "unreachable",
        }


@web_router.get("/approvals/{approval_id}", response_model=ApprovalDetailResponse)
async def approval_detail(
    approval_id: str, current_user: AuthenticatedUser = Depends(require_session)
) -> ApprovalDetailResponse:
    record = await get_approval_store().get_approval(approval_id, current_user.user_id)
    if not record:
        raise _generic("Approval not found.", status.HTTP_404_NOT_FOUND)
    now = int(time.time())
    if not _is_valid_record(record, current_user.user_id, now):
        raise _generic("Approval not found.", status.HTTP_404_NOT_FOUND)
    detail = _summary(record)
    detail["body"] = str(record.get("body", ""))
    detail["diff"] = str(record.get("diff", ""))
    detail["payload_hash"] = str(record.get("payload_hash") or approval_hash(record))
    return ApprovalDetailResponse(**detail)


@web_router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str,
    req: Optional[ApprovalDecisionRequest] = None,
    current_user: AuthenticatedUser = Depends(require_session),
) -> Dict[str, str]:
    record = await _decide(
        approval_id,
        current_user.user_id,
        "approved",
        req.payload_hash if req else None,
    )
    await get_execute_queue().enqueue(record["job_id"])
    return {"id": approval_id, "status": "approved"}


@web_router.post("/approvals/{approval_id}/reject")
async def reject(
    approval_id: str,
    req: Optional[ApprovalDecisionRequest] = None,
    current_user: AuthenticatedUser = Depends(require_session),
) -> Dict[str, str]:
    await _decide(
        approval_id,
        current_user.user_id,
        "rejected",
        req.payload_hash if req else None,
    )
    return {"id": approval_id, "status": "rejected"}


async def _decide(
    approval_id: str,
    user_id: str,
    decision: str,
    reviewed_hash: Optional[str] = None,
) -> Dict[str, Any]:
    store = get_approval_store()
    record = await store.get_approval(approval_id, user_id)
    if not record:
        raise _generic("Approval not found.", status.HTTP_404_NOT_FOUND)

    # Recompute from the stored payload and let the store compare it atomically.
    expected_hash = approval_hash(record)
    if reviewed_hash is not None and not hmac.compare_digest(reviewed_hash, expected_hash):
        raise _generic("Approval payload hash does not match.", status.HTTP_409_CONFLICT)
    result = await store.decide(approval_id, user_id, decision, expected_hash, int(time.time()))
    if result == "hash_mismatch":
        raise _generic("Approval payload hash does not match.", status.HTTP_409_CONFLICT)
    if result == "expired":
        raise _generic("This approval has expired.", status.HTTP_410_GONE)
    if result == "already_decided":
        raise _generic("This approval has already been used.", status.HTTP_409_CONFLICT)
    if result != "ok":
        raise _generic("Approval not found.", status.HTTP_404_NOT_FOUND)
    return record
