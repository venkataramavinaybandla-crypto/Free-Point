"""Demo client demonstrating open signup (no invite code), API key creation, and job execution."""

import argparse
import sys
import time
import uuid

import httpx

DEFAULT_HARNESS_URL = "http://127.0.0.1:8000"


def run_demo(harness_url: str, email: str, password: str) -> None:
    base_url = harness_url.rstrip("/")
    print(f"==> Connecting to Free Point at {base_url}")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        # 1. Health check
        health = client.get("/health")
        print(f"==> Health: {health.status_code} {health.json()}")

        # 2. Open signup (email + password only, no invite code required)
        print(f"==> Registering new account: {email} (open signup, no invite)")
        signup_resp = client.post(
            "/auth/signup",
            json={"email": email, "password": password},
        )
        if signup_resp.status_code != 201:
            print(f"Signup failed ({signup_resp.status_code}): {signup_resp.text}")
            sys.exit(1)

        auth_data = signup_resp.json()
        csrf_token = auth_data["csrf_token"]
        print(f"==> Account created! user_id={auth_data['user_id']}")

        # 3. Create an API key via session
        print("==> Issuing new API key...")
        key_resp = client.post("/keys", headers={"X-CSRF-Token": csrf_token})
        if key_resp.status_code != 201:
            print(f"Key creation failed ({key_resp.status_code}): {key_resp.text}")
            sys.exit(1)

        key_data = key_resp.json()
        api_key = key_data["key"]
        print(f"==> API key issued: {key_data['prefix']} (secret: {api_key[:10]}...)")

        # 4. Check usage quotas
        headers = {"Authorization": f"Bearer {api_key}"}
        usage_resp = client.get("/v1/usage", headers=headers)
        print(f"==> Usage: {usage_resp.json()}")

        # 5. Submit a job through the harness
        idempotency_key = str(uuid.uuid4())
        print(f"==> Submitting job with Idempotency-Key: {idempotency_key}...")
        job_resp = client.post(
            "/v1/jobs",
            headers={**headers, "Idempotency-Key": idempotency_key},
            json={"task": "Echo verification test", "tools": ["echo"]},
        )
        if job_resp.status_code not in (200, 201):
            print(f"Job creation failed ({job_resp.status_code}): {job_resp.text}")
            sys.exit(1)

        job_data = job_resp.json()
        print(f"==> Job accepted! id={job_data['id']} status={job_data['status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Free Point Open Signup Demo Client")
    parser.add_argument("--url", default=DEFAULT_HARNESS_URL, help="Harness URL (default: http://127.0.0.1:8000)")
    parser.add_argument(
        "--email", default=f"demo-{int(time.time())}@example.com", help="Email address for open signup"
    )
    parser.add_argument("--password", default="DemoPassword123!", help="Password (min 8 characters)")
    args = parser.parse_args()

    run_demo(args.url, args.email, args.password)


if __name__ == "__main__":
    main()
