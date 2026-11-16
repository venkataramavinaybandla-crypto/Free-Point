"""Pure-Python static analyzer run only inside the no-network sandbox.

This module opens and parses files. It never imports the target, runs it,
resolves its symlinks, or follows submodules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .archive import ArchiveRejected, is_archive, safe_extract

MAX_READ_BYTES = 32 * 1024 * 1024
MAX_STRINGS = 5000
MAX_SYMBOLS = 200
MAX_REPO_FILES = 5000
MAX_TEXT_SECTION = 40_000
STRING_RE = re.compile(rb"[\x20-\x7e]{4,}")
LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".sh": "Shell",
    ".tf": "Terraform",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
}
DEPENDENCY_FILES = (
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
)


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    length = len(data)
    return round(-sum((count / length) * math.log2(count / length) for count in counts if count), 4)


def file_type(path: Path) -> str:
    head = path.read_bytes()[:16]
    if head.startswith(b"\x7fELF"):
        return "ELF"
    if head.startswith(b"MZ"):
        return "PE"
    if head.startswith(b"%PDF"):
        return "PDF"
    if head.startswith(b"PK\x03\x04"):
        return "ZIP"
    if head.startswith(b"\x1f\x8b"):
        return "GZIP"
    suffix = path.suffix.lower()
    return LANGUAGES.get(suffix, suffix.lstrip(".") or "unknown")


def hashes(path: Path) -> dict[str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            sha256.update(chunk)
            md5.update(chunk)
    return {"sha256": sha256.hexdigest(), "md5": md5.hexdigest(), "size": str(size)}


def string_clusters(data: bytes) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for match in STRING_RE.finditer(data):
        value = match.group().decode("ascii", errors="replace")
        key = re.sub(r"[0-9a-fA-F]{6,}", "#", value[:32]).lower()
        record = clusters.setdefault(key, {"count": 0, "examples": []})
        record["count"] += 1
        if len(record["examples"]) < 3 and value not in record["examples"]:
            record["examples"].append(value[:160])
        if len(clusters) >= MAX_STRINGS:
            break
    ordered = sorted(clusters.items(), key=lambda item: item[1]["count"], reverse=True)[:20]
    return [{"pattern": key, "count": value["count"], "examples": value["examples"]} for key, value in ordered]


def _elf_sections(path: Path) -> list[dict[str, Any]]:
    from elftools.elf.elffile import ELFFile

    sections: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        sections.append(
            {
                "title": "ELF header",
                "text": json.dumps(
                    {
                        "class": elf.elfclass,
                        "machine": elf.header.get("e_machine"),
                        "type": elf.header.get("e_type"),
                        "entry": elf.header.get("e_entry"),
                    },
                    default=str,
                ),
            }
        )
        section_rows = []
        for section in elf.iter_sections():
            size = int(section["sh_size"])
            if not size:
                continue
            data = section.data()[: min(size, 1024 * 1024)]
            section_rows.append({"name": section.name, "size": size, "entropy": entropy(data)})
        sections.append({"title": "ELF sections and entropy", "text": json.dumps(section_rows[:100], indent=1)})

        symbols: list[str] = []
        for section_name in (".dynsym", ".symtab"):
            section = elf.get_section_by_name(section_name)
            if section is None:
                continue
            for symbol in section.iter_symbols():
                name = str(symbol.name)
                if not name:
                    continue
                kind = symbol["st_info"]["type"]
                undefined = symbol["st_shndx"] == "SHN_UNDEF"
                if undefined or kind == "STT_FUNC":
                    symbols.append(f"{'import' if undefined else 'func'} {name}")
                if len(symbols) >= MAX_SYMBOLS:
                    break
        sections.append({"title": "ELF imports and functions", "text": "\n".join(symbols) or "none"})
    return sections


def _pe_sections(path: Path) -> list[dict[str, Any]]:
    import pefile

    sections: list[dict[str, Any]] = []
    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"], pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
        )
        header = {
            "machine": hex(int(pe.FILE_HEADER.Machine)),
            "timestamp": int(pe.FILE_HEADER.TimeDateStamp),
            "subsystem": int(pe.OPTIONAL_HEADER.Subsystem),
        }
        sections.append({"title": "PE header", "text": json.dumps(header)})
        rows = [
            {"name": section.Name.rstrip(b"\x00").decode("ascii", errors="replace"), "size": int(section.SizeOfRawData), "entropy": entropy(section.get_data()[: 1024 * 1024])}
            for section in pe.sections
        ]
        sections.append({"title": "PE sections and entropy", "text": json.dumps(rows[:100], indent=1)})

        imports: list[str] = []
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])[:MAX_SYMBOLS]:
            dll = entry.dll.decode("ascii", errors="replace")
            for symbol in getattr(entry, "imports", [])[:100]:
                name = symbol.name.decode("ascii", errors="replace") if symbol.name else f"ordinal:{symbol.ordinal}"
                imports.append(f"{dll}!{name}")
                if len(imports) >= MAX_SYMBOLS:
                    break
        exports = [
            symbol.name.decode("ascii", errors="replace")
            for symbol in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])[:MAX_SYMBOLS]
            if symbol.name
        ]
        sections.append(
            {
                "title": "PE imports and exports",
                "text": json.dumps({"imports": imports, "exports": exports}, indent=1),
            }
        )
    finally:
        pe.close()
    return sections


def analyze_file(path: Path) -> list[dict[str, Any]]:
    if is_archive(path):
        return analyze_archive(path)
    data = path.read_bytes()
    info = {"type": file_type(path), "hashes": hashes(path), "path": path.name}
    sections: list[dict[str, Any]] = [{"title": "File identity", "text": json.dumps(info, indent=1)}]
    sections.append({"title": "Strings clusters", "text": json.dumps(string_clusters(data), indent=1)})
    if info["type"] == "ELF":
        try:
            sections.extend(_elf_sections(path))
        except Exception as err:  # malformed binaries are reportable, not fatal
            sections.append({"title": "ELF parse error", "text": type(err).__name__})
    elif info["type"] == "PE":
        try:
            sections.extend(_pe_sections(path))
        except Exception as err:
            sections.append({"title": "PE parse error", "text": type(err).__name__})
    return [{"title": item["title"], "text": str(item["text"])[:MAX_TEXT_SECTION]} for item in sections]


def analyze_archive(path: Path) -> list[dict[str, Any]]:
    destination = Path(tempfile.mkdtemp(prefix="rae-archive-", dir="/tmp"))
    try:
        names = safe_extract(path, destination)
        rows = []
        for name in names[:100]:
            candidate = destination / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            rows.append({"path": name, "type": file_type(candidate), "size": candidate.stat().st_size})
        return [
            {"title": "Archive identity", "text": json.dumps({"hashes": hashes(path), "entries": len(names)}, indent=1)},
            {"title": "Archive members", "text": json.dumps(rows, indent=1)},
        ]
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def analyze_repo(root: Path) -> list[dict[str, Any]]:
    languages: dict[str, int] = {}
    files: list[str] = []
    dependencies: dict[str, str] = {}
    total_files = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = [name for name in dirs if not (base / name).is_symlink() and name != ".git"]
        for name in names:
            candidate = base / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            total_files += 1
            if total_files > MAX_REPO_FILES:
                break
            relative = str(candidate.relative_to(root))
            files.append(relative)
            language = LANGUAGES.get(candidate.suffix.lower())
            if language:
                languages[language] = languages.get(language, 0) + 1
            if name in DEPENDENCY_FILES:
                try:
                    dependencies[relative] = candidate.read_text(encoding="utf-8", errors="replace")[:8000]
                except OSError:
                    pass
    return [
        {
            "title": "Repository map",
            "text": json.dumps(
                {"files": total_files, "languages": languages, "truncated": total_files > MAX_REPO_FILES},
                indent=1,
            ),
        },
        {"title": "Repository files", "text": "\n".join(files[:1000]) or "empty"},
        {"title": "Dependency files", "text": json.dumps(dependencies, indent=1)[:MAX_TEXT_SECTION]},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Static-only analyzer")
    parser.add_argument("path", help="Read-only input path")
    parser.add_argument("--kind", choices=("file", "repo"), default="file")
    args = parser.parse_args()
    target = Path(args.path)
    try:
        sections = analyze_repo(target) if args.kind == "repo" else analyze_file(target)
    except (ArchiveRejected, OSError) as err:
        print(json.dumps({"error": str(err)}))
        raise SystemExit(2) from err
    print(json.dumps({"sections": sections}, default=str))


if __name__ == "__main__":
    main()
