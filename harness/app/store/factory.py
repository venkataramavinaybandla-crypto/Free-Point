"""Store factory selecting between in-memory and DynamoDB storage."""

import os
from typing import Optional

from .base import (
    ApiKeyStore,
    IdempotencyStore,
    InviteStore,
    JobStore,
    Queue,
    SecretStore,
    SessionStore,
    UsageStore,
    UserStore,
)
from .dynamo import (
    DynamoDBApiKeyStore,
    DynamoDBIdempotencyStore,
    DynamoDBInviteStore,
    DynamoDBJobStore,
    DynamoDBQueue,
    DynamoDBSecretStore,
    DynamoDBSessionStore,
    DynamoDBUsageStore,
    DynamoDBUserStore,
)
from .memory import (
    InMemoryApiKeyStore,
    InMemoryIdempotencyStore,
    InMemoryInviteStore,
    InMemoryJobStore,
    InMemoryQueue,
    InMemorySecretStore,
    InMemorySessionStore,
    InMemoryUsageStore,
    InMemoryUserStore,
)

_invite_store: Optional[InviteStore] = None
_user_store: Optional[UserStore] = None
_session_store: Optional[SessionStore] = None
_api_key_store: Optional[ApiKeyStore] = None
_usage_store: Optional[UsageStore] = None
_idempotency_store: Optional[IdempotencyStore] = None
_secret_store: Optional[SecretStore] = None
_job_store: Optional[JobStore] = None
_queue: Optional[Queue] = None


def is_dynamodb() -> bool:
    return os.environ.get("STORE_BACKEND", "memory").lower() == "dynamodb"


def get_invite_store() -> InviteStore:
    global _invite_store
    if _invite_store is None:
        _invite_store = DynamoDBInviteStore() if is_dynamodb() else InMemoryInviteStore()
    return _invite_store


def get_user_store() -> UserStore:
    global _user_store
    if _user_store is None:
        _user_store = DynamoDBUserStore() if is_dynamodb() else InMemoryUserStore()
    return _user_store


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = DynamoDBSessionStore() if is_dynamodb() else InMemorySessionStore()
    return _session_store


def get_api_key_store() -> ApiKeyStore:
    global _api_key_store
    if _api_key_store is None:
        _api_key_store = DynamoDBApiKeyStore() if is_dynamodb() else InMemoryApiKeyStore()
    return _api_key_store


def get_usage_store() -> UsageStore:
    global _usage_store
    if _usage_store is None:
        _usage_store = DynamoDBUsageStore() if is_dynamodb() else InMemoryUsageStore()
    return _usage_store


def get_idempotency_store() -> IdempotencyStore:
    global _idempotency_store
    if _idempotency_store is None:
        _idempotency_store = DynamoDBIdempotencyStore() if is_dynamodb() else InMemoryIdempotencyStore()
    return _idempotency_store


def get_secret_store() -> SecretStore:
    global _secret_store
    if _secret_store is None:
        _secret_store = DynamoDBSecretStore() if is_dynamodb() else InMemorySecretStore()
    return _secret_store


def get_job_store() -> JobStore:
    global _job_store
    if _job_store is None:
        _job_store = DynamoDBJobStore() if is_dynamodb() else InMemoryJobStore()
    return _job_store


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = DynamoDBQueue() if is_dynamodb() else InMemoryQueue()
    return _queue


def reset_stores() -> None:
    """Reset singletons for test isolation."""
    global _invite_store, _user_store, _session_store, _api_key_store
    global _usage_store, _idempotency_store, _secret_store, _job_store, _queue
    _invite_store = None
    _user_store = None
    _session_store = None
    _api_key_store = None
    _usage_store = None
    _idempotency_store = None
    _secret_store = None
    _job_store = None
    _queue = None
