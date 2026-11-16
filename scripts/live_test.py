import getpass
import json
import time
import uuid

import requests

HARNESS_URL = "http://127.0.0.1:8000"
API_KEY = getpass.getpass("Free Point API key: ").strip()
H = {"Authorization": f"Bearer {API_KEY}", "X-API-Key": API_KEY}
GITHUB_REPO = ""  # owner/name of your sandbox repo; leave empty to skip

TESTS = [
    ("What is the current UTC time?", ["utc_time"]),
    (
        "Save this note: Free Point routes tool calls through MCP servers. Then tell me how Free Point calls tools.",
        ["rag"],
    ),
]
if GITHUB_REPO:
    TESTS.append((f"Read README.md in {GITHUB_REPO} and propose a one-line improvement as a draft PR.", ["github"]))

r = requests.get(f"{HARNESS_URL}/v1/usage", headers=H, timeout=20)
print("usage:", r.status_code, r.text[:200])
r = requests.post(f"{HARNESS_URL}/v1/model/test", headers=H, timeout=90)
print("model:", r.status_code, r.text[:300])


def run(task, tools, cap=240):
    r = requests.post(
        f"{HARNESS_URL}/v1/jobs",
        headers={**H, "Idempotency-Key": str(uuid.uuid4())},
        json={"task": task, "tools": tools},
        timeout=30,
    )
    if r.status_code >= 300:
        print("submit failed:", r.status_code, r.text[:400])
        return
    j = r.json()
    jid = j.get("job_id") or j.get("id")
    last = None
    wait = 1
    t0 = time.time()
    while time.time() - t0 < cap:
        j = requests.get(f"{HARNESS_URL}/v1/jobs/{jid}", headers=H, timeout=30).json()
        st = j.get("state") or j.get("status")
        if st != last:
            print(f"  [{time.time()-t0:5.1f}s] {st}")
            last = st
        if st == "AWAITING_APPROVAL":
            print(f"  -> approve it at {HARNESS_URL}/approvals")
            return
        if st in ("COMPLETED", "FAILED", "TIMED_OUT", "REJECTED"):
            break
        time.sleep(wait)
        wait = min(wait * 2, 8)
    print(json.dumps(j, indent=2)[:1500])


for task, tools in TESTS:
    print(f"\n### {task}\n    tools={tools}")
    run(task, tools)
