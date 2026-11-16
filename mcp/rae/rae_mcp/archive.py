"""Archive extraction guards used inside the analysis sandbox.

Nothing is extracted unless every member path stays under the destination and
the archive passes entry-count, total-size, and compression-ratio caps.
Symlinks and hardlinks are never materialized.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from pathlib import Path

MAX_ARCHIVE_ENTRIES = 1000
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


class ArchiveRejected(ValueError):
    """The archive is unsafe to extract."""


def safe_member_path(destination: Path, name: str) -> Path:
    if not name or "\x00" in name or name.startswith(("/", "\\")) or ":" in name:
        raise ArchiveRejected("Unsafe archive member path.")
    parts = Path(name.replace("\\", "/")).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ArchiveRejected("Unsafe archive member path.")
    target = (destination / Path(*parts)).resolve()
    root = destination.resolve()
    if target != root and root not in target.parents:
        raise ArchiveRejected("Archive member escapes the extraction root.")
    return target


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _extract_zip(source: Path, destination: Path) -> list[str]:
    names: list[str] = []
    total = 0
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise ArchiveRejected("Archive has too many entries.")
        for info in members:
            if info.is_dir() or _is_zip_symlink(info):
                continue
            target = safe_member_path(destination, info.filename)
            if info.file_size > MAX_EXTRACTED_BYTES:
                raise ArchiveRejected("Archive member is too large.")
            if info.compress_size and info.file_size > 1_000_000:
                if info.file_size / max(1, info.compress_size) > MAX_COMPRESSION_RATIO:
                    raise ArchiveRejected("Archive compression ratio is unsafe.")
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise ArchiveRejected("Archive expands beyond the size cap.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source_file, target.open("wb") as destination_file:
                destination_file.write(source_file.read(MAX_EXTRACTED_BYTES + 1))
            names.append(str(target.relative_to(destination)))
    return names


def _extract_tar(source: Path, destination: Path) -> list[str]:
    names: list[str] = []
    total = 0
    archive_size = max(1, source.stat().st_size)
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise ArchiveRejected("Archive has too many entries.")
        for member in members:
            if member.isdir() or member.issym() or member.islnk():
                continue
            if not member.isfile():
                continue
            target = safe_member_path(destination, member.name)
            if member.size > MAX_EXTRACTED_BYTES:
                raise ArchiveRejected("Archive member is too large.")
            total += member.size
            if total > MAX_EXTRACTED_BYTES:
                raise ArchiveRejected("Archive expands beyond the size cap.")
            if total > max(1_000_000, archive_size * MAX_COMPRESSION_RATIO):
                raise ArchiveRejected("Archive compression ratio is unsafe.")
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with extracted, target.open("wb") as destination_file:
                destination_file.write(extracted.read(MAX_EXTRACTED_BYTES + 1))
            names.append(str(target.relative_to(destination)))
    return names


def safe_extract(source: Path, destination: Path) -> list[str]:
    """Extract a zip or tar after validating every member."""
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(source):
        return _extract_zip(source, destination)
    if tarfile.is_tarfile(source):
        return _extract_tar(source, destination)
    raise ArchiveRejected("Unsupported archive format.")


def is_archive(path: Path) -> bool:
    return zipfile.is_zipfile(path) or tarfile.is_tarfile(path)


def remove_tree(path: Path) -> None:
    """Best-effort cleanup without following symlinks."""
    if not path.exists():
        return
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            candidate = Path(root) / name
            if candidate.is_symlink():
                candidate.unlink(missing_ok=True)
            else:
                candidate.unlink(missing_ok=True)
        for name in dirs:
            candidate = Path(root) / name
            if candidate.is_symlink():
                candidate.unlink(missing_ok=True)
            else:
                candidate.rmdir()
    path.rmdir()
