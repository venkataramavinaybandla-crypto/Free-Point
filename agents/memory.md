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
| P0 Setup \| done \|
| P1 Core harness | not started |
| P2 Skeleton deploy | not started |
| P3 Auth and API management | not started |
| P4 MCP tools | not started |
| P5 Prove it | not started |
| P6 Demo and report | not started |

## Known limitations (state in report)
Single NAT, ASG max 2, shared instance role on app hosts, Gmail testing mode, Colab dependency.

## Working preferences
- Keep credit usage minimal; short replies.
- Ask for missing details before building; no placeholders or invented filler.
- Original designs, no stock templates.
- Coding-agent prompts: no mid-process messages; final reply is just "Done".
