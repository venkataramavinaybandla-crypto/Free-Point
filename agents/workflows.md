# Free Point: Workflows

## 1. Agent session routine (humans and coding agents)
1. Read `Agents.md`, `persona_maaaaa.txt`, `context.md`, `memory.md`, `Architecture.json`.
2. Work on **one phase task** at a time.
3. Run the test for it. Save evidence to `docs/evidence/`.
4. Update `memory.md` (decision, status). Commit.
5. For coding-agent runs: no mid-process messages, minimal credit use, final reply is just **"Done"**.

## 2. Phase workflow
P0 Setup -> P1 Core harness -> P2 Skeleton deploy -> P3 Auth -> P4 MCP tools -> P5 Prove it -> P6 Demo.
Each phase ends with its output from `Architecture.json`. Do not start the next phase until the current output exists.

## 3. Request lifecycle
1. Client calls `POST /v1/jobs` with API key, `Idempotency-Key`, declared `tools[]`.
2. api-gateway: verify key hash, check rate limit and quota (429 + Retry-After), dedupe by idempotency key, write job `QUEUED`, enqueue to SQS, return job ID.
3. harness-worker: receive message, mark `RUNNING`, validate model URL (SSRF guard), loop up to 6 steps: ask model (via model-egress) with constrained JSON, validate the tool call against schema and policy, call MCP tool, wrap output as untrusted data, log audit entry.
4. Read-only result: write `COMPLETED`, delete message.
5. Write-capable action: store the PR artifact, compute hash, mark `AWAITING_APPROVAL`, **ack and exit**.
6. Client polls `GET /v1/jobs/{id}` with exponential backoff (1s, 2s, 4s).

## 4. Approval flow
1. Human logs in (session cookie) and opens the pending approval.
2. Approval checks: same user, not expired, unused, hash matches the stored artifact.
3. Approve enqueues an `execute` message; worker creates the **draft PR** from the stored artifact only, using a deterministic branch name (idempotent).
4. Mark `COMPLETED`. Rejection marks `REJECTED`.

## 5. Build workflow for each MCP tool (P4)
1. Load the **mcp-builder** skill; design tool names, schemas, pagination.
2. Implement with FastMCP; one container per tool.
3. Test alone with MCP Inspector.
4. Write the tool's **injection test** and **cross-tenant test** now, not later.
5. Wire into harness behind the explicit `tools[]` declaration.
6. Order: GitHub -> RAG -> Gmail (nice) -> RAE (drop first).

## 6. Deploy workflow (no SSH)
1. `docker build` x86 images, scan, push to ECR.
2. `terraform plan` -> review -> `terraform apply`.
3. Trigger ASG **instance refresh**.
4. Verify `/health` over HTTPS and check CloudWatch.
5. Save plan output and screenshots to `docs/evidence/`.

## 7. Infrastructure workflow
- `infra/bootstrap`: state bucket + lock table (applied once, kept out of teardown).
- `infra/main`: everything else, `force_destroy` on buckets, KMS in pending deletion on destroy.
- Verify at P2: NAT `source_dest_check=false`, iptables persisted, IMDS hop limit 2 + DOCKER-USER rule.

## 8. Break-fix drills (P5)
| Drill | Inject | Expected evidence |
|---|---|---|
| Kill instance mid-job | stop worker or instance | SQS redelivers, no duplicate PR, job completes |
| NAT down | stop NAT instance | model/GitHub calls fail cleanly, job FAILED with reason, recovery after restart |
| IAM deny | remove or deny a permission | AccessDenied in CloudTrail, diagnosed in a few commands |
Each drill: record symptom, commands used, root cause, fix, time to recover.

## 9. Test workflow
- **Injection suite**: 10 to 20 malicious emails/READMEs; expect blocked or approval-gated writes.
- **Cross-tenant**: user A uploads, user B queries, expect zero hits.
- **Load**: MockLLM latency 5 to 30s; scale on SQS backlog per instance; capture ASG scale-out.
- **SSRF**: private IPs, metadata IP, redirects, DNS rebinding cases rejected.

## 10. Demo workflow
1. Start Colab: cell 1 pull and serve Qwen3-1.7B with llama-server, cell 2 start ngrok and print the URL, cell 3 test call.
2. Register the model URL via `/v1/model/test`.
3. Run RAG question, GitHub draft-PR with approval, (optional) Gmail summary.
4. Backup: switch to MockLLM if Colab or ngrok drops.
5. Show dashboards and cost, then `terraform destroy`.

## 11. Teardown
Run `terraform destroy` for `infra/main` after every session. Confirm in console that no ALB, NAT, or instances remain. Keep bootstrap stack.
