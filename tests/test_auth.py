"""Comprehensive P3 auth, session, API management, and envelope encryption tests."""

import os

import httpx
import pytest

from harness.app.auth import reset_ip_tracker
from harness.app.crypto.envelope import EnvelopeEncryptor, LocalKeyProvider, redact_secrets
from harness.app.main import app
from harness.app.store.factory import (
    reset_stores,
)


@pytest.fixture(autouse=True)
def clean_stores():
    reset_stores()
    reset_ip_tracker()
    yield
    reset_stores()
    reset_ip_tracker()


@pytest.mark.asyncio
async def test_signup_works_with_email_and_password_only():
    """Signup works with email and password only; no invite needed."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        resp = await client.post(
            "/auth/signup",
            json={"email": "open_signup@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ok"
        assert data["email"] == "open_signup@example.com"
        assert "csrf_token" in data


@pytest.mark.asyncio
async def test_signup_invite_field_ignored():
    """Any invite field passed during signup is safely ignored."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        resp = await client.post(
            "/auth/signup",
            json={
                "email": "invite_ignored@example.com",
                "password": "Password123!",
                "invite": "FP-INVALID-CODE-OR-EXPIRED",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_signup_enumeration_safe_errors():
    """Signup returns uniform errors for existing email and invalid email format (no user enumeration)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        # Create user
        r1 = await client.post(
            "/auth/signup",
            json={"email": "existing_user@example.com", "password": "Password123!"},
        )
        assert r1.status_code == 201

        # Attempt to register existing user with same email (and uppercase variant)
        r2 = await client.post(
            "/auth/signup",
            json={"email": "EXISTING_USER@EXAMPLE.COM", "password": "Password123!"},
        )
        assert r2.status_code == 400
        assert r2.json()["detail"] == "Could not create account."

        # Attempt to register with invalid email format
        r3 = await client.post(
            "/auth/signup",
            json={"email": "not-an-email", "password": "Password123!"},
        )
        assert r3.status_code == 400
        assert r3.json()["detail"] == "Could not create account."


@pytest.mark.asyncio
async def test_no_route_or_page_references_invite_at_runtime():
    """No route or rendered web page references an invite at runtime."""
    # 1. Routes
    for route in app.routes:
        path = getattr(route, "path", "")
        assert "invite" not in path.lower(), f"Route references invite: {path}"

    # 2. Pages
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        pages = ["/", "/signup", "/login", "/dashboard", "/settings", "/approvals"]
        for p in pages:
            resp = await client.get(p, headers={"Accept": "text/html"})
            assert resp.status_code == 200, f"Page {p} returned {resp.status_code}"
            assert "invite" not in resp.text.lower(), f"Page {p} contains 'invite' in rendered text"


@pytest.mark.asyncio
async def test_global_cap_returns_503_and_resets_on_new_utc_day(monkeypatch):
    """Global daily job cap returns 503 with Retry-After when cap reached and resets on a new UTC day."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        s_resp = await client.post(
            "/auth/signup",
            json={"email": "cap_tester@example.com", "password": "Password123!"},
        )
        csrf = s_resp.json()["csrf_token"]
        k_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
        api_key = k_resp.json()["key"]
        headers = {"Authorization": f"Bearer {api_key}"}

        # Set cap to 2
        monkeypatch.setenv("GLOBAL_DAILY_JOB_CAP", "2")

        from datetime import datetime, timezone
        day1 = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 11, 16, 0, 1, 0, tzinfo=timezone.utc)

        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls._current_mock

        MockDatetime._current_mock = day1
        monkeypatch.setattr("harness.app.api.routes.datetime", MockDatetime)

        # 1. First job accepted
        j1 = await client.post(
            "/v1/jobs",
            headers={**headers, "Idempotency-Key": "cap-job-1"},
            json={"task": "Echo 1", "tools": ["echo"]},
        )
        assert j1.status_code in (200, 201)

        # 2. Second job accepted (cap = 2 reached)
        j2 = await client.post(
            "/v1/jobs",
            headers={**headers, "Idempotency-Key": "cap-job-2"},
            json={"task": "Echo 2", "tools": ["echo"]},
        )
        assert j2.status_code in (200, 201)

        # 3. Third job rejected with 503
        j3 = await client.post(
            "/v1/jobs",
            headers={**headers, "Idempotency-Key": "cap-job-3"},
            json={"task": "Echo 3", "tools": ["echo"]},
        )
        assert j3.status_code == 503
        assert "Retry-After" in j3.headers
        retry_after = int(j3.headers["Retry-After"])
        assert retry_after > 0
        assert "global daily job cap" in j3.json()["detail"].lower()

        # 4. Advance to new UTC day: day2
        MockDatetime._current_mock = day2

        # 5. Job accepted on new UTC day
        j4 = await client.post(
            "/v1/jobs",
            headers={**headers, "Idempotency-Key": "cap-job-4"},
            json={"task": "Echo 4", "tools": ["echo"]},
        )
        assert j4.status_code in (200, 201)


@pytest.mark.asyncio
async def test_signup_login_logout_flow_and_cookie_flags():
    """Test signup, login, logout and verification of session cookie security flags."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        email = "testflow@example.com"
        password = "SecurePassword456!"

        # Signup
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": email, "password": password},
        )
        assert signup_resp.status_code == 201

        # Check session cookie flags on signup
        set_cookie_headers = signup_resp.headers.get_list("set-cookie")
        session_cookie = [c for c in set_cookie_headers if "session_id=" in c]
        assert session_cookie, "session_id cookie must be set"
        cookie_str = session_cookie[0].lower()
        assert "httponly" in cookie_str
        assert "secure" in cookie_str
        assert "samesite=strict" in cookie_str

        # Login with correct password
        login_resp = await client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200
        csrf_token = login_resp.json()["csrf_token"]
        assert csrf_token

        # Login with wrong password rejected with uniform message (401)
        bad_login = await client.post(
            "/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        assert bad_login.status_code == 401
        assert bad_login.json()["detail"] == "Invalid credentials."

        # Login with unknown email rejected with same uniform message
        unknown_login = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": password},
        )
        assert unknown_login.status_code == 401
        assert unknown_login.json()["detail"] == "Invalid credentials."

        # Logout with CSRF token
        logout_resp = await client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json()["status"] == "logged_out"

        # Subsequent authenticated call fails after logout
        keys_resp = await client.get("/keys")
        assert keys_resp.status_code == 401


@pytest.mark.asyncio
async def test_csrf_double_submit_protection():
    """State-changing session routes reject missing or mismatched CSRF tokens."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "csrf_test@example.com", "password": "Password789!"},
        )
        valid_csrf = signup_resp.json()["csrf_token"]

        # Missing CSRF token -> 403
        resp_no_csrf = await client.post("/keys")
        assert resp_no_csrf.status_code == 403
        assert "CSRF" in resp_no_csrf.json()["detail"]

        # Wrong CSRF token -> 403
        resp_bad_csrf = await client.post("/keys", headers={"X-CSRF-Token": "wrong-token"})
        assert resp_bad_csrf.status_code == 403

        # Correct CSRF token -> 201
        resp_ok = await client.post("/keys", headers={"X-CSRF-Token": valid_csrf})
        assert resp_ok.status_code == 201


@pytest.mark.asyncio
async def test_api_keys_lifecycle_and_show_once():
    """Test API key creation, max 5 active limit, rotate, revoke, and show-once behavior."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "keys_lifecycle@example.com", "password": "Password789!"},
        )
        csrf = signup_resp.json()["csrf_token"]

        created_keys = []
        for _ in range(5):
            k_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
            assert k_resp.status_code == 201
            k_data = k_resp.json()
            assert k_data["key"].startswith(f"fp_{k_data['prefix']}_")
            created_keys.append(k_data)

        # 6th active key must be rejected (max 5 active per user)
        k_resp6 = await client.post("/keys", headers={"X-CSRF-Token": csrf})
        assert k_resp6.status_code == 400
        assert "maximum active api keys" in k_resp6.json()["detail"].lower()

        # GET /keys returns prefix, created, last_used only (never the secret key or hash)
        list_resp = await client.get("/keys")
        assert list_resp.status_code == 200
        listed_keys = list_resp.json()
        assert len(listed_keys) == 5
        for item in listed_keys:
            assert "prefix" in item
            assert "created_at" in item
            assert "key" not in item
            assert "key_hash" not in item

        # Rotate key: revokes old, returns new
        first_key = created_keys[0]
        rotate_resp = await client.post(f"/keys/{first_key['id']}/rotate", headers={"X-CSRF-Token": csrf})
        assert rotate_resp.status_code == 200
        rotated_data = rotate_resp.json()
        assert rotated_data["id"] != first_key["id"]

        # Revoke a key
        second_key = created_keys[1]
        revoke_resp = await client.post(f"/keys/{second_key['id']}/revoke", headers={"X-CSRF-Token": csrf})
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_wrong_key_and_revoked_key_401():
    """Verify wrong or revoked API keys return 401 on /v1 routes."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        # Provision user and key
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "key_auth@example.com", "password": "Password789!"},
        )
        csrf = signup_resp.json()["csrf_token"]
        key_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
        valid_key = key_resp.json()["key"]
        key_id = key_resp.json()["id"]

        job_payload = {"task": "Echo test", "tools": ["echo"]}

        # 1. Valid key works
        resp_ok = await client.post(
            "/v1/jobs",
            json=job_payload,
            headers={"Authorization": f"Bearer {valid_key}"},
        )
        assert resp_ok.status_code in (200, 201)

        # 2. Completely bogus key -> 401
        resp_bad = await client.post(
            "/v1/jobs",
            json=job_payload,
            headers={"Authorization": "Bearer fp_00000000_fake_key_secret_that_does_not_exist"},
        )
        assert resp_bad.status_code == 401

        # 3. Non-fp prefix -> 401
        resp_malformed = await client.post(
            "/v1/jobs",
            json=job_payload,
            headers={"Authorization": "Bearer some_random_token"},
        )
        assert resp_malformed.status_code == 401

        # 4. Revoke key -> 401
        await client.post(f"/keys/{key_id}/revoke", headers={"X-CSRF-Token": csrf})
        resp_revoked = await client.post(
            "/v1/jobs",
            json=job_payload,
            headers={"Authorization": f"Bearer {valid_key}"},
        )
        assert resp_revoked.status_code == 401
        assert "revoked" in resp_revoked.json()["detail"].lower()


@pytest.mark.asyncio
async def test_route_isolation_keys_never_work_on_session_routes_and_vice_versa():
    """Strict isolation: API keys are rejected on session routes, sessions on /v1 routes."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "isolation@example.com", "password": "Password789!"},
        )
        csrf = signup_resp.json()["csrf_token"]
        key_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
        api_key = key_resp.json()["key"]

    # New client without cookies: present API key to session route /keys
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as no_cookie_client:
        resp = await no_cookie_client.get("/keys", headers={"Authorization": f"Bearer {api_key}"})
        assert resp.status_code == 401
        assert "session" in resp.json()["detail"].lower()

    # Client with session cookies but NO API key calls /v1/jobs -> 401
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as session_client:
        # Log in to get cookie
        await session_client.post(
            "/auth/login",
            json={"email": "isolation@example.com", "password": "Password789!"},
        )
        resp_v1 = await session_client.post("/v1/jobs", json={"task": "Echo", "tools": ["echo"]})
        assert resp_v1.status_code == 401
        assert "api key required" in resp_v1.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rate_limiting_and_quota_headers():
    """Verify rate limit 429, Retry-After header, rate and quota response headers."""
    os.environ["RATE_LIMIT_PER_MINUTE"] = "3"
    os.environ["DAILY_QUOTA"] = "5"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "ratelimit@example.com", "password": "Password789!"},
        )
        csrf = signup_resp.json()["csrf_token"]
        key_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
        api_key = key_resp.json()["key"]

        headers = {"Authorization": f"Bearer {api_key}"}

        # 1st call
        r1 = await client.get("/v1/usage", headers=headers)
        assert r1.status_code == 200
        assert "X-RateLimit-Limit" in r1.headers
        assert "X-RateLimit-Remaining" in r1.headers
        assert "X-RateLimit-Reset" in r1.headers
        assert "X-Quota-Remaining" in r1.headers

        # 2nd and 3rd calls succeed
        r2 = await client.get("/v1/usage", headers=headers)
        assert r2.status_code == 200
        r3 = await client.get("/v1/usage", headers=headers)
        assert r3.status_code == 200

        # 4th call exceeds rate limit (limit=3) -> 429 with Retry-After
        r4 = await client.get("/v1/usage", headers=headers)
        assert r4.status_code == 429
        assert "Retry-After" in r4.headers
        assert "X-RateLimit-Limit" in r4.headers

    # Reset env
    os.environ["RATE_LIMIT_PER_MINUTE"] = "60"
    os.environ["DAILY_QUOTA"] = "1000"


@pytest.mark.asyncio
async def test_idempotency_key_conflict_422():
    """Same Idempotency-Key with different request body returns 422."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "idemp_test@example.com", "password": "Password789!"},
        )
        csrf = signup_resp.json()["csrf_token"]
        key_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
        api_key = key_resp.json()["key"]

        idemp_key = "idemp-unique-999"
        headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": idemp_key}

        # First request
        r1 = await client.post("/v1/jobs", json={"task": "Original task", "tools": ["echo"]}, headers=headers)
        assert r1.status_code == 201
        job_id = r1.json()["id"]

        # Same key, identical body -> 200 OK (idempotent replay)
        r2 = await client.post("/v1/jobs", json={"task": "Original task", "tools": ["echo"]}, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == job_id

        # Same key, DIFFERENT body -> 422 Unprocessable Entity
        r3 = await client.post("/v1/jobs", json={"task": "Modified task", "tools": ["echo"]}, headers=headers)
        assert r3.status_code == 422
        assert "idempotency key reused with different request payload" in r3.json()["detail"].lower()


def test_envelope_encryption_roundtrip_and_context_mismatch():
    """Envelope encryption round-trip and validation that mismatched context fails decryption."""
    local_provider = LocalKeyProvider()
    encryptor = EnvelopeEncryptor(key_provider=local_provider)

    secret_text = "sk-super-secret-model-token-12345"
    user_a = "usr-alpha"
    user_b = "usr-beta"
    tool_name = "model_url"

    encrypted_payload = encryptor.encrypt(plaintext=secret_text, user_id=user_a, tool=tool_name)
    assert secret_text not in encrypted_payload

    # Matching context succeeds
    decrypted = encryptor.decrypt(encrypted_payload, user_id=user_a, tool=tool_name)
    assert decrypted == secret_text

    # Wrong user_id fails
    with pytest.raises(ValueError, match="Decryption failed"):
        encryptor.decrypt(encrypted_payload, user_id=user_b, tool=tool_name)

    # Wrong tool fails
    with pytest.raises(ValueError, match="Decryption failed"):
        encryptor.decrypt(encrypted_payload, user_id=user_a, tool="different_tool")


@pytest.mark.asyncio
async def test_secret_never_present_in_logs_or_get_responses():
    """Secrets are never returned in GET /secrets or GET /keys and are redacted from logs."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        signup_resp = await client.post(
            "/auth/signup",
            json={"email": "secrets_leak_test@example.com", "password": "Password789!"},
        )
        csrf = signup_resp.json()["csrf_token"]

        secret_value = "ghp_super_secret_github_access_token_999"
        put_resp = await client.put(
            "/secrets/github_token",
            json={"value": secret_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert put_resp.status_code == 200

        # GET /secrets must never return the plaintext or ciphertext values
        get_resp = await client.get("/secrets")
        assert get_resp.status_code == 200
        secrets_list = get_resp.json()
        assert len(secrets_list) == 1
        assert secrets_list[0]["name"] == "github_token"
        assert "value" not in secrets_list[0]
        assert "encrypted_value" not in secrets_list[0]
        assert secret_value not in get_resp.text

    # Test log redaction utility
    raw_log = f"Worker connecting to Authorization: Bearer {secret_value} with fp_1234abcd_secretvalue"
    redacted_log = redact_secrets(raw_log, custom_secrets=[secret_value])
    assert secret_value not in redacted_log
    assert "[REDACTED" in redacted_log
