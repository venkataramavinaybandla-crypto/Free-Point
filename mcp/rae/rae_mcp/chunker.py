"""Chunk analysis sections into context-sized pieces."""

from __future__ import annotations

from typing import Any

MAX_CHUNK_CHARS = 3500
MAX_CHUNKS = 200
MAX_TOTAL_CHARS = 500_000
TRUNCATION_MARK = "\n...[truncated]"


def _split(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def build_chunks(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return per-section pieces that each fit a 4k-token context budget."""
    chunks: list[dict[str, Any]] = []
    total = 0
    for section in sections:
        title = str(section.get("title", "section"))[:120]
        text = str(section.get("text", ""))
        pieces = _split(text, MAX_CHUNK_CHARS)
        for index, piece in enumerate(pieces, start=1):
            if len(chunks) >= MAX_CHUNKS or total >= MAX_TOTAL_CHARS:
                chunks[-1]["text"] += TRUNCATION_MARK
                return chunks
            remaining = MAX_TOTAL_CHARS - total
            if len(piece) > remaining:
                piece = piece[:remaining] + TRUNCATION_MARK
            label = title if len(pieces) == 1 else f"{title} [{index}/{len(pieces)}]"
            chunks.append({"title": label, "text": piece})
            total += len(piece)
    return chunks or [{"title": "summary", "text": "No analyzable content."}]
