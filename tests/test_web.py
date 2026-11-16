"""P3b tests: pages, static assets, security headers, CSRF, approvals, secret hygiene."""

import asyncio
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.app.auth.dependencies import reset_ip_tracker
from harness.app.core.approvals import approval_hash
from harness.app.main import app
from harness.app.store.factory import (
    get_approval_store,
    get_execute_queue,
    reset_stores,
)
from harness.app.web.routes import ApprovalDetailResponse, ApprovalSummaryResponse

PAGES = ["/", "/signup", "/login", "/dashboard", "/settings", "/approvals"]


@pytest.fixture()
def client():
    reset_stores()
    reset_ip_tracker()
    with TestClient(app, base_url="https://test") as test_client:
        yield test_client
    reset_stores()
    reset_ip_tracker()


def signup(client: TestClient) -> dict:
    response = client.post(
        "/auth/signup",
        json={
            "email": f"web-{time.time_ns()}@example.com",
            "password": "Password123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def seed_approval(user_id: str, **overrides) -> dict:
    now = int(time.time())
    record = {
        "id": "apr-test-1",
        "user_id": user_id,
        "job_id": "job-test-1",
        "repo": "acme/vector-core",
        "base": "main",
        "head": "feat/vector-caching",
        "title": "Add a vector cache warmup",
        "body": "Implements the warmup described in issue 842.",
        "diff": "--- a/cache.py\n+++ b/cache.py\n+await cache.warmupIndex()\n",
        "created_at": now,
        "expires_at": now + 3600,
        "status": "pending",
        "used": False,
    }
    record.update(overrides)
    if "payload_hash" not in overrides:
        record["payload_hash"] = approval_hash(record)
    asyncio.run(get_approval_store().create_approval(record))
    return record


def test_security_headers_and_pages(client: TestClient):
    for path in PAGES:
        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html")
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "img-src 'self'" in csp
        assert "unsafe-inline" not in csp
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Strict-Transport-Security" in response.headers
        assert 'rel="icon"' in response.text
        assert "Free Point" in response.text


def test_hsts_only_behind_https():
    reset_stores()
    with TestClient(app, base_url="http://test") as plain:
        assert "Strict-Transport-Security" not in plain.get("/health").headers


def test_assets_served(client: TestClient):
    image = client.get("/static/assets/header_image.png")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")

    favicon = client.get("/static/favicon.svg")
    assert favicon.status_code == 200
    assert "svg" in favicon.headers["content-type"]

    stylesheet = client.get("/static/css/app.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")

    module = client.get("/static/js/api.js")
    assert module.status_code == 200
    assert "javascript" in module.headers["content-type"]


def test_csrf_required_on_web_routes(client: TestClient):
    auth = signup(client)
    csrf = auth["csrf_token"]

    assert client.post("/web/model/test").status_code == 403
    assert client.post("/approvals/apr-1/approve").status_code == 403
    assert client.post("/approvals/apr-1/reject").status_code == 403

    headers = {"X-CSRF-Token": csrf}
    assert client.post("/approvals/apr-1/approve", headers=headers).status_code == 404
    assert client.post("/approvals/apr-1/reject", headers=headers).status_code == 404
    assert client.get("/web/usage").status_code == 200


def test_usage_reports_real_key_counters(client: TestClient):
    auth = signup(client)
    csrf = auth["csrf_token"]
    created = client.post("/keys", headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201

    usage = client.get("/web/usage")
    assert usage.status_code == 200
    keys = usage.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["id"] == created.json()["id"]
    assert keys[0]["daily_quota"] == 1000
    assert keys[0]["quota_remaining"] == 1000
    assert keys[0]["revoked"] is False


def test_secrets_never_returned_in_responses(client: TestClient, monkeypatch):
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "true")
    auth = signup(client)
    csrf = auth["csrf_token"]
    secret_url = "http://localhost:9/v1"
    token = "sk-live-TOP-SECRET-token-123"

    for name, value in (("model_url", secret_url), ("model_bearer_token", token)):
        response = client.put(f"/secrets/{name}", json={"value": value}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200

    secrets = client.get("/secrets")
    assert secrets.status_code == 200
    assert token not in secrets.text
    assert secret_url not in secrets.text
    assert {item["name"] for item in secrets.json()} == {"model_url", "model_bearer_token"}

    page = client.get("/settings", headers={"Accept": "text/html"})
    assert token not in page.text
    assert secret_url not in page.text

    test_result = client.post("/web/model/test", headers={"X-CSRF-Token": csrf})
    assert token not in test_result.text
    assert secret_url not in test_result.text


def test_approval_list_detail_and_single_use(client: TestClient):
    auth = signup(client)
    csrf = auth["csrf_token"]
    record = seed_approval(auth["user_id"])

    listing = client.get("/approvals")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [record["id"]]
    assert record["diff"] not in listing.text

    detail = client.get(f"/approvals/{record['id']}")
    assert detail.status_code == 200
    assert detail.json()["diff"] == record["diff"]
    assert detail.json()["body"] == record["body"]
    assert detail.json()["payload_hash"] == approval_hash(record)

    headers = {"X-CSRF-Token": csrf}
    approved = client.post(f"/approvals/{record['id']}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert asyncio.run(get_execute_queue().dequeue(timeout=0.05)) == record["job_id"]

    reused = client.post(f"/approvals/{record['id']}/approve", headers=headers)
    assert reused.status_code == 409
    assert client.get("/approvals").json() == []


def test_approval_hash_mismatch_rejected(client: TestClient):
    auth = signup(client)
    csrf = auth["csrf_token"]
    record = seed_approval(auth["user_id"], payload_hash="0" * 64)

    response = client.post(f"/approvals/{record['id']}/approve", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409
    assert "hash" in response.json()["detail"].lower()
    assert asyncio.run(get_execute_queue().dequeue(timeout=0.01)) is None


def test_approval_expiry_rejected(client: TestClient):
    auth = signup(client)
    csrf = auth["csrf_token"]
    record = seed_approval(auth["user_id"], expires_at=int(time.time()) - 1)

    assert client.get("/approvals").json() == []
    response = client.post(f"/approvals/{record['id']}/approve", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 410


def test_approval_bound_to_user(client: TestClient):
    signup(client)
    record = seed_approval("usr-someone-else")
    assert client.get("/approvals").json() == []
    assert client.get(f"/approvals/{record['id']}").status_code == 404


def test_approvals_list_returns_documented_schema_and_filters_invalid_records(client: TestClient):
    auth = signup(client)
    user_id = auth["user_id"]
    now = int(time.time())

    # 1. Valid pending approval
    valid_record = seed_approval(user_id)

    # 2. Invalidate/junk records directly in the store
    store = get_approval_store()
    # Expired approval
    asyncio.run(
        store.create_approval(
            {
                "id": f"appr-expired-{now}",
                "user_id": user_id,
                "job_id": f"job-exp-{now}",
                "repo": "repo/exp",
                "base": "main",
                "head": "patch",
                "title": "Expired",
                "payload_hash": "a" * 64,
                "created_at": now - 1000,
                "expires_at": now - 10,
                "status": "pending",
                "used": False,
            }
        )
    )
    # Decided / used approval
    asyncio.run(
        store.create_approval(
            {
                "id": f"appr-used-{now}",
                "user_id": user_id,
                "job_id": f"job-used-{now}",
                "repo": "repo/used",
                "base": "main",
                "head": "patch",
                "title": "Used",
                "payload_hash": "b" * 64,
                "created_at": now,
                "expires_at": now + 900,
                "status": "approved",
                "used": True,
            }
        )
    )
    # Other user's approval
    asyncio.run(
        store.create_approval(
            {
                "id": f"appr-other-{now}",
                "user_id": "usr-other-tenant",
                "job_id": f"job-other-{now}",
                "repo": "repo/other",
                "base": "main",
                "head": "patch",
                "title": "Other",
                "payload_hash": "c" * 64,
                "created_at": now,
                "expires_at": now + 900,
                "status": "pending",
                "used": False,
            }
        )
    )
    # Corrupt / junk record missing fields
    asyncio.run(
        store.create_approval(
            {
                "id": f"appr-junk-{now}",
                "user_id": user_id,
                "job_id": "",
                "repo": "",
                "base": "main",
                "head": "patch",
                "title": "",
                "created_at": now,
                "expires_at": now + 900,
                "status": "pending",
            }
        )
    )

    # Fetch list
    res = client.get("/approvals")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["id"] == valid_record["id"]

    # Validate schema with Pydantic model
    validated = ApprovalSummaryResponse(**items[0])
    assert validated.id == valid_record["id"]
    assert validated.job_id == valid_record["job_id"]
    assert validated.repo == valid_record["repo"]
    assert validated.base == valid_record["base"]
    assert validated.head == valid_record["head"]
    assert validated.title == valid_record["title"]
    assert validated.created_at == valid_record["created_at"]
    assert validated.expires_at == valid_record["expires_at"]
    assert validated.status == "pending"


def test_approvals_empty_state_payload(client: TestClient):
    signup(client)
    res = client.get("/approvals")
    assert res.status_code == 200
    assert res.json() == []

    # Verify that the web UI empty state text matches "No pending approvals"
    with open("web/static/js/approvals.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert 'setListNotice("No pending approvals")' in js


def test_approvals_schema_contract_and_no_missing_fields(client: TestClient):
    import re

    auth = signup(client)
    record = seed_approval(auth["user_id"])

    # 1. API contract verification
    list_res = client.get("/approvals")
    assert list_res.status_code == 200
    detail_res = client.get(f"/approvals/{record['id']}")
    assert detail_res.status_code == 200

    # Ensure neither API output contains undefined or Never
    assert "undefined" not in list_res.text
    assert "Never" not in list_res.text
    assert "undefined" not in detail_res.text
    assert "Never" not in detail_res.text

    summary = ApprovalSummaryResponse(**list_res.json()[0])
    detail = ApprovalDetailResponse(**detail_res.json())
    assert summary.id == record["id"]
    assert detail.id == record["id"]

    # 2. JS-free contract check: read web/static/js/approvals.js and extract field accesses
    with open("web/static/js/approvals.js", "r", encoding="utf-8") as f:
        js = f.read()

    approval_fields = set(re.findall(r"\bapproval\.([a-zA-Z0-9_]+)\b", js))
    detail_fields = set(re.findall(r"\bdetail\.([a-zA-Z0-9_]+)\b", js))

    for field in approval_fields:
        assert (
            field in ApprovalSummaryResponse.model_fields
        ), f"UI reads 'approval.{field}' which is missing from ApprovalSummaryResponse"

    for field in detail_fields:
        assert (
            field in ApprovalDetailResponse.model_fields
        ), f"UI reads 'detail.{field}' which is missing from ApprovalDetailResponse"

    # 3. HTML contract: verify disabled buttons in detail pane
    page_res = client.get("/approvals", headers={"Accept": "text/html"})
    assert page_res.status_code == 200
    html = page_res.text
    assert 'id="approve-button"' in html
    assert 'id="reject-button"' in html
    assert 'id="approve-button" class="btn btn-primary" type="button" disabled' in html
    assert 'id="reject-button" class="btn btn-danger" type="button" disabled' in html


def test_save_then_list_shows_name_with_no_value(client: TestClient, monkeypatch):
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "true")
    auth = signup(client)
    csrf = auth["csrf_token"]
    secret_url = "http://localhost:9/v1?private_param=should_not_leak"
    token = "sk-super-secret-model-token-999"

    # Save both model_url and model_bearer_token
    res_url = client.put("/secrets/model_url", json={"value": secret_url}, headers={"X-CSRF-Token": csrf})
    assert res_url.status_code == 200
    res_token = client.put("/secrets/model_bearer_token", json={"value": token}, headers={"X-CSRF-Token": csrf})
    assert res_token.status_code == 200

    # List secrets
    secrets = client.get("/secrets")
    assert secrets.status_code == 200
    data = secrets.json()
    assert len(data) == 2
    names = {item["name"] for item in data}
    assert names == {"model_url", "model_bearer_token"}

    for item in data:
        assert "updated_at" in item
        assert "value" not in item
        assert "token" not in item

    # Neither value appears in the response
    assert token not in secrets.text
    assert secret_url not in secrets.text
    assert "private_param" not in secrets.text


def test_failed_save_keeps_form_data(client: TestClient, monkeypatch):
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "false")
    auth = signup(client)
    csrf = auth["csrf_token"]

    # Attempting to save a prohibited private IP URL fails SSRF
    bad_url = "http://127.0.0.1:8080/v1"
    response = client.put("/secrets/model_url", json={"value": bad_url}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400
    assert "SSRF" in response.text

    # Verify nothing was persisted
    secrets = client.get("/secrets").json()
    assert not any(s["name"] == "model_url" for s in secrets)

    # In settings.js, when api() throws, the input is NOT cleared and generic error is shown
    with open("web/static/js/settings.js", "r", encoding="utf-8") as f:
        js_code = f.read()
    assert 'urlField.value = "";' in js_code
    assert 'tokenField.value = "";' in js_code
    assert "saveFailed = true" in js_code
    assert "Could not save the model settings." in js_code


def test_delete_secret_removes_it(client: TestClient, monkeypatch):
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "true")
    auth = signup(client)
    csrf = auth["csrf_token"]

    client.put("/secrets/model_bearer_token", json={"value": "my-token"}, headers={"X-CSRF-Token": csrf})
    assert any(s["name"] == "model_bearer_token" for s in client.get("/secrets").json())

    # DELETE without CSRF fails
    no_csrf = client.delete("/secrets/model_bearer_token")
    assert no_csrf.status_code == 403

    # DELETE with CSRF succeeds
    deleted = client.delete("/secrets/model_bearer_token", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    assert deleted.json() == {"name": "model_bearer_token", "status": "deleted"}

    # Verified removed
    assert not any(s["name"] == "model_bearer_token" for s in client.get("/secrets").json())


class _MockSafeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._response


def test_test_connection_errors_map_to_reason_classes(client: TestClient, monkeypatch):
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "true")
    auth = signup(client)
    csrf = auth["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    # Store model URL and token
    client.put("/secrets/model_url", json={"value": "http://localhost:9/v1?token=query_leak"}, headers=headers)
    client.put("/secrets/model_bearer_token", json={"value": "sk-test-token-val"}, headers=headers)

    # 1. SSRF Guard failure
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "false")
    res_ssrf = client.post("/web/model/test", headers=headers)
    assert res_ssrf.status_code == 200
    assert res_ssrf.json()["result"] == "invalid URL blocked by SSRF guard"
    assert res_ssrf.json()["status"] == "failed"
    assert "sk-test-token-val" not in res_ssrf.text
    assert "query_leak" not in res_ssrf.text

    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "true")

    # 2. Unauthorized
    monkeypatch.setattr(
        "harness.app.web.routes.create_safe_client",
        lambda **kwargs: _MockSafeClient(response=httpx.Response(401)),
    )
    res_auth = client.post("/web/model/test", headers=headers)
    assert res_auth.status_code == 200
    assert res_auth.json()["result"] == "unauthorized"
    assert res_auth.json()["status"] == "failed"
    assert "sk-test-token-val" not in res_auth.text

    # 3. Timeout
    monkeypatch.setattr(
        "harness.app.web.routes.create_safe_client",
        lambda **kwargs: _MockSafeClient(exc=httpx.TimeoutException("Connection timed out")),
    )
    res_timeout = client.post("/web/model/test", headers=headers)
    assert res_timeout.status_code == 200
    assert res_timeout.json()["result"] == "timeout"
    assert res_timeout.json()["status"] == "failed"
    assert "sk-test-token-val" not in res_timeout.text

    # 4. Unreachable
    monkeypatch.setattr(
        "harness.app.web.routes.create_safe_client",
        lambda **kwargs: _MockSafeClient(exc=httpx.ConnectError("Connection refused")),
    )
    res_unreach = client.post("/web/model/test", headers=headers)
    assert res_unreach.status_code == 200
    assert res_unreach.json()["result"] == "unreachable"
    assert res_unreach.json()["status"] == "failed"
    assert "sk-test-token-val" not in res_unreach.text

    # 5. Passed
    monkeypatch.setattr(
        "harness.app.web.routes.create_safe_client",
        lambda **kwargs: _MockSafeClient(response=httpx.Response(200, json={"object": "list"})),
    )
    res_passed = client.post("/web/model/test", headers=headers)
    assert res_passed.status_code == 200
    assert res_passed.json()["result"] == "passed"
    assert res_passed.json()["status"] == "ok"
    assert "sk-test-token-val" not in res_passed.text


def test_token_absent_from_responses_and_logs(client: TestClient, caplog, monkeypatch):
    monkeypatch.setenv("DEV_ALLOW_INSECURE_LOCAL", "true")
    auth = signup(client)
    csrf = auth["csrf_token"]
    secret_url = "http://localhost:9/v1?key=sensitive_query_param"
    token = "sk-super-secret-token-xyz-12345"

    with caplog.at_level(logging.DEBUG):
        client.put("/secrets/model_url", json={"value": secret_url}, headers={"X-CSRF-Token": csrf})
        client.put("/secrets/model_bearer_token", json={"value": token}, headers={"X-CSRF-Token": csrf})

        res_secrets = client.get("/secrets")
        res_page = client.get("/settings", headers={"Accept": "text/html"})
        res_test = client.post("/web/model/test", headers={"X-CSRF-Token": csrf})

    for response_text in (res_secrets.text, res_page.text, res_test.text):
        assert token not in response_text
        assert "sensitive_query_param" not in response_text

    assert token not in caplog.text
    assert "sensitive_query_param" not in caplog.text


def test_settings_ui_elements_and_js_contract(client: TestClient):
    signup(client)
    page = client.get("/settings", headers={"Accept": "text/html"})
    assert page.status_code == 200
    html = page.text

    assert 'id="model-form"' in html
    assert 'id="model-url"' in html
    assert 'id="model-token"' in html
    assert 'id="save-model"' in html
    assert 'id="test-model"' in html
    assert 'id="test-result"' in html
    assert 'id="secret-list"' in html

    with open("web/static/js/settings.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "••••••••" in js
    assert '"Saved"' in js
    assert '"Replace"' in js
    assert '"Delete"' in js
    assert '"DELETE"' in js
    for reason in ["unreachable", "unauthorized", "invalid URL blocked by SSRF guard", "timeout", "passed"]:
        assert reason in js
