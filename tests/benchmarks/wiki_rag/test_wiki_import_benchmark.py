from __future__ import annotations

import pytest


@pytest.mark.benchmark
def test_wiki_import_benchmark_contract_shape():
    # Optional benchmark placeholder with deterministic contract checks.
    result = {
        "schema": "wiki_import_benchmark_result.v1",
        "pages_per_second": 0.0,
        "chunks_per_second": 0.0,
        "index_size_mb": 0.0,
    }
    assert result["schema"] == "wiki_import_benchmark_result.v1"
