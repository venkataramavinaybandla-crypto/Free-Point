# MCP tool servers

Four FastMCP servers run on the private compose network. The harness is the
only caller and the only service with a published port.

| Service | Package | Tools |
|---|---|---|
| `github-mcp` | `github_mcp` | `list_files`, `read_file`, `create_draft_pr` (model-facing); `execute_draft_pr` (approval consumer only) |
| `rag-mcp` | `rag_mcp` | `ingest_text`, `query` |
| `gmail-mcp` | `gmail_mcp` | `list_recent` (metadata only, max 10), `summarize_email` (HTML stripped, max 2,000 chars) |
| `rae-mcp` | `rae_mcp` | `analyze_binary`, `analyze_repo`, `get_chunk` (static analysis only) |

## Internal authentication

Every `/mcp` request carries `X-FP-User-Id`, `X-FP-Job-Id`, `X-FP-Timestamp`,
`X-FP-Repo-Allowlist`, `X-FP-Base-Branch`, an optional `X-FP-Token`, and an
`X-FP-Signature` HMAC over all of them. The secret is `INTERNAL_MCP_SECRET`; it
must be set in the harness and every tool service. Requests older than 90
seconds, tampered headers, and unsigned requests get `401`. `/health` is open.

The GitHub token and the Gmail access token are minted/decrypted by the harness
per call and travel only in the signed `X-FP-Token` header. They are never tool
arguments or log fields. Gmail refresh-token storage and Google testing-mode
limits are documented in `docs/gmail-oauth.md`.

## GitHub flow

1. A model may declare `github_list_files`, `github_read_file`, and
   `create_draft_pr`. The last one never calls GitHub; it returns an artifact
   with `repo`, `base`, `head` (`freepoint/<job_id>`), `title`, `body`, `diff`.
2. The harness stores that artifact in the approvals table with
   `sha256(user_id, job_id, repo, base, head, title, body, diff)` and stops the
   job at `AWAITING_APPROVAL`.
3. After a human approves, the execute consumer recomputes the hash and calls
   `execute_draft_pr`. That tool creates a branch and a draft PR containing
   `.freepoint/patches/<job_id>.patch`. Existing PRs or branches are returned
   as-is, so retries are safe. No write ever targets `main` or `master`.

## Gmail flow

`list_recent` calls `messages.list` plus metadata-only `messages.get`; it never
returns bodies. `summarize_email` fetches one message, prefers `text/plain`,
strips HTML, truncates to 2,000 characters, and returns the text under
`untrusted_data`. The harness wraps every tool output in an untrusted delimiter
and asks the model for a two-line summary.

## RAE flow

`analyze_binary` reads an `artifacts/...` S3 object and `analyze_repo` clones an
https repository with hooks disabled, `--depth 1`, no submodules, and LFS
smudging off. Parsing runs in a child container with `--network none`,
read-only root, uid 65534, dropped capabilities, cpu/memory/pids caps, a tmpfs
`/tmp`, and a read-only input mount. The target is never executed. Archives are
checked for zip-slip, symlinks, entry-count, total-size, and compression-ratio
bombs. Results are split into chunks of at most 3,500 characters; `get_chunk(n)`
returns the next piece.

In production the RAE host consumes its own `free-point-rae` queue with the
`free-point-rae` instance profile (artifacts read, results write, no KMS and no
DynamoDB). The harness submits `{job_id, s3_key}` and polls
`results/{job_id}.json`.

## Local run

```bash
export INTERNAL_MCP_SECRET="$(openssl rand -hex 32)"
docker compose build
docker compose up
```

The harness is on `http://127.0.0.1:8000`; no tool port is published. For AWS
access, `rag-mcp` uses the instance role and the bucket/table created in
`infra/main/rag.tf`.
