"""Tests for Idempotency-Key deduplication."""

import pytest

from harness.app.store.memory import InMemoryJobStore


@pytest.mark.asyncio
async def test_idempotency_key_deduplication():
    store = InMemoryJobStore()

    job1 = await store.create_job(
        task="run analysis",
        declared_tools=["echo"],
        idempotency_key="idemp-key-001",
    )

    job2 = await store.create_job(
        task="different task text with same key",
        declared_tools=["echo"],
        idempotency_key="idemp-key-001",
    )

    assert job1.id == job2.id
    assert job2.task == "run analysis"


@pytest.mark.asyncio
async def test_different_idempotency_keys_create_different_jobs():
    store = InMemoryJobStore()
    job1 = await store.create_job(task="task 1", declared_tools=["echo"], idempotency_key="key-A")
    job2 = await store.create_job(task="task 2", declared_tools=["echo"], idempotency_key="key-B")
    assert job1.id != job2.id
