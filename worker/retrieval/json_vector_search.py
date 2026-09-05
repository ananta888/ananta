"""Replaceable in-memory cosine search strategies for the JSON vector store."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol


class CosineSearchIndexPort(Protocol):
    """Rank a bounded candidate projection without owning persistence."""

    def rank(
        self,
        query: Sequence[float],
        *,
        candidate_indices: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]: ...


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return 0.0
    return float(numerator / (left_norm * right_norm))


class PythonCosineSearchIndex:
    """Dependency-free reference implementation."""

    def __init__(self, vectors: Sequence[Sequence[float]]) -> None:
        self._vectors = tuple(tuple(float(value) for value in vector) for vector in vectors)

    def rank(
        self,
        query: Sequence[float],
        *,
        candidate_indices: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        ranked = [
            (int(index), _cosine_similarity(query, self._vectors[int(index)]))
            for index in candidate_indices
        ]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return tuple(ranked[: max(0, int(top_k))])


class NumpyCosineSearchIndex:
    """Vectorized adapter, isolated from persistence and filtering concerns."""

    def __init__(self, vectors: Sequence[Sequence[float]]) -> None:
        import numpy as np

        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError("vector_matrix_invalid")
        self._np = np
        self._matrix = matrix
        self._norms = np.linalg.norm(matrix, axis=1)

    def rank(
        self,
        query: Sequence[float],
        *,
        candidate_indices: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        np = self._np
        indices = np.asarray(tuple(int(value) for value in candidate_indices), dtype=np.int64)
        count = min(max(0, int(top_k)), int(indices.size))
        if count == 0:
            return ()
        query_array = np.asarray(query, dtype=np.float32)
        query_norm = float(np.linalg.norm(query_array))
        scores = np.zeros(indices.size, dtype=np.float32)
        candidate_norms = self._norms[indices]
        if query_norm > 1e-9:
            nonzero = candidate_norms > 1e-9
            scores[nonzero] = (
                self._matrix[indices[nonzero]] @ query_array
            ) / (candidate_norms[nonzero] * query_norm)
        # Secondary ordering by original entry index preserves the stable tie
        # behavior of the reference implementation.
        order = np.lexsort((indices, -scores))[:count]
        return tuple((int(indices[pos]), float(scores[pos])) for pos in order)


def create_cosine_search_index(
    vectors: Sequence[Sequence[float]],
) -> CosineSearchIndexPort:
    """Use the vectorized strategy when available, with a safe core fallback."""

    try:
        return NumpyCosineSearchIndex(vectors)
    except ImportError:
        return PythonCosineSearchIndex(vectors)


__all__ = [
    "CosineSearchIndexPort",
    "NumpyCosineSearchIndex",
    "PythonCosineSearchIndex",
    "create_cosine_search_index",
]
