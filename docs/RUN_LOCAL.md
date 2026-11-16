# Running Free Point Locally

This guide explains how to start Free Point services locally and run the demo workflow with open signup.

## Overview

Free Point is an Agent Harness as a Service. With open signup enabled:
- **Signup**: Users register with `email` and `password` only. No invite code is required anywhere in the flow.
- **Cost Guard**: A global daily job cap (`GLOBAL_DAILY_JOB_CAP`, default 200) protects compute resources by returning `503 Service Unavailable` with a `Retry-After` header when reached.
- **Per-Key Quotas**: Fixed-window rate limiting (per minute) and daily quotas per API key apply before the global cap.

## Quick Start

### 1. Start Services

To launch the stack with Docker Compose:

```powershell
./scripts/demo_up.ps1 -Build
```

Or via the docker command directly:

```bash
export INTERNAL_MCP_SECRET="local-dev-mcp-secret-32-chars-long-minimum"
docker compose up --build
```

For running without Docker directly from source:

```bash
export STORE_BACKEND="memory"
export LLM_PROVIDER="mock"
export DEV_ALLOW_INSECURE_LOCAL="true"
export INTERNAL_MCP_SECRET="local-dev-mcp-secret-32-chars-long-minimum"
uvicorn harness.app.main:app --host 0.0.0.0 --port 8000
```

### 2. Run the Demo Flow

The demo script demonstrates the full lifecycle:
1. Account registration via open signup (`POST /auth/signup` with email + password)
2. API key generation (`POST /keys`)
3. Usage verification (`GET /v1/usage`)
4. Job submission (`POST /v1/jobs`)

```bash
python scripts/demo_client.py
```

### 3. Open Signup API

#### Register Account
```bash
curl -X POST http://127.0.0.1:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "developer@example.com", "password": "SecurePassword123!"}'
```

Returns `201 Created` with a session cookie and CSRF token.

#### Issue API Key
```bash
curl -X POST http://127.0.0.1:8000/keys \
  -H "Cookie: session_id=<SESSION_ID>" \
  -H "X-CSRF-Token: <CSRF_TOKEN>"
```

#### Submit a Job
```bash
curl -X POST http://127.0.0.1:8000/v1/jobs \
  -H "Authorization: Bearer fp_<prefix>_<secret>" \
  -H "Idempotency-Key: <UUID>" \
  -H "Content-Type: application/json" \
  -d '{"task": "Echo check", "tools": ["echo"]}'
```

### 4. Configuration & Environment Variables

- `GLOBAL_DAILY_JOB_CAP`: Maximum accepted jobs across all tenants per UTC day (default: `200`). At the cap, returns `503` with `Retry-After` until midnight UTC.
- `RATE_LIMIT_PER_MINUTE`: Per-API-key 60-second window request cap (default: `60`).
- `DAILY_QUOTA`: Per-API-key 24-hour job quota (default: `1000`).
- `STORE_BACKEND`: `memory` for local development, `dynamodb` for AWS deployments.
