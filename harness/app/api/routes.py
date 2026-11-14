"""API routes for jobs, model testing, and health checks."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from ..core.agent_loop import AgentLoop
from ..core.tools import ToolRegistry
from ..providers.mock_provider import get_llm_provider
from ..providers.openai_client import OpenAICompatibleProvider
from ..security.ssrf import SSRFError, SSRFValidator, create_safe_client
from ..store.memory import InMemoryJobStore, InMemoryQueue
from .dependencies import get_api_key

router = APIRouter()

job_store = InMemoryJobStore()
job_queue = InMemoryQueue()
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


class TestModelRequest(BaseModel):
    model_url: str = Field(..., description="OpenAI-compatible /v1 endpoint URL")
    api_key: Optional[str] = Field(None, description="Optional Bearer token")


class TestModelResponse(BaseModel):
    status: str
    model_url: str
    message: str


@router.get("/health", tags=["System"])
async def health_check() -> Dict[str, str]:
    """Health check endpoint for ALB and container monitors."""
    return {"status": "ok", "service": "free-point-harness"}


@router.post("/v1/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED, tags=["Jobs"])
async def create_job(
    req: CreateJobRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    api_key: Optional[str] = Depends(get_api_key),
) -> JobResponse:
    """Create and enqueue a new harness job."""
    request_id = f"req-{uuid.uuid4().hex[:8]}"

    if req.model_url:
        validator = SSRFValidator()
        try:
            validator.validate_url(req.model_url)
        except SSRFError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if idempotency_key:
        existing = await job_store.get_by_idempotency_key(idempotency_key)
        if existing:
            response.status_code = status.HTTP_200_OK
            return JobResponse(**existing.model_dump())

    job = await job_store.create_job(
        task=req.task,
        declared_tools=req.tools,
        idempotency_key=idempotency_key,
        model_url=req.model_url,
    )
    await job_queue.enqueue(job.id)

    provider = OpenAICompatibleProvider(base_url=job.model_url) if job.model_url else get_llm_provider()
    loop = AgentLoop(provider=provider, tool_registry=tool_registry)
    updated_job = await loop.run(job, request_id=request_id)
    await job_store.update_job(updated_job)

    return JobResponse(**updated_job.model_dump())


@router.get("/v1/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
async def get_job(
    job_id: str,
    api_key: Optional[str] = Depends(get_api_key),
) -> JobResponse:
    """Retrieve current job execution status and results."""
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found")
    return JobResponse(**job.model_dump())


@router.post("/v1/model/test", response_model=TestModelResponse, tags=["Model"])
async def test_model_endpoint(
    req: TestModelRequest,
    api_key: Optional[str] = Depends(get_api_key),
) -> TestModelResponse:
    """Validate user-provided model endpoint URL for SSRF compliance and reachability."""
    validator = SSRFValidator()
    try:
        validator.validate_url(req.model_url)
    except SSRFError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF validation rejected URL: {e}",
        )

    try:
        async with create_safe_client(validator=validator, timeout=5.0) as client:
            test_url = f"{req.model_url.rstrip('/')}/models"
            headers = {}
            if req.api_key:
                headers["Authorization"] = f"Bearer {req.api_key}"
            resp = await client.get(test_url, headers=headers)
            message = f"Endpoint reachable, HTTP status: {resp.status_code}"
    except Exception as err:
        message = f"SSRF checks passed, reachability check: {err}"

    return TestModelResponse(
        status="verified",
        model_url=req.model_url,
        message=message,
    )
