"""Smoke test for P3 auth & API management (httpx + ASGITransport, no new deps)."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Repo root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["STORE_BACKEND"] = "memory"
os.environ["RATE_LIMIT_PER_MINUTE"] = "5"
os.environ["DAILY_QUOTA"] = "1000"

import httpx  # noqa: E402

from harness.app.auth.tokens import generate_invite_code, hash_sha256  # noqa: E402
from harness.app.main import app  # noqa: E402
from harness.app.store.factory import get_invite_store, reset_stores  # noqa: E402

# ── helpers ──────────────────────────────────────────────────────────────

results: List[Tuple[str, str]] = []


def record(name: str, passed: bool) -> None:
    results.append((name, "PASS" if passed else "FAIL"))


def cookies_from_headers(response: httpx.Response) -> Dict[str, Dict[str, Any]]:
    """Parse Set-Cookie headers into {name: {value, flags...}}."""
    parsed: Dict[str, Dict[str, Any]] = {}
    for raw in response.headers.multi_items():
        if raw[0].lower() != "set-cookie":
            continue
        parts = raw[1].split(";")
        nv = parts[0].strip().split("=", 1)
        name = nv[0]
        value = nv[1] if len(nv) > 1 else ""
        flags = {}
        for p in parts[1:]:
            p = p.strip().lower()
            if "=" in p:
                k, v = p.split("=", 1)
                flags[k.strip()] = v.strip()
            elif p:
                flags[p] = True
        parsed[name] = {"value": value, **flags}
    return parsed


async def main() -> None:
    reset_stores()

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        # ── 1. Create invite ─────────────────────────────────────────────
        invite_code = generate_invite_code()
        invite_hash = hash_sha256(invite_code)
        store = get_invite_store()
        import time

        await store.create_invite(code_hash=invite_hash, expires_at=int(time.time()) + 3600)

        # ── 2. Signup ────────────────────────────────────────────────────
        r = await c.post(
            "/auth/signup",
            json={"invite": invite_code, "email": "smoke@test.dev", "password": "Str0ngP@ss!"},
        )
        record("signup", r.status_code == 201)
        signup_data = r.json()
        csrf = signup_data.get("csrf_token", "")

        # Grab raw cookies from headers (Secure cookies won't populate jar over http)
        cookies = cookies_from_headers(r)
        session_cookie = cookies.get("session_id", {})
        session_id = session_cookie.get("value", "")

        # ── 3. Cookie flags ──────────────────────────────────────────────
        has_httponly = "httponly" in session_cookie
        has_secure = "secure" in session_cookie
        has_samesite = session_cookie.get("samesite", "") == "strict"
        record("cookie_flags", has_httponly and has_secure and has_samesite)

        # ── 4. Login ─────────────────────────────────────────────────────
        r = await c.post(
            "/auth/login",
            json={"email": "smoke@test.dev", "password": "Str0ngP@ss!"},
        )
        record("login", r.status_code == 200)
        login_data = r.json()
        csrf = login_data.get("csrf_token", "")
        login_cookies = cookies_from_headers(r)
        session_id = login_cookies.get("session_id", {}).get("value", session_id)

        # Helper: session headers for state-changing routes
        def session_headers() -> Dict[str, str]:
            return {"X-CSRF-Token": csrf, "Cookie": f"session_id={session_id}; csrf_token={csrf}"}

        def session_get_headers() -> Dict[str, str]:
            return {"Cookie": f"session_id={session_id}; csrf_token={csrf}"}

        # ── 5. Create API key (show once) ────────────────────────────────
        r = await c.post("/keys", headers=session_headers())
        record("create_key", r.status_code == 201 and "key" in r.json())
        key_data = r.json()
        api_key = key_data.get("key", "")
        key_id = key_data.get("id", "")

        # ── 6. List keys: secret NOT shown ───────────────────────────────
        r = await c.get("/keys", headers=session_get_headers())
        key_list_text = r.text
        record("key_show_once", r.status_code == 200 and api_key not in key_list_text)

        # ── 7. GET /v1/usage with valid key ──────────────────────────────
        r = await c.get("/v1/usage", headers={"X-API-Key": api_key})
        record("usage_200", r.status_code == 200)

        # ── 8. Wrong key → 401 ───────────────────────────────────────────
        r = await c.get("/v1/usage", headers={"X-API-Key": "fp_bad1_notreal"})
        record("wrong_key_401", r.status_code == 401)

        # ── 9. Rotate key, old key → 401 ────────────────────────────────
        r = await c.post(f"/keys/{key_id}/rotate", headers=session_headers())
        record("rotate_key", r.status_code == 200 and "key" in r.json())
        rotated_data = r.json()
        new_api_key = rotated_data.get("key", "")
        new_key_id = rotated_data.get("id", "")

        r = await c.get("/v1/usage", headers={"X-API-Key": api_key})
        record("old_key_after_rotate_401", r.status_code == 401)

        # ── 10. Revoke key, then use → 401 ──────────────────────────────
        r = await c.post(f"/keys/{new_key_id}/revoke", headers=session_headers())
        record("revoke_key", r.status_code == 200)

        r = await c.get("/v1/usage", headers={"X-API-Key": new_api_key})
        record("revoked_key_401", r.status_code == 401)

        # Create a fresh key for remaining tests
        r = await c.post("/keys", headers=session_headers())
        fresh_key = r.json().get("key", "")

        # ── 11. API key on session route → rejected ──────────────────────
        r = await c.get("/keys", headers={"X-API-Key": fresh_key})
        record("key_on_session_rejected", r.status_code == 401)

        # ── 12. Session cookie on /v1 route → rejected ───────────────────
        r = await c.get("/v1/usage", headers=session_get_headers())
        record("session_on_v1_rejected", r.status_code == 401)

        # ── 13. Rate limit 429 + Retry-After ─────────────────────────────
        # RATE_LIMIT_PER_MINUTE=5; we already used some calls. Burn through.
        got_429 = False
        retry_after_present = False
        for _ in range(10):
            r = await c.get("/v1/usage", headers={"X-API-Key": fresh_key})
            if r.status_code == 429:
                got_429 = True
                retry_after_present = "retry-after" in r.headers
                break
        record("rate_limit_429", got_429 and retry_after_present)

        # ── 14. Idempotency conflict 422 ─────────────────────────────────
        # Need a fresh key that isn't rate limited (new minute window or new key)
        # Create another key for idempotency test
        r = await c.post("/keys", headers=session_headers())
        idem_key = r.json().get("key", "")

        idem_header = "test-idempotency-abc"
        body1 = {"task": "task-a", "tools": ["tool1"]}
        r1 = await c.post(
            "/v1/jobs",
            json=body1,
            headers={"X-API-Key": idem_key, "Idempotency-Key": idem_header},
        )
        # First call should succeed (201 or 200)
        first_ok = r1.status_code in (200, 201)

        body2 = {"task": "task-b-different", "tools": ["tool2"]}
        r2 = await c.post(
            "/v1/jobs",
            json=body2,
            headers={"X-API-Key": idem_key, "Idempotency-Key": idem_header},
        )
        record("idempotency_422", first_ok and r2.status_code == 422)

        # ── 15. PUT /secrets then GET /secrets ───────────────────────────
        secret_value = "ghp_SuperSecretGitHubToken12345"
        r = await c.put(
            "/secrets/github_token",
            json={"value": secret_value},
            headers=session_headers(),
        )
        put_ok = r.status_code == 200

        r = await c.get("/secrets", headers=session_get_headers())
        get_ok = r.status_code == 200
        secrets_body = r.text
        names_present = "github_token" in secrets_body
        value_absent = secret_value not in secrets_body
        record("secrets_names_only", put_ok and get_ok and names_present and value_absent)

        # ── 16. Secret not in any previous response text ─────────────────
        # We check the GET /secrets response doesn't contain the plaintext
        record("secret_not_leaked", secret_value not in secrets_body)

    # ── Print results table ──────────────────────────────────────────────
    max_w = max(len(n) for n, _ in results)
    print(f"\n{'Check':<{max_w}}  Result")
    print(f"{'-'*max_w}  ------")
    for name, status in results:
        print(f"{name:<{max_w}}  {status}")


if __name__ == "__main__":
    asyncio.run(main())
