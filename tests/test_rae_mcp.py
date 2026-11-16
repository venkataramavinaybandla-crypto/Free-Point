"""RAE static-analysis tests: sandbox shape, archive guards, chunk caps, IAM."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import rae_mcp.sandbox as sandbox
from rae_mcp.archive import ArchiveRejected, safe_extract
from rae_mcp.chunker import MAX_CHUNK_CHARS, build_chunks
from rae_mcp.sandbox import SandboxError, build_sandbox_command, clone_repository, run_sandbox
from rae_mcp.server import create_app
from rae_mcp.service import RaeService

from harness.app.mcp_client.rae_queue import RaeQueueClient


def test_rae_mcp_rejects_unsigned_calls() -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app("test-secret")) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401


def test_sandbox_command_has_all_restrictions(tmp_path: Path) -> None:
    command = build_sandbox_command(tmp_path, "sample.bin", "file", "rae:test")
    joined = " ".join(command)
    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[command.index("--network") : command.index("--network") + 2]
    assert "--read-only" in command
    assert "--user" in command and "65534:65534" in command
    assert "--cap-drop" in command and "ALL" in command
    assert "--security-opt" in command and "no-new-privileges" in command
    assert "--pids-limit" in command and "128" in command
    assert "--memory" in command and "512m" in command
    assert "--cpus" in command and "0.5" in command
    assert "--tmpfs" in command
    assert "readonly" in joined
    assert command[-3:] == ["/input/sample.bin", "--kind", "file"]


def test_sandbox_uses_shared_volume_in_compose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    work = tmp_path / "rae-work"
    work.mkdir()
    monkeypatch.setenv("RAE_WORK_DIR", str(work))
    monkeypatch.setenv("RAE_WORK_VOLUME", "free-point-rae_work")
    command = build_sandbox_command(work / "rae-input-1", "sample.bin", "file", "rae:test")
    assert "type=volume,src=free-point-rae_work,dst=/input,readonly" in command
    assert command[-3] == "/input/rae-input-1/sample.bin"


def test_sandbox_never_executes_the_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"sections":[{"title":"ok","text":"data"}]}', stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    payload = run_sandbox(tmp_path, "target.sh", "file", "rae:test")
    assert payload["sections"][0]["title"] == "ok"
    assert calls and calls[0][0] == "docker"
    assert "sh" not in calls[0]
    assert "target.sh" not in calls[0][0]


def test_clone_is_shallow_and_hardened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        clone_dir = Path(command[-1])
        (clone_dir / ".git").mkdir(parents=True)
        assert kwargs["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = clone_repository("https://example.com/acme/repo.git", tmp_path)
    command = calls[0]
    assert command[0] == "git"
    assert "--depth" in command and "1" in command
    assert "--no-recurse-submodules" in command
    assert "core.hooksPath=/dev/null" in command
    assert "submodule.recurse=false" in command
    assert "--" in command
    assert result.is_dir()
    assert not (result / ".git").exists()


def test_repo_url_rejects_file_ssh_and_private_hosts(tmp_path: Path) -> None:
    for url in ("file:///etc/passwd", "ssh://git@example.com/repo", "https://127.0.0.1/repo", "https://user:pass@example.com/repo"):
        with pytest.raises(SandboxError):
            clone_repository(url, tmp_path)


def test_archive_bomb_and_zip_slip_are_rejected(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.txt", b"0" * (2 * 1024 * 1024))
    with pytest.raises(ArchiveRejected):
        safe_extract(bomb, tmp_path / "bomb-out")

    slip = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip, "w") as archive:
        archive.writestr("../escape.txt", b"x")
    with pytest.raises(ArchiveRejected):
        safe_extract(slip, tmp_path / "slip-out")
    assert not (tmp_path / "escape.txt").exists()


def test_archive_total_size_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import rae_mcp.archive as archive_module

    payload = tmp_path / "large.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("large.txt", b"x" * 2000)
    monkeypatch.setattr(archive_module, "MAX_EXTRACTED_BYTES", 1000)
    with pytest.raises(ArchiveRejected):
        safe_extract(payload, tmp_path / "large-out")


def test_oversize_sandbox_output_is_truncated_or_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="x" * (sandbox.MAX_SANDBOX_OUTPUT + 1), stderr=""),
    )
    with pytest.raises(SandboxError):
        run_sandbox(tmp_path, "sample.bin", "file", "rae:test")


def test_chunk_cap_and_oversize_truncation() -> None:
    chunks = build_chunks([{"title": "big", "text": "x" * 800_000}])
    assert len(chunks) <= 200
    assert all(len(chunk["text"]) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert chunks[-1]["text"].endswith("[truncated]")


class FakeS3:
    def __init__(self) -> None:
        self.objects = {"artifacts/a.bin": b"hello"}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        data = self.objects[Key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}


def test_service_chunks_and_scopes_analysis_to_user(tmp_path: Path) -> None:
    def runner(root: Path, target: str, kind: str) -> dict[str, Any]:
        assert target == "sample.bin" and kind == "file"
        return {"sections": [{"title": "File identity", "text": "ELF sha256=abc"}]}

    service = RaeService(s3_client=FakeS3(), runner=runner)
    result = __import__("asyncio").run(service.analyze_s3_key("usr-a", "artifacts/a.bin"))
    assert result["chunk_count"] >= 1
    assert result["chunks"][0]["index"] == 0
    assert service.get_chunk("usr-a", 0)["text"] == "ELF sha256=abc"
    with pytest.raises(SandboxError):
        service.get_chunk("usr-b", 0)
    with pytest.raises(SandboxError):
        __import__("asyncio").run(service.analyze_s3_key("usr-a", "results/a.bin"))


def test_repo_language_map(tmp_path: Path) -> None:
    from rae_mcp.analyzer import analyze_repo

    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    sections = analyze_repo(tmp_path)
    mapping = sections[0]["text"]
    assert "Python" in mapping and "JSON" in mapping
    dependencies = sections[2]["text"]
    assert "package.json" in dependencies


def test_rae_policy_has_no_kms_or_dynamodb_and_compose_has_no_cloud_secrets() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    rae_block = compose.split("  rae-mcp:", 1)[1].split("  rag-mcp:", 1)[0]
    assert "KMS_KEY_ID" not in rae_block
    assert "DYNAMODB" not in rae_block

    terraform = Path("infra/main/rae.tf").read_text(encoding="utf-8")
    policy = terraform.split('resource "aws_iam_policy" "rae"', 1)[1].split('resource "aws_iam_role_policy_attachment" "rae_attach"', 1)[0]
    assert "kms:" not in policy
    assert "dynamodb:" not in policy
    assert "sqs:SendMessage" not in policy
    assert 'resource "aws_sqs_queue" "rae"' in Path("infra/main/queues.tf").read_text(encoding="utf-8")


def test_harness_queue_contract_passes_only_s3_key() -> None:
    import asyncio

    sent: list[dict[str, Any]] = []

    class FakeSQS:
        def send_message(self, **kwargs: Any) -> None:
            sent.append(kwargs)

    class FakeResultsS3:
        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
            payload = {"analysis": {"summary": "ok"}, "chunks": [{"title": "a", "text": "chunk text"}]}
            return {"Body": io.BytesIO(json.dumps(payload).encode())}

    client = RaeQueueClient("https://sqs/rae", "free-point-artifacts", sqs_client=FakeSQS(), s3_client=FakeResultsS3())
    result = asyncio.run(client.analyze("job-1", "artifacts/a.bin"))
    assert result["analysis"]["summary"] == "ok"
    assert "s3_key" in json.loads(sent[0]["MessageBody"])
    assert client.get_chunk(0)["text"] == "chunk text"


def test_get_chunk_requires_prior_analysis() -> None:
    service = RaeService(s3_client=FakeS3(), runner=lambda *_: {"sections": []})
    with pytest.raises(SandboxError):
        service.get_chunk("usr-a", 0)
