"""Tests for FastAPI HTTP endpoints."""

import httpx
import pytest

from harness.app.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "free-point-harness"}


@pytest.mark.asyncio
async def test_create_and_get_job():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "task": "Please echo something",
            "tools": ["echo"],
        }
        resp = await client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "test-key-api-1"})
        assert resp.status_code in (200, 201)
        data = resp.json()
        job_id = data["id"]
        assert job_id.startswith("job-")
        assert data["status"] in ("COMPLETED", "RUNNING", "QUEUED")

        resp2 = await client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "test-key-api-1"})
        assert resp2.status_code == 200
        assert resp2.json()["id"] == job_id

        get_resp = await client.get(f"/v1/jobs/{job_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == job_id


@pytest.mark.asyncio
async def test_model_test_endpoint_rejects_ssrf():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/model/test", json={"model_url": "https://169.254.169.254/v1"})
        assert resp.status_code == 400
        assert "SSRF" in resp.json()["detail"]
