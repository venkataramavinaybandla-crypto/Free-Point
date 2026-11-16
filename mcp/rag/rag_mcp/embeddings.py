"""Embedding providers.

``FastEmbedder`` loads the ONNX model baked into the image; it never downloads
at runtime. ``HashingEmbedder`` is a deterministic, dependency-free embedder
used by tests.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol, Sequence

MODEL_NAME = os.environ.get("RAG_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
MODEL_DIMENSION = 384


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedder:
    """ONNX bge-small embedder with the model cache baked into the image."""

    dimension = MODEL_DIMENSION

    def __init__(self, model_name: str = MODEL_NAME, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir or os.environ.get("FASTEMBED_CACHE_PATH"),
            local_files_only=True,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(list(texts))]


class HashingEmbedder:
    """Deterministic bag-of-words embedder for offline tests."""

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] % 2 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


def embedder_from_env() -> Embedder:
    return FastEmbedder()
