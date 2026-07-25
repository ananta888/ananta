from __future__ import annotations

from pathlib import Path

from worker.retrieval.vector_index_execution import ConfiguredVectorIndexExecution


def _resolved(index_path: Path) -> dict[str, object]:
    return {
        "schema": "ananta.vector_store_resolved_config.v1",
        "config": {
            "provider": "json",
            "json": {"index_path": str(index_path)},
        },
    }


def test_index_upsert_does_not_require_compatibility(tmp_path: Path) -> None:
    execution = ConfiguredVectorIndexExecution()
    result = execution.execute(
        operation="index",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "points": [
                {
                    "record_id": "record-a",
                    "vector": [1.0, 0.0],
                    "payload": {"kind": "code", "file": "src/a.py"},
                    "source_hash": "source-a",
                }
            ]
        },
        idempotency_key="index-idempotency-key",
    )

    assert result["status"] == "completed"
    assert result["reason_code"] == "upsert"
    assert result["result"]["upserted"] == 1


def test_worker_rejects_point_scope_override(tmp_path: Path) -> None:
    execution = ConfiguredVectorIndexExecution()
    result = execution.execute(
        operation="index",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config=_resolved(tmp_path / "index.json"),
        payload={
            "points": [
                {
                    "record_id": "record-a",
                    "vector": [1.0, 0.0],
                    "scope": {
                        "workspace_id": "workspace-b",
                        "repository_id": "repository-a",
                        "profile_name": "default",
                        "domain": "codecompass",
                    },
                }
            ]
        },
        idempotency_key="scope-idempotency-key",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "vector_index_operation_failed"
    assert result["result"] is None
