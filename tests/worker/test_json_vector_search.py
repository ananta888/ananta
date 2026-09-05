from __future__ import annotations

import pytest

from worker.retrieval.json_vector_search import (
    NumpyCosineSearchIndex,
    PythonCosineSearchIndex,
)


@pytest.mark.parametrize(
    "query,candidates,top_k",
    [
        ((1.0, 0.0), (0, 1, 2, 3), 3),
        ((0.0, 1.0), (1, 2), 10),
        ((0.0, 0.0), (3, 1, 2), 2),
    ],
)
def test_numpy_strategy_matches_dependency_free_reference(
    query, candidates, top_k
) -> None:
    vectors = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, 0.0))
    expected = PythonCosineSearchIndex(vectors).rank(
        query, candidate_indices=candidates, top_k=top_k
    )
    actual = NumpyCosineSearchIndex(vectors).rank(
        query, candidate_indices=candidates, top_k=top_k
    )

    assert [index for index, _score in actual] == [
        index for index, _score in expected
    ]
    assert [score for _index, score in actual] == pytest.approx(
        [score for _index, score in expected], abs=1e-6
    )
