"""Deterministic, non-protected branch names derived from a job id."""

from __future__ import annotations

import re

PROTECTED_BRANCHES = {"main", "master"}


class BranchNameError(ValueError):
    """The job id cannot produce a safe branch name."""


def branch_for_job(job_id: str) -> str:
    safe = re.sub(r"[^a-z0-9._-]+", "-", job_id.lower()).strip("-._")
    if not safe:
        raise BranchNameError("A job id is required to derive the branch.")
    branch = f"freepoint/{safe}"[:60].rstrip("/-._")
    if not branch or branch.split("/")[-1] in PROTECTED_BRANCHES:
        raise BranchNameError("Derived branch name is protected.")
    return branch
