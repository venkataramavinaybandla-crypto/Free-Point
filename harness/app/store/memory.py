"""In-memory job storage and queue implementations."""

import asyncio
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.state import JobStatus


class Job(BaseModel):
    id: str = Field(default_factory=lambda: f"job-{uuid.uuid4().hex[:12]}")
    task: str
    declared_tools: List[str]
    status: JobStatus = JobStatus.QUEUED
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    steps: int = 0
    result: Optional[Any] = None
    error: Optional[str] = None
    approval_artifact: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    model_url: Optional[str] = None


class JobStore(ABC):
    @abstractmethod
    async def create_job(
        self,
        task: str,
        declared_tools: List[str],
        idempotency_key: Optional[str] = None,
        model_url: Optional[str] = None,
    ) -> Job:
        pass

    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[Job]:
        pass

    @abstractmethod
    async def update_job(self, job: Job) -> Job:
        pass

    @abstractmethod
    async def get_by_idempotency_key(self, key: str) -> Optional[Job]:
        pass


class Queue(ABC):
    @abstractmethod
    async def enqueue(self, job_id: str) -> None:
        pass

    @abstractmethod
    async def dequeue(self, timeout: Optional[float] = None) -> Optional[str]:
        pass


class InMemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._idempotency_map: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        task: str,
        declared_tools: List[str],
        idempotency_key: Optional[str] = None,
        model_url: Optional[str] = None,
    ) -> Job:
        async with self._lock:
            if idempotency_key and idempotency_key in self._idempotency_map:
                existing_id = self._idempotency_map[idempotency_key]
                return self._jobs[existing_id]

            job = Job(
                task=task,
                declared_tools=declared_tools,
                idempotency_key=idempotency_key,
                model_url=model_url,
            )
            self._jobs[job.id] = job
            if idempotency_key:
                self._idempotency_map[idempotency_key] = job.id
            return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_job(self, job: Job) -> Job:
        async with self._lock:
            job.updated_at = datetime.now(timezone.utc).isoformat()
            self._jobs[job.id] = job
            return job

    async def get_by_idempotency_key(self, key: str) -> Optional[Job]:
        async with self._lock:
            job_id = self._idempotency_map.get(key)
            if job_id:
                return self._jobs.get(job_id)
            return None


class InMemoryQueue(Queue):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            if timeout:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except (asyncio.TimeoutError, TimeoutError):
            return None
