"""Tests for FastAPI HTTP endpoints."""

import time

import httpx
import pytest

from harness.app.auth.tokens import generate_api_key
from harness.app.main import app
from harness.app.store.factory import get_api_key_store


async def _create_test_key(user_id: str = "usr-test-api") -> str:
    full_key, key_id, prefix, key_hash = generate_api_key()
    store = get_api_key_store()
    await store.create_key(
        key_id=key_id,
        user_id=user_id,
        prefix=prefix,
        key_hash=key_hash,
        created_at=int(time.time()),
    )
    return full_key


@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "free-point-harness"}


@pytest.mark.asyncio
async def test_create_and_get_job():
    api_key = await _create_test_key()
    headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": "test-key-api-1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "task": "Please echo something",
            "tools": ["echo"],
        }
        resp = await client.post("/v1/jobs", json=payload, headers=headers)
        assert resp.status_code in (200, 201)
        data = resp.json()
        job_id = data["id"]
        assert job_id.startswith("job-")
        assert data["status"] in ("COMPLETED", "RUNNING", "QUEUED")

        resp2 = await client.post("/v1/jobs", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["id"] == job_id

        get_resp = await client.get(f"/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {api_key}"})
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == job_id


@pytest.mark.asyncio
async def test_declared_tools_limited_and_regex_validated():
    api_key = await _create_test_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        too_many = await client.post(
            "/v1/jobs", json={"task": "x", "tools": ["echo", "utc_time", "rag_query"]}, headers=headers
        )
        assert too_many.status_code == 422

        bad_name = await client.post(
            "/v1/jobs", json={"task": "x", "tools": ["Bad-Tool;"]}, headers=headers
        )
        assert bad_name.status_code == 422


@pytest.mark.asyncio
async def test_model_test_endpoint_rejects_ssrf():
    api_key = await _create_test_key()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/model/test",
            json={"model_url": "https://169.254.169.254/v1"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert resp.status_code == 400
        assert "SSRF" in resp.json()["detail"]
