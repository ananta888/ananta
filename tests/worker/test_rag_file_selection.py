from __future__ import annotations

from worker.coding.file_selection import FileSelectionLimits, select_candidate_files, select_candidate_files_from_hybrid_retrieval


def test_file_selection_prefers_ranked_refs_with_provenance() -> None:
    result = select_candidate_files(
        context_envelope={
            "retrieval_refs": [
                {"path": "src/a.py", "score": 0.2, "source_id": "rag", "symbol": "A"},
                {"path": "src/b.py", "score": 0.9, "source_id": "rag", "symbol": "B"},
            ],
            "file_sizes": {"src/a.py": 200, "src/b.py": 300},
        },
        limits=FileSelectionLimits(max_files=2, max_bytes=1000),
    )
    assert result["status"] == "ok"
    assert [item["path"] for item in result["selected_files"]] == ["src/b.py", "src/a.py"]
    assert result["selected_files"][0]["source_provenance"]["source_id"] == "rag"


def test_file_selection_degrades_to_explicit_files_without_rag() -> None:
    result = select_candidate_files(
        context_envelope={"retrieval_refs": []},
        explicit_files=["README.md", "src/main.py"],
    )
    assert result["status"] == "degraded"
    assert result["reason"] == "rag_unavailable_explicit_files_fallback"
    assert [item["path"] for item in result["selected_files"]] == ["README.md", "src/main.py"]


def test_file_selection_respects_byte_limit() -> None:
    result = select_candidate_files(
        context_envelope={
            "retrieval_refs": [
                {"path": "big.py", "score": 0.9},
                {"path": "small.py", "score": 0.8},
            ],
            "file_sizes": {"big.py": 1500, "small.py": 200},
        },
        limits=FileSelectionLimits(max_files=5, max_bytes=500),
    )
    assert result["status"] == "ok"
    assert [item["path"] for item in result["selected_files"]] == ["small.py"]


def test_file_selection_uses_profile_defaults_when_limits_missing() -> None:
    result = select_candidate_files(
        context_envelope={
            "retrieval_refs": [
                {"path": "a.py", "score": 0.9},
                {"path": "b.py", "score": 0.8},
                {"path": "c.py", "score": 0.7},
            ],
            "file_sizes": {"a.py": 50000, "b.py": 50000, "c.py": 50000},
        },
        execution_profile="safe",
    )
    assert result["execution_profile"] == "safe"
    assert result["usage_limits"]["max_files"] == 8
    assert result["usage_limits"]["max_bytes"] == 80000
    assert [item["path"] for item in result["selected_files"]] == ["a.py"]


def test_file_selection_from_hybrid_retrieval_adds_trace_data() -> None:
    result = select_candidate_files_from_hybrid_retrieval(
        query="fix auth bug",
        channel_results={
            "dense": [{"path": "src/auth.py", "content_hash": "h1", "score": 0.9, "text": "auth bug fix"}],
            "lexical": [{"path": "tests/test_auth.py", "content_hash": "h2", "score": 0.8, "text": "auth test"}],
            "symbol": [],
        },
        context_envelope={"file_sizes": {"src/auth.py": 200, "tests/test_auth.py": 200}},
        execution_profile="balanced",
    )
    assert result["status"] == "ok"
    assert result["retrieval_trace"]["query_original"] == "fix auth bug"
    assert result["retrieval_trace"]["selected_paths"]
