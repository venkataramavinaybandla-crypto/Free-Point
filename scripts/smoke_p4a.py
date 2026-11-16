"""P4a compose smoke test: MCP tools, approvals, isolation, and secret hygiene.

Run from the repository root:

    ./.venv/Scripts/python.exe scripts/smoke_p4a.py

The stack uses in-memory harness storage, the mock model, an internal GitHub
REST double, and in-memory S3/DynamoDB doubles for RAG. Only HTTP is used to
talk to the harness; docker compose is used for lifecycle, internal probes,
and log inspection.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = "free-point-p4a-smoke"
MAIN_COMPOSE = os.path.join(ROOT, "docker-compose.yml")
SMOKE_COMPOSE = os.path.join(ROOT, "docker-compose.smoke.yml")
SECRET = "p4a-smoke-internal-secret"
HOST_PORT = 18000
BASE_URL = f"http://127.0.0.1:{HOST_PORT}"
GITHUB_TOKEN = "ghp_p4aSmokeTokenThatMustNeverAppear123"
GITHUB_REPOS = ["smoke/example", "test/repo"]

COMPOSE_ENV = {
    **os.environ,
    "INTERNAL_MCP_SECRET": SECRET,
    "HARNESS_PORT": str(HOST_PORT),
}

results: List[Tuple[str, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, "PASS" if passed else "FAIL"))
    if not passed and detail:
        print(f"[{name}] {detail}", file=sys.stderr)


def compose(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        MAIN_COMPOSE,
        "-f",
        SMOKE_COMPOSE,
        *args,
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=COMPOSE_ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{result.stderr[-4000:]}")
    return result


def compose_exec(code: str, *argv: str) -> subprocess.CompletedProcess[str]:
    return compose("exec", "-T", "harness", "python", "-c", code, *argv, timeout=120)


def parse_cookies(response: httpx.Response) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for name, value in response.headers.multi_items():
        if name.lower() != "set-cookie":
            continue
        pair = value.split(";", 1)[0].strip()
        if "=" in pair:
            key, val = pair.split("=", 1)
            cookies[key] = val
    return cookies


def session_headers(session_id: str, csrf: str) -> Dict[str, str]:
    return {
        "Cookie": f"session_id={session_id}; csrf_token={csrf}",
        "X-CSRF-Token": csrf,
    }


def session_cookie_only(session_id: str, csrf: str) -> Dict[str, str]:
    return {"Cookie": f"session_id={session_id}; csrf_token={csrf}"}


def wait_for_harness(client: httpx.Client, timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            response = client.get("/health")
            if response.status_code == 200:
                return
        except httpx.HTTPError as err:
            last_error = str(err)
        time.sleep(1.0)
    raise RuntimeError(f"harness did not become healthy: {last_error}")


def github_state() -> Dict[str, Any]:
    code = (
        "import urllib.request;"
        "print('STATE:' + urllib.request.urlopen('http://mock-github:8081/__admin/state', timeout=5).read().decode())"
    )
    result = compose_exec(code)
    for line in result.stdout.splitlines():
        if line.startswith("STATE:"):
            return json.loads(line[len("STATE:") :])
    raise RuntimeError(f"could not read mock GitHub state: {result.stderr[-2000:]}")


def wait_for_job(client: httpx.Client, key: str, job_id: str, timeout: float = 45.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    latest: Dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": key})
        if response.status_code == 200:
            latest = response.json()
            if latest.get("status") in {"COMPLETED", "FAILED", "REJECTED", "TIMED_OUT"}:
                return latest
        time.sleep(0.5)
    return latest


def rag_roundtrip(user_a: str, user_b: str) -> Dict[str, Any]:
    code = r"""
import asyncio
import json
import sys

from app.mcp_client.client import build_mcp_caller
from mcp_common.security import TrustedContext


async def main() -> None:
    caller = build_mcp_caller()
    if caller is None:
        raise SystemExit("MCP caller is not configured")

    text = "Free Point stores tenant vectors in a private FAISS index on S3."
    question = "Where are tenant vectors stored?"

    ingest = await caller.call_tool(
        "rag",
        "ingest_text",
        {"text": text, "source": "p4a-smoke"},
        TrustedContext(user_id=sys.argv[1], job_id="smoke-rag-a"),
    )
    hit = await caller.call_tool(
        "rag",
        "query",
        {"query": question, "k": 5},
        TrustedContext(user_id=sys.argv[1], job_id="smoke-rag-a"),
    )
    miss = await caller.call_tool(
        "rag",
        "query",
        {"query": question, "k": 5},
        TrustedContext(user_id=sys.argv[2], job_id="smoke-rag-b"),
    )
    print("RAG:" + json.dumps({"ingest": ingest, "hit": hit, "miss": miss}))


asyncio.run(main())
"""
    result = compose_exec(code, user_a, user_b)
    for line in result.stdout.splitlines():
        if line.startswith("RAG:"):
            return json.loads(line[len("RAG:") :])
    raise RuntimeError(f"RAG roundtrip failed: {result.stderr[-3000:]}")


def internal_unsigned_probe() -> Dict[str, Any]:
    code = r"""
import json
import urllib.error
import urllib.request

health = urllib.request.urlopen("http://github-mcp:8080/health", timeout=5).status
try:
    urllib.request.urlopen("http://github-mcp:8080/mcp", timeout=5)
    unsigned = 200
except urllib.error.HTTPError as err:
    unsigned = err.code
print("PROBE:" + json.dumps({"health": health, "unsigned": unsigned}))
"""
    result = compose_exec(code)
    for line in result.stdout.splitlines():
        if line.startswith("PROBE:"):
            return json.loads(line[len("PROBE:") :])
    raise RuntimeError(f"internal probe failed: {result.stderr[-3000:]}")


def main() -> int:
    started = False
    try:
        compose("down", "-v", timeout=120, check=False)
        compose("up", "-d", "--build", "--wait", timeout=1200)
        started = True

        with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
            wait_for_harness(client)

            # Signup user A, create an API key, then signup user B.
            invite_a = client.post("/auth/dev/invite")
            signup_a = client.post(
                "/auth/signup",
                json={
                    "invite": invite_a.json().get("invite", ""),
                    "email": f"p4a-a-{int(time.time())}@test.dev",
                    "password": "Str0ngP@ss!",
                },
            )
            cookies_a = parse_cookies(signup_a)
            csrf_a = signup_a.json().get("csrf_token", "")
            session_a = cookies_a.get("session_id", "")
            user_a = signup_a.json().get("user_id", "")
            key_response = client.post("/keys", headers=session_headers(session_a, csrf_a))
            api_key = key_response.json().get("key", "")

            invite_b = client.post("/auth/dev/invite")
            signup_b = client.post(
                "/auth/signup",
                json={
                    "invite": invite_b.json().get("invite", ""),
                    "email": f"p4a-b-{int(time.time())}@test.dev",
                    "password": "Str0ngP@ss!",
                },
            )
            user_b = signup_b.json().get("user_id", "")
            record(
                "signup_and_key",
                invite_a.status_code == 200
                and signup_a.status_code == 201
                and key_response.status_code == 201
                and signup_b.status_code == 201
                and bool(user_a)
                and bool(user_b)
                and bool(api_key),
                f"invite={invite_a.status_code} signupA={signup_a.status_code} key={key_response.status_code} signupB={signup_b.status_code}",
            )

            config = client.put(
                "/github/config",
                json={"token": GITHUB_TOKEN, "allowlist": GITHUB_REPOS, "base_branch": "main"},
                headers=session_headers(session_a, csrf_a),
            )
            record(
                "github_config_stored",
                config.status_code == 200 and config.json().get("configured") is True,
                f"status={config.status_code} body={config.text[:500]}",
            )

            # RAG: A ingests and finds its chunk; B sees nothing.
            rag = rag_roundtrip(user_a, user_b)
            hit_results = rag.get("hit", {}).get("results", [])
            miss_results = rag.get("miss", {}).get("results", [])
            record(
                "rag_user_a_hit",
                rag.get("ingest", {}).get("version") == 1
                and bool(hit_results)
                and any("faiss" in str(item.get("text", "")).lower() for item in hit_results),
                json.dumps(rag.get("ingest", {})),
            )
            record(
                "rag_user_b_zero_hits",
                miss_results == [] and rag.get("miss", {}).get("version") == 0,
                json.dumps(rag.get("miss", {})),
            )

            # PR job stops at approval with a hash-bound row.
            job_response = client.post(
                "/v1/jobs",
                json={"task": "Open a PR for the smoke test", "tools": ["github"]},
                headers={"X-API-Key": api_key},
            )
            job = job_response.json()
            job_id = str(job.get("id", ""))
            artifact = job.get("approval_artifact") or {}
            artifact_hash = str(artifact.get("artifact_hash", ""))

            approvals = client.get("/approvals", headers=session_cookie_only(session_a, csrf_a))
            approval_rows = approvals.json() if approvals.status_code == 200 else []
            approval_id = f"appr-{job_id}"
            approval_row = next((row for row in approval_rows if row.get("id") == approval_id), None)
            detail = client.get(f"/approvals/{approval_id}", headers=session_cookie_only(session_a, csrf_a))
            detail_body = detail.json() if detail.status_code == 200 else {}
            record(
                "pr_job_awaiting_approval",
                job_response.status_code == 201
                and job.get("status") == "AWAITING_APPROVAL"
                and artifact.get("action") == "draft_pr",
                f"status={job_response.status_code} job={job.get('status')} error={job.get('error')}",
            )
            record(
                "approval_row_and_hash",
                approval_row is not None
                and len(str(approval_row.get("payload_hash", ""))) == 64
                and approval_row.get("payload_hash") == artifact_hash
                and detail_body.get("payload_hash") == artifact_hash
                and artifact.get("payload", {}).get("head", "").startswith("freepoint/"),
                json.dumps({"row": approval_row, "artifact_hash": artifact_hash, "detail": detail_body.get("payload_hash")}),
            )
            state_before = github_state()
            record(
                "github_zero_writes_before_approval",
                state_before.get("write_calls") == 0 and state_before.get("pulls") == [],
                json.dumps({"write_calls": state_before.get("write_calls"), "pulls": len(state_before.get("pulls", []))}),
            )

            # A tampered review hash cannot enqueue execution.
            tampered = client.post(
                f"/approvals/{approval_id}/approve",
                json={"payload_hash": "0" * 64},
                headers=session_headers(session_a, csrf_a),
            )
            state_after_tamper = github_state()
            record(
                "tampered_approval_rejected",
                tampered.status_code == 409
                and state_after_tamper.get("write_calls") == 0
                and state_after_tamper.get("pulls") == [],
                f"status={tampered.status_code} body={tampered.text[:300]}",
            )

            # Correct hash enqueues execution exactly once.
            approved = client.post(
                f"/approvals/{approval_id}/approve",
                json={"payload_hash": detail_body.get("payload_hash")},
                headers=session_headers(session_a, csrf_a),
            )
            completed = wait_for_job(client, api_key, job_id)
            state_after = github_state()
            pulls = state_after.get("pulls", [])
            pull = pulls[0] if len(pulls) == 1 else {}
            head = pull.get("head", {}).get("ref", "")
            base = pull.get("base", {}).get("ref", "")
            record(
                "approved_execute_creates_draft_pr",
                approved.status_code == 200
                and completed.get("status") == "COMPLETED"
                and state_after.get("write_calls", 0) > 0
                and len(pulls) == 1
                and pull.get("draft") is True,
                json.dumps({"approve": approved.status_code, "job": completed.get("status"), "state": state_after}),
            )
            record(
                "draft_pr_non_main_branch",
                head.startswith("freepoint/")
                and head not in {"main", "master"}
                and base == "main"
                and completed.get("result", {}).get("head") == head,
                f"head={head} base={base} result={completed.get('result')}",
            )

            writes_before_repeat = state_after.get("write_calls", 0)
            repeated = client.post(
                f"/approvals/{approval_id}/approve",
                json={"payload_hash": detail_body.get("payload_hash")},
                headers=session_headers(session_a, csrf_a),
            )
            time.sleep(1.0)
            state_repeat = github_state()
            record(
                "reapprove_409_execute_once",
                repeated.status_code == 409
                and state_repeat.get("write_calls") == writes_before_repeat
                and len(state_repeat.get("pulls", [])) == 1,
                f"status={repeated.status_code} writes={state_repeat.get('write_calls')} pulls={len(state_repeat.get('pulls', []))}",
            )

            # A README injection is returned as untrusted data: no write, taint logged.
            injection_response = client.post(
                "/v1/jobs",
                json={"task": "Read the README in smoke/example and summarize it", "tools": ["github"]},
                headers={"X-API-Key": api_key},
            )
            injection_job = injection_response.json()
            injection_id = str(injection_job.get("id", ""))
            injection_done = wait_for_job(client, api_key, injection_id)
            approvals_after_injection = client.get(
                "/approvals", headers=session_cookie_only(session_a, csrf_a)
            ).json()
            state_injection = github_state()
            harness_logs = compose("logs", "--no-color", "harness", timeout=120).stdout
            taint_line = any(
                injection_id in line
                and '"tainted_by_untrusted_source": true' in line
                and "tool_called" in line
                for line in harness_logs.splitlines()
            )
            record(
                "injection_no_write_with_taint_audit",
                injection_response.status_code == 201
                and injection_done.get("status") == "COMPLETED"
                and injection_done.get("tainted") is True
                and taint_line
                and approvals_after_injection == []
                and state_injection.get("write_calls") == writes_before_repeat
                and len(state_injection.get("pulls", [])) == 1,
                json.dumps(
                    {
                        "status": injection_done.get("status"),
                        "tainted": injection_done.get("tainted"),
                        "taint_audit": taint_line,
                        "approvals": len(approvals_after_injection),
                        "writes": state_injection.get("write_calls"),
                    }
                ),
            )

            too_many = client.post(
                "/v1/jobs",
                json={"task": "three tools", "tools": ["github", "rag", "echo"]},
                headers={"X-API-Key": api_key},
            )
            record(
                "three_tools_rejected",
                too_many.status_code == 422,
                f"status={too_many.status_code} body={too_many.text[:300]}",
            )

            # The tool server is not reachable from the host, and unsigned internal calls fail.
            port_output = compose("port", "github-mcp", "8080").stdout.strip()
            mapped = bool(re.search(r":\d+$", port_output)) and not port_output.endswith(":0")
            host_call_failed = False
            try:
                host_response = httpx.get("http://127.0.0.1:8080/health", timeout=2.0)
                host_call_failed = host_response.status_code >= 500
            except httpx.HTTPError:
                host_call_failed = True
            record(
                "github_mcp_no_host_port",
                not mapped and host_call_failed,
                f"port={port_output!r} host_call_failed={host_call_failed}",
            )

            probe = internal_unsigned_probe()
            record(
                "unsigned_internal_call_rejected",
                probe.get("health") == 200 and probe.get("unsigned") == 401,
                json.dumps(probe),
            )

            all_logs = compose("logs", "--no-color", timeout=120).stdout
            token_free = (
                GITHUB_TOKEN not in all_logs
                and api_key not in all_logs
                and SECRET not in all_logs
                and "ghp_" not in all_logs
            )
            record(
                "container_logs_have_no_tokens",
                token_free,
                "one or more secrets appeared in container logs",
            )

    except Exception as err:  # keep the table useful when a step aborts
        record("scenario_completed", False, str(err))
    finally:
        if started:
            compose("down", "-v", timeout=120, check=False)

    width = max(len(name) for name, _ in results)
    print(f"{'CHECK':<{width}}  RESULT")
    print(f"{'-' * width}  ------")
    for name, status in results:
        print(f"{name:<{width}}  {status}")
    return 0 if all(status == "PASS" for _, status in results) else 1


if __name__ == "__main__":
    sys.exit(main())
