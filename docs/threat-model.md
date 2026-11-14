# Free Point Threat Model

## 1. System Overview & Scope
Free Point is an Agent Harness as a Service (HaaS). Users connect their own OpenAI-compatible model endpoints (e.g., Colab llama-server via ngrok) to the Free Point API Gateway, which wraps model invocations with tool capabilities via Model Context Protocol (MCP) servers (GitHub, RAG, Gmail, RAE). This threat model identifies key assets, trust boundaries, and controls for the top threats based on the security policy defined in `Architecture.json`.

## 2. Core Assets
- **Credentials & Secrets**: User API keys, Google OAuth tokens, GitHub personal access tokens, platform signing keys, AWS IAM instance credentials.
- **User Data & State**: User session data, job execution histories, audit logs, DynamoDB state tables.
- **RAG & Storage Assets**: S3 PR artifacts, per-tenant FAISS vector index files, uploaded source files.
- **Compute & Cloud Resources**: EC2 instances (App ASG, RAE ASG, NAT instance), ALB, SQS queues, AWS budget allocation.
- **External Write Targets**: Target GitHub repositories and branches.

## 3. Trust Boundaries
- **TB-1: External Untrusted vs. API Gateway**: Public internet clients invoking `/v1` endpoints vs. Free Point ingress.
- **TB-2: Harness Worker vs. External User Model**: Internal agent loop communicating outbound with untrusted user-supplied model URLs.
- **TB-3: Harness Runtime vs. MCP Tool Data Egress**: Ingestion of external repo files, emails, web content, and vector chunks (untrusted data).
- **TB-4: Tenant vs. Tenant**: Strict isolation between multi-tenant jobs, API keys, RAG FAISS indexes, and KMS contexts.
- **TB-5: Static Analysis Sandbox vs. Host OS**: Execution boundary around RAE parsing untrusted binaries and archives.
- **TB-6: Model Suggestions vs. GitHub Write Execution**: Separation of automated agent reasoning from mutating write actions.

## 4. Top Threats & Security Policy Controls

| Threat | Description & Attack Vector | Control (from Architecture.json `security_policy`) |
| :--- | :--- | :--- |
| **Prompt Injection** | Indirect prompt injection embedded in untrusted external inputs (emails, GitHub READMEs/issues, RAG retrieval chunks) attempting to hijack model instructions. | **Policy in code, never in prompt**: All tool outputs, emails, and repo files treated as untrusted data. Model suggestions cannot bypass hardcoded gates. `tainted_by_untrusted_source` flag logged in audit trail. Human approval required for all state-mutating actions; `/approvals` endpoint is never exposed as an MCP tool. |
| **SSRF (Server-Side Request Forgery)** | Malicious or compromised user model URLs targeting internal AWS metadata (IMDS 169.254.169.254), VPC private subnets, or loopback interfaces. | **SSRF Protection**: Outbound model calls enforced HTTPS-only. Connect-time peer-IP validation blocking IPv4/IPv6 private, loopback, and link-local ranges. Pinned IP with original SNI/Host, redirects disabled, response size caps. IMDSv2 required (hop limit 2) with host `DOCKER-USER` iptables rule dropping 169.254.169.254 for worker containers. Dedicated no-AWS-credentials egress container. |
| **Key Theft & Credential Exposure** | Exfiltration or leak of user API keys, GitHub/Gmail OAuth tokens, or AWS service credentials. | **Hashed & KMS-Envelope Storage**: API keys and invite codes stored hashed (SHA-256). User OAuth tokens encrypted in DynamoDB via KMS envelope encryption with `user_id` + `tool` encryption context. Platform secrets stored in SSM Parameter Store (`SecureString`). Ephemeral IMDSv2 instance credentials; no hardcoded credentials. |
| **Tenant Leakage** | Cross-tenant data exposure in shared databases, SQS queues, S3 storage, or FAISS vector search queries. | **Multi-Tenant Key Partitioning**: `tenant_id` prefix in every DynamoDB partition key, S3 object path (`rag-indexes/{user_id}/`), and SQS payload. Conditional writes on DynamoDB pointers (`rag_pointers`). Mandatory cross-tenant automated leak integration tests in test suite. |
| **Cost Abuse & Denial of Wallet** | Runaway agent loops, endless recursive tool invocation, excessive model polling, or SQS queue flooding driving up AWS costs. | **Cost Guardrails & Hard Caps**: Per-job hard limits (`max_steps = 6`, `max_job_seconds = 120`, `sqs_visibility_timeout = 180s`). API Gateway rate limiting via DynamoDB atomic counters with `429 Retry-After`. ASG capped at max 2 instances. AWS Budgets budget with automated email notifications at 50%, 80%, and 100%. DynamoDB on-demand mode. |
| **Malicious Binaries & Exploitation** | Zip bombs, path traversal (Zip-Slip), buffer overflows, or malicious code execution via static binary/repo analysis in RAE. | **Isolated Analyzer Sandbox**: RAE parser runs in a `--network none` child container with a read-only filesystem, non-root user, memory/CPU/time limits, archive-bomb and zip-slip path validation. Git hooks disabled; symlinks, git submodules, and git-LFS not followed. Isolated compute instance with own IAM role and zero KMS permissions. |

## 5. Security Principles & Non-Negotiables
1. **Policy Lives in Code**: Authorization, state transitions, and tool whitelists are enforced by deterministic application code, never delegated to model system prompts.
2. **Immutable Approval Binding**: GitHub write actions require human sign-off bound cryptographically to `hash(user_id, job_id, repo, base, head, title, body, diff)`. Worker acks and exits at `AWAITING_APPROVAL`; approval triggers a separate execute worker using the exact stored artifact.
3. **No Direct Execution**: MCP RAE is strictly static analysis; untrusted user binaries are never executed.
