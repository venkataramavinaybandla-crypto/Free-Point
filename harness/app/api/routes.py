"""API routes for jobs, model testing, usage, and health checks."""

import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from ..auth.dependencies import AuthenticatedKey, require_api_key
from ..core.agent_loop import AgentLoop
from ..core.tools import ToolRegistry
from ..crypto.envelope import EnvelopeEncryptor
from ..providers.mock_provider import get_llm_provider
from ..providers.openai_client import OpenAICompatibleProvider
from ..security.ssrf import SSRFError, SSRFValidator, create_safe_client
from ..store.factory import (
    get_idempotency_store,
    get_job_store,
    get_queue,
    get_secret_store,
    get_usage_store,
)

router = APIRouter()
tool_registry = ToolRegistry()


class CreateJobRequest(BaseModel):
    task: str = Field(..., description="The user objective or task for the agent harness")
    tools: List[str] = Field(..., description="Explicitly declared tools allowlist for this job")
    model_url: Optional[str] = Field(None, description="Optional custom OpenAI-compatible endpoint URL")


class JobResponse(BaseModel):
    id: str
    task: str
    declared_tools: List[str]
    status: str
    created_at: str
    updated_at: str
    steps: int
    result: Optional[Any] = None
    error: Optional[str] = None
    approval_artifact: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    model_url: Optional[str] = None
    user_id: Optional[str] = None


class TestModelRequest(BaseModel):
    model_url: Optional[str] = Field(None, description="OpenAI-compatible /v1 endpoint URL (or use stored secret)")
    api_key: Optional[str] = Field(None, description="Optional Bearer token")


class TestModelResponse(BaseModel):
    status: str
    model_url: str
    message: str


class UsageResponse(BaseModel):
    key_id: str
    rate_limit: int
    rate_remaining: int
    rate_reset: int
    daily_quota: int
    quota_remaining: int


@router.get("/health", tags=["System"])
async def health_check() -> Dict[str, str]:
    """Health check endpoint for ALB and container monitors (public, no auth)."""
    return {"status": "ok", "service": "free-point-harness"}


@router.post("/v1/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED, tags=["Jobs"])
async def create_job(
    req: CreateJobRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    caller: AuthenticatedKey = Depends(require_api_key),
) -> JobResponse:
    """Create and enqueue a new harness job."""
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    job_store = get_job_store()
    job_queue = get_queue()
    idempotency_store = get_idempotency_store()
    secret_store = get_secret_store()

    # Determine effective model URL
    effective_model_url = req.model_url
    if not effective_model_url:
        stored_enc = await secret_store.get_secret(caller.user_id, "model_url")
        if stored_enc:
            encryptor = EnvelopeEncryptor()
            effective_model_url = encryptor.decrypt(stored_enc, user_id=caller.user_id, tool="model_url")

    # Wire P1 SSRF guard: re-validate on every job start
    if effective_model_url:
        validator = SSRFValidator()
        try:
            validator.validate_url(effective_model_url)
        except SSRFError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Idempotency check bound to user_id plus request hash
    req_bytes = json.dumps(req.model_dump(), sort_keys=True).encode("utf-8")
    req_hash = hashlib.sha256(req_bytes).hexdigest()

    if idempotency_key:
        composite_key = f"{caller.user_id}:{idempotency_key}"
        existing_record = await idempotency_store.get_record(composite_key)
        if existing_record:
            if existing_record["request_hash"] != req_hash:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Idempotency key reused with different request payload.",
                )
            cached_job = await job_store.get_job(existing_record["response_body"]["id"])
            if cached_job:
                response.status_code = status.HTTP_200_OK
                return JobResponse(**cached_job.model_dump())

    job = await job_store.create_job(
        task=req.task,
        declared_tools=req.tools,
        idempotency_key=idempotency_key,
        model_url=effective_model_url,
        user_id=caller.user_id,
    )
    await job_queue.enqueue(job.id)

    # Resolve optional stored bearer token if using custom model
    bearer_token: Optional[str] = None
    if effective_model_url:
        enc_token = await secret_store.get_secret(caller.user_id, "model_bearer_token")
        if enc_token:
            encryptor = EnvelopeEncryptor()
            bearer_token = encryptor.decrypt(enc_token, user_id=caller.user_id, tool="model_bearer_token")

    provider = (
        OpenAICompatibleProvider(base_url=job.model_url, api_key=bearer_token)
        if job.model_url
        else get_llm_provider()
    )
    loop = AgentLoop(provider=provider, tool_registry=tool_registry)
    updated_job = await loop.run(job, request_id=request_id)
    await job_store.update_job(updated_job)

    # Save idempotency record
    if idempotency_key:
        composite_key = f"{caller.user_id}:{idempotency_key}"
        expires_at = int(time.time()) + 86400
        await idempotency_store.save_record(
            composite_key=composite_key,
            user_id=caller.user_id,
            request_hash=req_hash,
            status_code=status.HTTP_201_CREATED,
            response_body={"id": updated_job.id},
            expires_at=expires_at,
        )

    return JobResponse(**updated_job.model_dump())


@router.get("/v1/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
async def get_job(
    job_id: str,
    caller: AuthenticatedKey = Depends(require_api_key),
) -> JobResponse:
    """Retrieve current job execution status and results."""
    job_store = get_job_store()
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found")
    return JobResponse(**job.model_dump())


@router.post("/v1/model/test", response_model=TestModelResponse, tags=["Model"])
async def test_model_endpoint(
    req: TestModelRequest,
    caller: AuthenticatedKey = Depends(require_api_key),
) -> TestModelResponse:
    """Validate user-provided model endpoint URL for SSRF compliance and reachability."""
    secret_store = get_secret_store()
    target_url = req.model_url
    bearer_token = req.api_key

    if not target_url:
        stored_enc = await secret_store.get_secret(caller.user_id, "model_url")
        if stored_enc:
            encryptor = EnvelopeEncryptor()
            target_url = encryptor.decrypt(stored_enc, user_id=caller.user_id, tool="model_url")

    if not target_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No model_url provided or stored for user.",
        )

    if not bearer_token:
        stored_tok_enc = await secret_store.get_secret(caller.user_id, "model_bearer_token")
        if stored_tok_enc:
            encryptor = EnvelopeEncryptor()
            bearer_token = encryptor.decrypt(stored_tok_enc, user_id=caller.user_id, tool="model_bearer_token")

    validator = SSRFValidator()
    try:
        validator.validate_url(target_url)
    except SSRFError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF validation rejected URL: {e}",
        )

    try:
        async with create_safe_client(validator=validator, timeout=5.0) as client:
            test_url = f"{target_url.rstrip('/')}/models"
            headers = {}
            if bearer_token:
                headers["Authorization"] = f"Bearer {bearer_token}"
            resp = await client.get(test_url, headers=headers)
            message = f"Endpoint reachable, HTTP status: {resp.status_code}"
    except Exception as err:
        message = f"SSRF checks passed, reachability check: {err}"

    return TestModelResponse(
        status="verified",
        model_url=target_url,
        message=message,
    )


@router.get("/v1/usage", response_model=UsageResponse, tags=["API Management"])
async def get_usage(
    caller: AuthenticatedKey = Depends(require_api_key),
) -> UsageResponse:
    """Retrieve the caller API key rate limit and daily quota usage counters."""
    rate_limit = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    daily_quota = int(os.environ.get("DAILY_QUOTA", "1000"))
    usage_store = get_usage_store()
    usage = await usage_store.get_usage(caller.key_id, rate_limit, daily_quota)
    return UsageResponse(**usage)
