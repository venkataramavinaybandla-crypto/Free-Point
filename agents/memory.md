# Free Point: Project Memory

Running record of decisions, corrections, and status. Update it whenever something is decided or changes. Never delete history; mark items superseded.

## Locked decisions
- D1: Product is a **harness service with an API key**, not a built-in-feature agent.
- D2: Problem statement **#19** (API Management and Microservices).
- D3: Language and frameworks: **Python, FastAPI, FastMCP**.
- D4: Model on Colab via **llama.cpp llama-server + ngrok**; harness uses an OpenAI-compatible `base_url`, so local or hosted models also work.
- D5: Runtime on **Docker + EC2 Auto Scaling behind an ALB** (required by the problem statement set). Not Lambda.
- D6: Egress via **NAT instance (t3.nano, x86)** plus free S3 and DynamoDB gateway endpoints. No interface endpoints.
- D7: **HTTPS from P2** (ACM cert, 443 listener, 80 redirect).
- D8: **SQS job queue**, job states, DLQ, visibility timeout at least 1.5x job cap.
- D9: Approval flow: worker acks at AWAITING_APPROVAL; approval enqueues an execute message; hash-bound, single-use, expiring.
- D10: Every GitHub write needs approval; draft PRs only; branch protection on target repos.
- D11: User tokens KMS-envelope-encrypted in DynamoDB; our own secrets in SSM; no per-user Secrets Manager.
- D12: RAE runs on its **own instance** with zero KMS permission and its own queue.
- D13: Embeddings via **fastembed (ONNX)** baked into the image; app instance **t3.medium**.
- D14: FAISS indexes versioned in S3, pointer flipped with a DynamoDB conditional write.
- D15: Tools declared explicitly in each request; regex only validates.
- D16: MockLLM built in **P1**; load tests run against it.
- D17: Cut line: Core = harness, auth, GitHub, RAG. Nice = Gmail. Drop first = RAE.
- D18: Plan **frozen at v4**. No more review rounds; fix issues only when they block a phase.
- D19: Single-use invite codes stored as SHA-256 hash only with expiry; created via `scripts/create_invite.py`. (Superseded by D35)
- D20: Passwords hashed with argon2id (`argon2-cffi`); random 32-byte sessions stored hashed with expiry, httpOnly + Secure + SameSite=Strict cookies, CSRF double-submit token on state-changing session routes.
- D21: API keys follow format `fp_<prefix>_<secret>` (32+ bytes entropy, shown once, stored prefix + SHA-256 hash, max 5 active per user).
- D22: Route isolation enforced: `require_api_key` for all `/v1` routes and `require_session` for web session routes; keys never work on session routes and vice versa.
- D23: Per-key fixed-window rate limit (60s) and daily quota (86400s) with DynamoDB atomic ADD counters and `X-RateLimit-*`/`X-Quota-Remaining` headers on all `/v1` responses; `GET /v1/usage`.
- D24: Idempotency bound to `user_id` plus request hash; same key + different payload returns 422.
- D25: KMS envelope encryption (`GenerateDataKey` + `AES-256-GCM`), EncryptionContext = `user_id` + `tool`; `LocalKeyProvider` for dev/test; secrets decrypted at job time only and redacted from logs.
- D26: Storage backend abstracted with `STORE_BACKEND=memory|dynamodb`.
- D27: P3b web UI is plain HTML + CSS + ES modules served by the FastAPI app (`/static` via StaticFiles, pages via FileResponse), with strict self-only CSP and all fonts/assets self-hosted. Approvals live in `free-point-approvals` (single-use, TTL, `user_id-index`); approving enqueues through the existing `Queue` interface to the execute queue. Browser pages never use localStorage/sessionStorage.
- D28: Landing copy is audited against `agents/Architecture.json`; false claims (per-invocation Docker sandboxes, merge/Slack/Drive features, fabricated metrics) were rewritten. Audit record: `docs/web-copy-audit.md`.
- D29: Internal MCP calls are HMAC-signed (`INTERNAL_MCP_SECRET`) with tenant, job, timestamp, token, allowlist and base branch in the signed payload; tool servers reject unsigned, stale or tampered calls. GitHub tokens are decrypted per call from the `github` secret (context user_id+github) and travel only in the signed header.
- D30: GitHub writes are split into `create_draft_pr` (pure artifact, no API call) and `execute_draft_pr` (approval consumer only). Branches are deterministic (`freepoint/<job_id>`), PRs are drafts, existing branch/PR is reused, and main/master is never a write target. Repo allowlists are stored encrypted per user.
- D31: RAG indexes are per-user FAISS files under `index/<user_id>/v<n>` in S3; version objects are created with `If-None-Match: *` and the DynamoDB pointer is flipped with a conditional update and retry. Embeddings use baked-in bge-small ONNX; the trusted tenant always comes from the signed context.
- D32: Gmail uses only `gmail.readonly`. OAuth start/callback are session routes with state + PKCE; the redirect URI comes from `GOOGLE_REDIRECT_URI`. The refresh token is KMS-envelope-encrypted with context `user_id` + `gmail`; the harness mints an access token per call and puts it only in the signed internal header. Testing-mode limits (7-day refresh expiry, test users) are documented in `docs/gmail-oauth.md`.
- D33: RAE is static-only. S3 files and shallow https clones are parsed in a child container with `--network none`, read-only root, uid 65534, dropped capabilities, cpu/memory/pids/time/size caps, and a tmpfs `/tmp`. Archive members are checked for zip-slip, symlinks, entry-count, total-size, and compression-ratio bombs. Output is chunked to 3,500 characters with `get_chunk(n)`.
- D34: RAE placement is a separate SQS queue/DLQ, instance profile, and ASG in `infra/main/rae.tf`; it has artifacts read, results write, its own queue only, and no KMS or DynamoDB permissions. The harness submits `{job_id, s3_key}` and polls `results/{job_id}.json`. The analyzer target is never executed.
- D35: Invite codes are dormant. Planned later as OPTIONAL referral codes tied to credits (inviter and invited both earn credits). Never mandatory.

## Corrections (history)
- Early idea was an AI agent with built-in features. Corrected to HaaS.
- "No NAT" was wrong because the harness must reach ngrok, GitHub, and Gmail. Replaced by NAT instance.
- IMDS hop limit 1 breaks Docker. Now hop limit 2 plus DOCKER-USER rule.
- ARM (t4g) dropped to avoid image mismatch. x86 everywhere.
- KMS encryption context is audit, not isolation. Real isolation = separate instance profile.

## Open items
- Domain name for HTTPS (not yet decided).
- Monthly AWS budget and expected number of demo users (not yet given); decides 1 vs 2 AZ for ASG.
- AWS region: ap-south-1 is proposed, not confirmed.
- Team member names and roll numbers for the presentation (ask before building the PPT; never invent).

## Status tracker
| Phase | Status |
|---|---|
| P0 Setup | done |
| P1 Core harness | done |
| P2 Skeleton deploy | done |
| P3 Auth and API management | done |
| P3b Web UI | done |
| P4 MCP tools | done (GitHub, RAG, Gmail, RAE) |
| P5 Prove it | not started |
| P6 Demo and report | not started |

## Known limitations (state in report)
Single NAT, ASG max 2, shared instance role on app hosts, Gmail testing mode, Colab dependency.

## Working preferences
- Keep credit usage minimal; short replies.
- Ask for missing details before building; no placeholders or invented filler.
- Original designs, no stock templates.
- Coding-agent prompts: no mid-process messages; final reply is just "Done".
