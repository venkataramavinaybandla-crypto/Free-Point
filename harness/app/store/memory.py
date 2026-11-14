"""In-memory job, queue, auth, and api-management storage implementations."""

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    user_id: Optional[str] = None


class JobStore(ABC):
    @abstractmethod
    async def create_job(
        self,
        task: str,
        declared_tools: List[str],
        idempotency_key: Optional[str] = None,
        model_url: Optional[str] = None,
        user_id: Optional[str] = None,
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
        user_id: Optional[str] = None,
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
                user_id=user_id,
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


class InMemoryInviteStore:
    def __init__(self) -> None:
        self._invites: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create_invite(self, code_hash: str, expires_at: int) -> None:
        async with self._lock:
            self._invites[code_hash] = {
                "code_hash": code_hash,
                "expires_at": expires_at,
                "used": False,
                "created_at": int(time.time()),
            }

    async def consume_invite(self, code_hash: str) -> bool:
        async with self._lock:
            invite = self._invites.get(code_hash)
            if not invite:
                return False
            now = int(time.time())
            if invite["used"] or invite["expires_at"] <= now:
                return False
            invite["used"] = True
            invite["consumed_at"] = now
            return True


class InMemoryUserStore:
    def __init__(self) -> None:
        self._users_by_id: Dict[str, Dict[str, Any]] = {}
        self._users_by_email: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create_user(self, user_id: str, email: str, password_hash: str, created_at: int) -> Dict[str, Any]:
        async with self._lock:
            normalized_email = email.strip().lower()
            if normalized_email in self._users_by_email:
                raise ValueError("Email already registered")
            user_doc = {
                "id": user_id,
                "email": normalized_email,
                "password_hash": password_hash,
                "created_at": created_at,
            }
            self._users_by_id[user_id] = user_doc
            self._users_by_email[normalized_email] = user_doc
            return user_doc

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._users_by_email.get(email.strip().lower())

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._users_by_id.get(user_id)


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self, session_hash: str, user_id: str, csrf_token: str, expires_at: int
    ) -> Dict[str, Any]:
        async with self._lock:
            session_doc = {
                "session_hash": session_hash,
                "user_id": user_id,
                "csrf_token": csrf_token,
                "expires_at": expires_at,
                "created_at": int(time.time()),
            }
            self._sessions[session_hash] = session_doc
            return session_doc

    async def get_session(self, session_hash: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            session = self._sessions.get(session_hash)
            if not session:
                return None
            if session["expires_at"] <= int(time.time()):
                del self._sessions[session_hash]
                return None
            return session

    async def delete_session(self, session_hash: str) -> None:
        async with self._lock:
            self._sessions.pop(session_hash, None)


class InMemoryApiKeyStore:
    def __init__(self) -> None:
        self._keys_by_prefix: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create_key(
        self, key_id: str, user_id: str, prefix: str, key_hash: str, created_at: int
    ) -> Dict[str, Any]:
        async with self._lock:
            key_doc = {
                "key_id": key_id,
                "user_id": user_id,
                "prefix": prefix,
                "key_hash": key_hash,
                "created_at": created_at,
                "revoked": False,
                "last_used": None,
            }
            self._keys_by_prefix[prefix] = key_doc
            return key_doc

    async def get_key_by_prefix(self, prefix: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._keys_by_prefix.get(prefix)

    async def get_key_by_id(self, key_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            for k in self._keys_by_prefix.values():
                if k["key_id"] == key_id and k["user_id"] == user_id:
                    return k
            return None

    async def list_keys_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        async with self._lock:
            return [k for k in self._keys_by_prefix.values() if k["user_id"] == user_id]

    async def count_active_keys(self, user_id: str) -> int:
        async with self._lock:
            return sum(1 for k in self._keys_by_prefix.values() if k["user_id"] == user_id and not k["revoked"])

    async def rotate_key(
        self, old_key_id: str, user_id: str, new_key_id: str, new_prefix: str, new_hash: str, created_at: int
    ) -> Optional[Dict[str, Any]]:
        async with self._lock:
            old_doc = None
            for k in self._keys_by_prefix.values():
                if k["key_id"] == old_key_id and k["user_id"] == user_id:
                    old_doc = k
                    break
            if not old_doc:
                return None
            old_doc["revoked"] = True

            new_doc = {
                "key_id": new_key_id,
                "user_id": user_id,
                "prefix": new_prefix,
                "key_hash": new_hash,
                "created_at": created_at,
                "revoked": False,
                "last_used": None,
            }
            self._keys_by_prefix[new_prefix] = new_doc
            return new_doc

    async def revoke_key(self, key_id: str, user_id: str) -> bool:
        async with self._lock:
            for k in self._keys_by_prefix.values():
                if k["key_id"] == key_id and k["user_id"] == user_id:
                    k["revoked"] = True
                    return True
            return False

    async def update_last_used(self, key_id: str, last_used: int) -> None:
        async with self._lock:
            for k in self._keys_by_prefix.values():
                if k["key_id"] == key_id:
                    k["last_used"] = last_used
                    break


class InMemoryUsageStore:
    def __init__(self) -> None:
        self._counters: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def record_and_check(
        self, key_id: str, rate_limit: int, daily_quota: int
    ) -> Tuple[bool, bool, Dict[str, Any]]:
        async with self._lock:
            now = int(time.time())
            window_epoch = (now // 60) * 60
            rate_reset = window_epoch + 60
            retry_after_rate = max(1, rate_reset - now)

            day_epoch = (now // 86400) * 86400
            quota_reset = day_epoch + 86400
            retry_after_quota = max(1, quota_reset - now)

            rate_k = f"rate:{key_id}:{window_epoch}"
            quota_k = f"quota:{key_id}:{day_epoch}"

            rate_count = self._counters.get(rate_k, 0) + 1
            quota_count = self._counters.get(quota_k, 0) + 1

            self._counters[rate_k] = rate_count
            self._counters[quota_k] = quota_count

            rate_exceeded = rate_count > rate_limit
            quota_exceeded = quota_count > daily_quota

            info = {
                "rate_limit": rate_limit,
                "rate_remaining": max(0, rate_limit - rate_count),
                "rate_reset": rate_reset,
                "daily_quota": daily_quota,
                "quota_remaining": max(0, daily_quota - quota_count),
                "retry_after": retry_after_quota if quota_exceeded else retry_after_rate,
            }
            return rate_exceeded, quota_exceeded, info

    async def get_usage(self, key_id: str, rate_limit: int, daily_quota: int) -> Dict[str, Any]:
        async with self._lock:
            now = int(time.time())
            window_epoch = (now // 60) * 60
            day_epoch = (now // 86400) * 86400
            rate_count = self._counters.get(f"rate:{key_id}:{window_epoch}", 0)
            quota_count = self._counters.get(f"quota:{key_id}:{day_epoch}", 0)
            return {
                "key_id": key_id,
                "rate_limit": rate_limit,
                "rate_remaining": max(0, rate_limit - rate_count),
                "rate_reset": window_epoch + 60,
                "daily_quota": daily_quota,
                "quota_remaining": max(0, daily_quota - quota_count),
            }


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_record(self, composite_key: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            rec = self._records.get(composite_key)
            if not rec:
                return None
            if rec["expires_at"] <= int(time.time()):
                del self._records[composite_key]
                return None
            return rec

    async def save_record(
        self,
        composite_key: str,
        user_id: str,
        request_hash: str,
        status_code: int,
        response_body: Any,
        expires_at: int,
    ) -> None:
        async with self._lock:
            self._records[composite_key] = {
                "composite_key": composite_key,
                "user_id": user_id,
                "request_hash": request_hash,
                "status_code": status_code,
                "response_body": response_body,
                "expires_at": expires_at,
            }


class InMemorySecretStore:
    def __init__(self) -> None:
        self._secrets: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def set_secret(self, user_id: str, name: str, encrypted_value: str, updated_at: int) -> None:
        async with self._lock:
            self._secrets[(user_id, name)] = {
                "user_id": user_id,
                "name": name,
                "encrypted_value": encrypted_value,
                "updated_at": updated_at,
            }

    async def get_secret(self, user_id: str, name: str) -> Optional[str]:
        async with self._lock:
            doc = self._secrets.get((user_id, name))
            if doc:
                return doc["encrypted_value"]
            return None

    async def list_secrets(self, user_id: str) -> List[Dict[str, Any]]:
        async with self._lock:
            results = []
            for (uid, name), doc in self._secrets.items():
                if uid == user_id:
                    results.append({"name": name, "updated_at": doc["updated_at"]})
            return sorted(results, key=lambda x: x["name"])
