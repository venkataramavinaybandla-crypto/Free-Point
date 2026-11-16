"""Deterministic chunking with overlap and hard limits."""

from __future__ import annotations

MAX_INGEST_CHARS = 200_000
MAX_QUERY_CHARS = 2_000
MAX_CHUNKS_PER_CALL = 256
MAX_STORED_CHUNKS = 2_000
MAX_SOURCE_CHARS = 300
MAX_K = 8


class ChunkingError(ValueError):
    """The text cannot be indexed within the configured limits."""


def clean_source(source: str) -> str:
    value = " ".join(source.split())
    if not value:
        return "manual"
    return value[:MAX_SOURCE_CHARS]


def chunk_text(text: str, chunk_chars: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping windows, preferring whitespace boundaries."""
    if not text or not text.strip():
        raise ChunkingError("Text is empty.")
    if len(text) > MAX_INGEST_CHARS:
        raise ChunkingError(f"Text exceeds the {MAX_INGEST_CHARS} character ingest cap.")
    if chunk_chars < 100 or chunk_chars > 4_000:
        raise ChunkingError("Invalid chunk size.")
    if overlap < 0 or overlap >= chunk_chars // 2:
        raise ChunkingError("Invalid overlap.")

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_chars, length)
        if end < length:
            boundary = text.rfind(" ", start + chunk_chars // 2, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if len(chunks) > MAX_CHUNKS_PER_CALL:
            raise ChunkingError(f"Text exceeds the {MAX_CHUNKS_PER_CALL} chunk cap.")
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def clean_query(query: str) -> str:
    value = query.strip()
    if not value:
        raise ChunkingError("Query is empty.")
    if len(value) > MAX_QUERY_CHARS:
        raise ChunkingError(f"Query exceeds the {MAX_QUERY_CHARS} character cap.")
    return value


def clean_k(k: int) -> int:
    if k < 1 or k > MAX_K:
        raise ChunkingError(f"k must be between 1 and {MAX_K}.")
    return k
