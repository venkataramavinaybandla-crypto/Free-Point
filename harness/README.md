# Harness

FastAPI API gateway, agent loop, and approval-gated write consumer.

## MCP wiring

Set `INTERNAL_MCP_SECRET`, `GITHUB_MCP_URL`, `RAG_MCP_URL`, `GMAIL_MCP_URL` and
`RAE_MCP_URL` to enable the tool set. The harness signs every call with the
tenant, job, token and repository allowlist, decrypts the GitHub token from the
user's `github` secret and mints the Gmail access token from the encrypted
`gmail` refresh token per call, and never exposes `execute_draft_pr` to the
model.

Gmail OAuth routes are `GET /oauth/google/start` and
`GET /oauth/google/callback`; see `docs/gmail-oauth.md`. RAE binary analysis can
use the separate `free-point-rae` queue when `RAE_QUEUE_URL` and
`RAE_ARTIFACTS_BUCKET` are set; the harness then submits only the S3 key and
polls the results prefix.

## Queue and sandbox settings

- `RAE_QUEUE_URL`, `RAE_ARTIFACTS_BUCKET`, `RAE_RESULTS_PREFIX`: use the RAE
  worker queue instead of calling the RAE MCP analyzer directly.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`: Google
  OAuth. The redirect URI is never hardcoded.

## Execute consumer

The model loop stops at `AWAITING_APPROVAL`. `python -m app.core.worker` (or
`EXECUTE_CONSUMER_ENABLED=true` in the app process) consumes approved job ids,
re-checks the stored artifact hash, creates the draft PR through
`github-mcp`, and marks the job `COMPLETED`. Re-delivery is safe because the
GitHub executor reuses an existing branch or PR.
