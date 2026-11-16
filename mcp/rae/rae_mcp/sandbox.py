"""Sandbox launcher and hardened git clone.

The analysis process is always ``docker run`` with no network, a read-only
root filesystem, a non-root uid, dropped capabilities, and cpu/memory/pids/time
limits. The target path is mounted read-only and is never used as an executable.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_SANDBOX_OUTPUT = 2_000_000
SANDBOX_IMAGE = os.environ.get("RAE_SANDBOX_IMAGE", "free-point-rae-mcp")


class SandboxError(RuntimeError):
    """The sandbox could not analyze the input."""


def validate_repo_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SandboxError("Repository URL must be credential-free https.")
    if parsed.query or parsed.fragment:
        raise SandboxError("Repository URL cannot contain query or fragment data.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return url
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise SandboxError("Repository host is not allowed.")
    return url


def build_sandbox_command(input_dir: Path, target: str, kind: str, image: str | None = None) -> list[str]:
    """Build the fixed-shape docker command asserted by the tests."""
    work_volume = os.environ.get("RAE_WORK_VOLUME", "")
    if work_volume:
        work_root = Path(os.environ.get("RAE_WORK_DIR", "/rae-work"))
        relative = input_dir.resolve().relative_to(work_root.resolve()).as_posix()
        mount = f"type=volume,src={work_volume},dst=/input,readonly"
        target_path = f"/input/{relative}/{target}"
    else:
        mount = f"type=bind,src={input_dir.resolve()},dst=/input,readonly"
        target_path = f"/input/{target}"
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "0.5",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=256m",
        "--mount",
        mount,
        image or os.environ.get("RAE_SANDBOX_IMAGE", SANDBOX_IMAGE),
        "python",
        "-m",
        "rae_mcp.analyzer",
        target_path,
        "--kind",
        kind,
    ]


def run_sandbox(input_dir: Path, target: str, kind: str, image: str | None = None) -> dict[str, Any]:
    command = build_sandbox_command(input_dir, target, kind, image)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={"PYTHONPATH": "/app", "PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise SandboxError("Analysis sandbox failed.") from err
    if len(result.stdout) > MAX_SANDBOX_OUTPUT:
        raise SandboxError("Analysis output exceeded the cap.")
    if result.returncode != 0:
        raise SandboxError("Analysis sandbox rejected the input.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as err:
        raise SandboxError("Analysis sandbox returned invalid output.") from err
    if not isinstance(payload, dict) or payload.get("error"):
        raise SandboxError(str(payload.get("error", "Analysis failed."))[:300])
    sections = payload.get("sections")
    if not isinstance(sections, list):
        raise SandboxError("Analysis sandbox returned no sections.")
    return payload


def clone_repository(url: str, destination: Path) -> Path:
    """Clone one branch without hooks, submodules, LFS data, or local file access."""
    validate_repo_url(url)
    clone_dir = destination / "repo"
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(destination),
    }
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "submodule.recurse=false",
        "-c",
        "protocol.file.allow=never",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-recurse-submodules",
        "--",
        url,
        str(clone_dir),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise SandboxError("Repository clone failed.") from err
    if result.returncode != 0 or not clone_dir.is_dir():
        raise SandboxError("Repository clone failed.")
    shutil.rmtree(clone_dir / ".git", ignore_errors=True)
    return clone_dir


def staged_temp_dir() -> Path:
    root = Path(os.environ.get("RAE_WORK_DIR", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="rae-input-", dir=root))
    path.chmod(0o755)
    return path
