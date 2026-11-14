# Free Point: Project Context

Read this first. It is the stable brief for any human or coding agent working on this repo.

## What this is
**Free Point** is an **Agent Harness as a Service**. It is NOT an AI agent with built-in features.
A user signs up on the website, gets an **API key**, and connects **their own model** (any OpenAI-compatible endpoint) to our harness API. The harness gives that model agentic abilities through **MCP servers** (GitHub, RAG, Gmail-read, RAE).

Course: Cloud Infrastructure and Services (25CS2206E). Chosen problem statement: **#19 API Management and Microservices**. Review is presented as: Abstract, Problem Statement, Motivation, Functionalities, AWS Services, Conclusion.

## Audience and tone
Student project, not a startup. Low effort is fine, shaky is not. Evidence matters more than polish: Terraform plans, measured cost, one fixed break-fix, test results.

## Stack (locked)
Python, FastAPI, FastMCP, httpx, boto3, fastembed (ONNX), FAISS, Docker Compose, Terraform, AWS (EC2 ASG, ALB, SQS, DynamoDB, S3, KMS, SSM, ECR, CloudWatch, CloudTrail, Budgets).
Model side: Colab, llama.cpp `llama-server`, Qwen3-1.7B GGUF, ngrok. Context capped near 4k tokens.

## Repo layout
```
free-point/
  Agents.md  persona_maaaaa.txt  Architecture.json  context.md  memory.md  workflows.md
  harness/   api-gateway + worker + model-egress
  auth/      signup, sessions, keys, approvals
  mcp/       github/  rag/  gmail/  rae/
  web/       signup + key dashboard + approval UI
  infra/     bootstrap/  main/
  tests/     injection/  tenant/  load/
  docs/      threat-model.md  evidence/  ppt/
```

## Non-negotiable rules
1. **Policy lives in code, never in the prompt.**
2. All tool output, emails, repo files, and RAG text are **untrusted data**.
3. Every GitHub write needs **human approval** bound to a content hash. Draft PRs on a branch, never main.
4. The approve endpoint is **never** exposed as a model tool.
5. User model URLs are SSRF-guarded and re-validated on every job.
6. No secrets in the repo. API keys and invite codes are stored hashed.
7. Small model, smart harness: strict JSON schemas, constrained decoding, retries, 1 to 2 declared tools per job.
8. Everything in AWS is Terraform. No console clicking, no SSH deploys.
9. Cost first: ASG max 2, on-demand DynamoDB, no interface endpoints, 7-day logs, destroy after sessions.

## Scope cut line
Core: harness, auth/API management, GitHub, RAG. Nice: Gmail. Drop first: RAE.

## Glossary
- **Harness**: the service that wraps a model with tools, limits, and safety.
- **MCP**: Model Context Protocol, how tools are exposed to the agent.
- **RAE**: Reverse Engineer Anything style static analysis of binaries and repos, never executed.
- **Taint**: flag set when a job has read untrusted content; kept in audit logs as evidence.
- **DLQ**: dead-letter queue for jobs that fail repeatedly.

## Definition of done (any task)
Code runs in Docker, has a test, logs a request_id, respects the rules above, and leaves evidence (output or screenshot) in `docs/evidence/`.
