"""Abstract store interfaces for Free Point."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from ..core.state import JobStatus
from .memory import Job, JobStore, Queue


class InviteStore(ABC):
    @abstractmethod
    async def create_invite(self, code_hash: str, expires_at: int) -> None:
        pass

    @abstractmethod
    async def consume_invite(self, code_hash: str) -> bool:
        """Atomically validate and mark an invite used. Returns False if invalid/used/expired."""
        pass


class UserStore(ABC):
    @abstractmethod
    async def create_user(self, user_id: str, email: str, password_hash: str, created_at: int) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        pass


class SessionStore(ABC):
    @abstractmethod
    async def create_session(
        self, session_hash: str, user_id: str, csrf_token: str, expires_at: int
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_session(self, session_hash: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def delete_session(self, session_hash: str) -> None:
        pass


class ApiKeyStore(ABC):
    @abstractmethod
    async def create_key(
        self, key_id: str, user_id: str, prefix: str, key_hash: str, created_at: int
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_key_by_prefix(self, prefix: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_key_by_id(self, key_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def list_keys_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def count_active_keys(self, user_id: str) -> int:
        pass

    @abstractmethod
    async def rotate_key(
        self, old_key_id: str, user_id: str, new_key_id: str, new_prefix: str, new_hash: str, created_at: int
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def revoke_key(self, key_id: str, user_id: str) -> bool:
        pass

    @abstractmethod
    async def update_last_used(self, key_id: str, last_used: int) -> None:
        pass


class UsageStore(ABC):
    @abstractmethod
    async def record_and_check(
        self, key_id: str, rate_limit: int, daily_quota: int
    ) -> Tuple[bool, bool, Dict[str, Any]]:
        """Increment counters and check limits. Returns (rate_exceeded, quota_exceeded, info)."""
        pass

    @abstractmethod
    async def get_usage(self, key_id: str, rate_limit: int, daily_quota: int) -> Dict[str, Any]:
        pass


class IdempotencyStore(ABC):
    @abstractmethod
    async def get_record(self, composite_key: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def save_record(
        self,
        composite_key: str,
        user_id: str,
        request_hash: str,
        status_code: int,
        response_body: Any,
        expires_at: int,
    ) -> None:
        pass


class ApprovalStore(ABC):
    @abstractmethod
    async def create_approval(self, approval: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_approval(self, approval_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def list_pending(self, user_id: str, now: int) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def decide(
        self, approval_id: str, user_id: str, decision: str, expected_hash: str, now: int
    ) -> str:
        """Atomically consume a pending approval.

        Returns one of: ``ok``, ``not_found``, ``hash_mismatch``,
        ``already_decided`` or ``expired``.
        """
        pass


class SecretStore(ABC):
    @abstractmethod
    async def set_secret(self, user_id: str, name: str, encrypted_value: str, updated_at: int) -> None:
        pass

    @abstractmethod
    async def get_secret(self, user_id: str, name: str) -> Optional[str]:
        pass

    @abstractmethod
    async def list_secrets(self, user_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def delete_secret(self, user_id: str, name: str) -> bool:
        pass


class JobCounterStore(ABC):
    @abstractmethod
    async def increment_and_check(self, date_str: str, cap: int) -> Tuple[bool, int]:
        """Atomically increment the counter for date_str and check against cap.

        Returns (cap_exceeded, current_count).
        """
        pass

    @abstractmethod
    async def get_count(self, date_str: str) -> int:
        pass

    @abstractmethod
    async def reset(self) -> None:
        pass


__all__ = [
    "ApiKeyStore",
    "ApprovalStore",
    "IdempotencyStore",
    "InviteStore",
    "Job",
    "JobCounterStore",
    "JobStatus",
    "JobStore",
    "Queue",
    "SecretStore",
    "SessionStore",
    "UsageStore",
    "UserStore",
]
