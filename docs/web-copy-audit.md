# Web copy audit (P3b)

Source template: `web/template/code.html` and `web/template/DESIGN.md`.
Reference of record: `agents/Architecture.json` plus the implemented P1-P3 behaviour.
Every row below is a claim that was checked; false or unverifiable copy was rewritten in
`web/pages/`. Visual language, layout and typography are unchanged.

Legend: **False** = contradicts the architecture or the code. **Unverified** = no implementation or
measurement backs it. **Example** = illustrative content, now explicitly labelled. **True** = kept.

| # | Template copy | Verdict | Change in `web/pages/index.html` |
|---|---|---|---|
| 1 | "Free Point turns it into an agent that can ship pull requests" | False (no merge, draft PRs only) | "wraps it in a harness with declared tools, hard limits, and approval-gated GitHub writes that never touch main" |
| 2 | Hero diagram "Sandboxed execution with isolated runners" | False | "Only the RAE parser runs in a no-network sandbox" |
| 3 | Hero diagram "Human sign-off required for destructive actions" | Unverified (gate covers GitHub writes) | "A single-use approval bound to the PR content hash" |
| 4 | Hero diagram "Low-latency schema and capability matcher" | Unverified | "Each job lists its tools; code validates every call" |
| 5 | "Every invocation is bound to an ephemeral, immutable Docker sandbox torn down immediately after PR verification." | False | Replaced with: policy in code, human gate before every GitHub write, RAE parser stays network-isolated |
| 6 | "Plug in any OpenAI-compatible model ... zero prompt retraining or fine-tuning required." | True | Kept, plus the real SSRF/HTTPS rules |
| 7 | "Granular sandboxing. Strict read-only workspace policies. Every outbound socket call is quarantined within sub-second Docker sandbox environments." | False | "Tool output is treated as untrusted data, tool calls are schema-checked, and jobs stop at a wall-clock limit." |
| 8 | "drafts pull requests, verifies integration tests, and alerts Slack for human approval" | False (no Slack, no test verification) | "draft a pull request on a branch. Nothing merges, and the worker only executes after a human approves the exact diff." |
| 9 | `tool::runtime.github.pr_v2`, `tool::rag.semantic_indexer`, `tool::inbox.zero_mutation`, `tool::ast.vuln_scanner` | False (invented tool ids) | Real service names: `mcp/github`, `mcp/rag`, `mcp/gmail`, `mcp/rae` |
| 10 | PR diff snippet with repo/branch | Example | Kept, labelled "Example diff" |
| 11 | "+148 lines / -32 lines" | False numbers | Removed |
| 12 | "Sandboxed PR: Zero push access to protected branch." | Mostly true | "Draft PRs only. No push access to protected branches." |
| 13 | "Approve and merge" button | False (no merge step) | Replaced with a "Review approvals" link to `/approvals` |
| 14 | "Google Drive Index ... connected Google Drive repositories" | False (Google Drive is not a RAG source) | "fastembed vectors in a FAISS index owned by one user ... Index versions live in S3" |
| 15 | "Semantic index active" status line | False | Removed |
| 16 | Inbox mock thread "Tokens: 820" | False number | Removed |
| 17 | Inbox "Drafted reply ... Outbound dispatch halted" | False (Gmail is read-only) | "Read-only Gmail summaries: two lines per message, no write scopes, no SMTP path" |
| 18 | "Zero SMTP outbound permissions without signature" | Unverified wording | "Read-only by design." |
| 19 | "Security Vulnerability Assessment / Clean status / Parse depth: 16 layers / Verified Clean" | False statuses and numbers | Removed; "Static analysis ... Samples are never executed" |
| 20 | "Pure static analysis. Executed in transient memory with zero file-system writing." | False (container has a read-only FS) | "the parser runs in a no-network, read-only container" |
| 21 | "Mutual encryption enforced end-to-end between all agent socket brokers and LLM provider endpoints." | False (no mTLS) | "Model URLs must be HTTPS. Every connection resolves, checks each IP, pins the peer and follows no redirects." |
| 22 | "Argon2id cryptographic salt and zero-knowledge enclave storage" | False (keys are SHA-256 hashed; Argon2id is for passwords; no enclave) | "Keys are shown once, stored as SHA-256 hashes with a public prefix. Passwords use Argon2id." |
| 23 | "Hardware stops on all repository mutating events. Human signature tokens required before merging." | False | "Single-use, expiring approvals bound to hash(user, job, repo, base, head, title, body, diff)." |
| 24 | "Every tool run spins up its own transient Docker sandbox" | False | "Tenant ids in every storage key and per-user indexes. Only the RAE parser runs network-isolated." |
| 25 | `https://api.freepoint.dev/v1/jobs` | Unverified (no deployed domain) | `https://your-host/v1/jobs` |
| 26 | Payload `{model, action, repo, params, sandbox, require_human_signoff}` | False (not the API schema) | Real schema: `{"task": ..., "tools": ["github"]}` |
| 27 | "Payload Size: 418 bytes / HTTP/2 201 Created" | False numbers | Removed |
| 28 | "Live SSE Telemetry / streaming" | False (no SSE endpoint) | Labelled "Example log"; footer points at `GET /v1/jobs/{id}` |
| 29 | Log lines: sandbox provisioned, `scratchfs` clone, "14/14 unit vectors green", fake PR URL, duration 2.41s, memory 48MB/512MB | False | Rewritten to the real job lifecycle (queued, tools declared, schema validated, artifact staged, `AWAITING_APPROVAL`), labelled Example |
| 30 | "Active Production API Key fp_live_89a••••••29c" | False (wrong key format) | Replaced by a description of the real prefix/created/last-used columns |
| 31 | "Monthly Sandbox Quota 68% • 6,802 / 10,000 runs" | False numbers and wrong quota model | Replaced by "rate limit and daily quota per key" (real API values rendered in the dashboard) |
| 32 | "Connected Engine qwen3-1.7b / 18ms latency" | False measurement | Replaced by "URL and token stored encrypted" |
| 33 | Approval hash "8f4ae12dc089bb219" | False value | Replaced by "Open a pending request, read the PR payload and payload hash" |
| 34 | Jobs table with three fabricated job ids, initiators and elapsed times | False | Replaced by three real feature descriptions (keys, approvals, model test) |
| 35 | "All systems operational" | Unverified status | Replaced by a link to the real `/health` endpoint |
| 36 | "© 2025 Free Point Inc." | False (student project, wrong year) | "© 2026 Free Point. Student project for 25CS2206E." |
| 37 | "Architecture", "Changelog", "Security Whitepaper", "Twitter / X", "Discord Enclave" | Unverified destinations | Real links: API docs, approvals, health check |
| 38 | Footer version "v2.4.0-liquid" | False | `v0.1.0`, the FastAPI application version |
| 39 | Invite form "Claim key" + "Invocation verified. Welcome to Free Point alpha network." | False (fabricated success state) | Superceded by open signup. Invite form and "Claim key" wording removed; replaced with direct "Create account" CTA button to `/signup` |
| 40 | "Ready to arm your models with verified runtime agency?" | Unverified ("verified") | "Ready to arm your models with approval-gated hands?" |
| 41 | "Free Point is rolling out in cohorts. Enter your invitation key..." | Superceded by open signup | "Free Point is open for signup. Create an account with your email and password, then connect an OpenAI-compatible endpoint." |
| 42 | Hero CTA "Enter invite code" | Superceded by open signup | Replaced with "Create account" linking directly to open signup (`/signup`) |
| 43 | Signup page eyebrow "Invite only" and cohort lede | Superceded by open signup | Replaced with eyebrow "Get started" and open signup lede; invite code field removed from form |

## Confirmed true and kept

- The harness uses open signup (email + password only); invite codes are dormant.
- Users receive an API key; only a prefix and metadata are stored after the show-once moment.
- Model endpoints are OpenAI-compatible, HTTPS-only and SSRF-guarded.
- GitHub writes are approval-gated, single-use, expiring and hash-bound.
- Job states and the approval hand-off match `agents/Architecture.json` and `agents/workflows.md`.

## Data honesty rules applied to the app pages

- No usage, quota, latency or job numbers are hard-coded. The dashboard renders `/keys`,
  `/web/usage` and `/secrets` responses only, with loading, empty and error states.
- The two terminal panels on the landing page are labelled **Example request** and **Example log**.
  No other sample data is presented as a real account.
- The only key material shown is the value returned once by `POST /keys` or `POST /keys/{id}/rotate`,
  held in a module variable for the lifetime of the modal and never written to storage.
