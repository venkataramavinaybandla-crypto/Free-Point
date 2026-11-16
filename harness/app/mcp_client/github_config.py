"""Per-user GitHub configuration stored as one encrypted secret.

The model never sees the token or the allowlist bytes; the harness decrypts the
configuration for each tool call and signs it into the internal header set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from mcp_common.repos import is_valid_branch, is_valid_repo

from ..crypto.envelope import EnvelopeEncryptor
from ..store.factory import get_secret_store

SECRET_NAME = "github"
MAX_ALLOWLIST = 20


class GitHubConfigError(ValueError):
    """The stored GitHub configuration is missing or invalid."""


@dataclass(frozen=True)
class GitHubCredentials:
    token: str
    allowlist: tuple[str, ...]
    base_branch: str = "main"


def validate_config(token: str, allowlist: Sequence[str], base_branch: str = "main") -> GitHubCredentials:
    clean_token = token.strip()
    if not clean_token or len(clean_token) > 4096:
        raise GitHubConfigError("A GitHub token is required.")
    repositories = tuple(dict.fromkeys(repo.strip() for repo in allowlist if repo.strip()))
    if not repositories or len(repositories) > MAX_ALLOWLIST:
        raise GitHubConfigError(f"An allowlist of 1-{MAX_ALLOWLIST} repositories is required.")
    for repo in repositories:
        if not is_valid_repo(repo):
            raise GitHubConfigError("Allowlist entries must be in owner/name form.")
    base = base_branch.strip() or "main"
    if not is_valid_branch(base):
        raise GitHubConfigError("Invalid base branch.")
    return GitHubCredentials(token=clean_token, allowlist=repositories, base_branch=base)


async def store_github_config(
    user_id: str,
    token: str,
    allowlist: Sequence[str],
    base_branch: str = "main",
) -> GitHubCredentials:
    credentials = validate_config(token, allowlist, base_branch)
    payload = json.dumps(
        {
            "token": credentials.token,
            "allowlist": list(credentials.allowlist),
            "base_branch": credentials.base_branch,
        },
        separators=(",", ":"),
    )
    encrypted = EnvelopeEncryptor().encrypt(payload, user_id=user_id, tool=SECRET_NAME)
    await get_secret_store().set_secret(user_id, SECRET_NAME, encrypted, updated_at=_now())
    return credentials


async def load_github_credentials(
    user_id: str,
    encryptor: EnvelopeEncryptor | None = None,
    secret_store: Any = None,
) -> GitHubCredentials:
    store = secret_store or get_secret_store()
    encrypted = await store.get_secret(user_id, SECRET_NAME)
    if not encrypted:
        raise GitHubConfigError("GitHub is not configured for this user.")
    try:
        raw = (encryptor or EnvelopeEncryptor()).decrypt(encrypted, user_id=user_id, tool=SECRET_NAME)
        data = json.loads(raw)
        return validate_config(
            str(data.get("token", "")),
            list(data.get("allowlist", [])),
            str(data.get("base_branch", "main")),
        )
    except (ValueError, TypeError, KeyError) as err:
        raise GitHubConfigError("Stored GitHub configuration could not be read.") from err


def _now() -> int:
    import time

    return int(time.time())
