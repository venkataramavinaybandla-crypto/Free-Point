"""Execute consumer for human-approved GitHub writes.

The model loop stops at ``AWAITING_APPROVAL``. This consumer is the only caller
of the GitHub execute tool: it re-reads the stored artifact, re-checks the hash,
creates the draft PR, and marks the job completed. Retries are safe because the
GitHub executor is idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Awaitable, Callable

from mcp_common.security import TrustedContext

from ..audit import audit_logger
from ..mcp_client.client import ToolCaller, build_mcp_caller
from ..mcp_client.github_config import GitHubCredentials, load_github_credentials
from ..store.factory import get_approval_store, get_execute_queue, get_job_store
from ..store.memory import Job
from .approvals import approval_hash
from .state import InvalidStateTransitionError, JobStatus, transition

ARTIFACT_FIELDS = ("repo", "base", "head", "title", "body", "diff")
CredentialsLoader = Callable[[], Awaitable[GitHubCredentials]]


class ExecutionError(RuntimeError):
    """An approved artifact could not be executed safely."""


async def execute_approved_job(
    job_id: str,
    caller: ToolCaller | None = None,
    credentials_loader: CredentialsLoader | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    job_store = get_job_store()
    job = await job_store.get_job(job_id)
    if job is None:
        raise ExecutionError("Job not found.")
    if job.status == JobStatus.COMPLETED:
        return job.result if isinstance(job.result, dict) else {"status": "completed"}
    if job.status == JobStatus.REJECTED:
        raise ExecutionError("Job was rejected.")

    try:
        await _execute(job, job_store, caller, credentials_loader, request_id)
    except ExecutionError as err:
        await _mark_failed(job, job_store, str(err), request_id)
        raise
    except Exception as err:
        await _mark_failed(job, job_store, "GitHub execution failed.", request_id)
        raise ExecutionError("GitHub execution failed.") from err
    return job.result if isinstance(job.result, dict) else {"status": "completed"}


async def _execute(
    job: Job,
    job_store: Any,
    caller: ToolCaller | None,
    credentials_loader: CredentialsLoader | None,
    request_id: str | None,
) -> None:
    if not job.user_id:
        raise ExecutionError("Job has no tenant identity.")

    approval = await get_approval_store().get_approval(f"appr-{job.id}", job.user_id)
    if approval is None:
        raise ExecutionError("Approval not found.")
    if approval.get("status") != "approved" or not approval.get("used"):
        raise ExecutionError("Approval is not in an approved state.")
    if approval_hash(approval) != approval.get("payload_hash"):
        raise ExecutionError("Approval payload hash does not match.")

    stored = job.approval_artifact or {}
    artifact = stored.get("payload") if isinstance(stored.get("payload"), dict) else stored
    for field in ARTIFACT_FIELDS:
        if str(artifact.get(field, "")) != str(approval.get(field, "")):
            raise ExecutionError("Stored artifact does not match the approval.")

    recomputed = approval_hash({"user_id": job.user_id, "job_id": job.id, **artifact})
    if recomputed != approval.get("payload_hash"):
        raise ExecutionError("Approval payload hash does not match.")

    if job.status == JobStatus.AWAITING_APPROVAL:
        job.status = transition(job.status, JobStatus.APPROVED)
        await job_store.update_job(job)
    if job.status == JobStatus.APPROVED:
        job.status = transition(job.status, JobStatus.EXECUTING)
        await job_store.update_job(job)
    if job.status != JobStatus.EXECUTING:
        raise ExecutionError("Job is not approved for execution.")

    active_caller = caller or build_mcp_caller()
    if active_caller is None:
        raise ExecutionError("GitHub MCP endpoint is not configured.")

    credentials = await (credentials_loader or (lambda: load_github_credentials(job.user_id)))()
    repo = str(artifact.get("repo", ""))
    if repo not in credentials.allowlist:
        raise ExecutionError("Repository is no longer allowlisted.")

    audit_logger.log_event(
        "execute_started",
        job_id=job.id,
        request_id=request_id,
        tool_name="execute_draft_pr",
        tainted_by_untrusted_source=job.tainted,
        details={"repo": repo, "head": artifact.get("head")},
    )

    context = TrustedContext(
        user_id=job.user_id,
        job_id=job.id,
        token=credentials.token,
        repo_allowlist=credentials.allowlist,
        base_branch=credentials.base_branch,
    )
    result = await active_caller.call_tool("github", "execute_draft_pr", dict(artifact), context)

    job.result = {
        "status": result.get("status", "created"),
        "pr_url": result.get("url", ""),
        "pr_number": result.get("number"),
        "head": result.get("head", artifact.get("head")),
        "base": result.get("base", artifact.get("base")),
    }
    job.status = transition(job.status, JobStatus.COMPLETED)
    await job_store.update_job(job)
    audit_logger.log_event(
        "execute_completed",
        job_id=job.id,
        request_id=request_id,
        tool_name="execute_draft_pr",
        tainted_by_untrusted_source=job.tainted,
        details={"repo": repo, "pr_number": job.result["pr_number"]},
    )


async def _mark_failed(job: Job, job_store: Any, message: str, request_id: str | None) -> None:
    try:
        job.status = transition(job.status, JobStatus.FAILED)
    except InvalidStateTransitionError:
        pass
    job.error = message
    await job_store.update_job(job)
    audit_logger.log_event(
        "execute_failed",
        job_id=job.id,
        request_id=request_id,
        tool_name="execute_draft_pr",
        tainted_by_untrusted_source=job.tainted,
        details={"error": message},
    )


async def run_execute_consumer(poll_timeout: float = 5.0, max_messages: int | None = None) -> None:
    """Consume approved job ids from the execute queue."""
    queue = get_execute_queue()
    handled = 0
    while max_messages is None or handled < max_messages:
        job_id = await queue.dequeue(timeout=poll_timeout)
        if job_id is None:
            if max_messages is not None:
                break
            continue
        try:
            await execute_approved_job(job_id)
        except ExecutionError:
            pass
        handled += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Free Point approved-write consumer")
    parser.add_argument("--once", action="store_true", help="Drain the queue once and exit")
    args = parser.parse_args()
    asyncio.run(run_execute_consumer(max_messages=1 if args.once else None))


if __name__ == "__main__":
    main()
