"""Repository and branch validation shared by the harness and tool servers."""

from __future__ import annotations

import re

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
MAX_REPO_CHARS = 140
MAX_BRANCH_CHARS = 120


def is_valid_repo(repo: str) -> bool:
    if not repo or len(repo) > MAX_REPO_CHARS or not REPO_PATTERN.fullmatch(repo):
        return False
    owner, name = repo.split("/", 1)
    return owner not in {".", ".."} and name not in {".", ".."}


def is_valid_branch(branch: str) -> bool:
    return (
        bool(branch)
        and len(branch) <= MAX_BRANCH_CHARS
        and bool(BRANCH_PATTERN.fullmatch(branch))
        and ".." not in branch
        and not branch.startswith("/")
        and not branch.endswith("/")
        and "//" not in branch
    )
