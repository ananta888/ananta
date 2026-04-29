from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol


class EmbeddingProvider(Protocol):
    provider_id: str
    model_version: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class HashEmbeddingProvider:
    provider_id: str = "local_hash"
    model_version: str = "hash-v1"
    dimensions: int = 12

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        dims = max(1, int(self.dimensions))
        for value in list(texts or []):
            digest = sha256(str(value or "").encode("utf-8")).digest()
            bucket = [0.0 for _ in range(dims)]
            for index, byte in enumerate(digest):
                bucket[index % dims] += float(byte) / 255.0
            normalized = max(sum(abs(item) for item in bucket), 1e-9)
            vectors.append([item / normalized for item in bucket])
        return vectors

