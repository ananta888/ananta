from __future__ import annotations

from worker.retrieval.vector_index_job_handler import (
    VectorIndexWorkerTaskHandler,
)


def _envelope(operation: str = "index") -> dict:
    payload = {
        "points": [{"point_id": "1", "vector": [1.0], "payload": {}}],
        "batch_size": 128,
    }
    if operation == "delete":
        payload = {"point_ids": ["1"], "batch_size": 128}
    if operation == "migrate":
        payload = {"migration": {"dry_run": True}, "batch_size": 128}
    return {
        "schema": "ananta.vector_index_task.v1",
        "job_id": "vector-index-12345678",
        "operation": operation,
        "scope": {
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        "idempotency_key": "request-1234",
        "resolved_config": {
            "schema": "ananta.vector_store_resolved_config.v1",
            "provider": "json",
            "config_hash": "abc",
            "config": {"provider": "json"},
        },
        "payload": payload,
    }


def test_worker_handler_executes_exactly_one_hub_envelope() -> None:
    calls: list[dict] = []

    class Execution:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "completed",
                "reason_code": "ok",
                "diagnostics": {"backend": "json"},
                "result": {"upserted": 1},
            }

    result = VectorIndexWorkerTaskHandler(Execution()).execute(_envelope())

    assert len(calls) == 1
    assert calls[0]["operation"] == "index"
    assert calls[0]["scope"]["workspace_id"] == "workspace-a"
    assert result["status"] == "completed"
    assert result["result"] == {"upserted": 1}


def test_worker_handler_never_accepts_search_or_exposes_exception_text() -> None:
    class Execution:
        def execute(self, **kwargs):
            del kwargs
            raise RuntimeError("secret-token-must-not-leak")

    failed = VectorIndexWorkerTaskHandler(Execution()).execute(_envelope())
    assert failed["status"] == "failed"
    assert "secret-token" not in str(failed)

    invalid = _envelope()
    invalid["operation"] = "search"
    try:
        VectorIndexWorkerTaskHandler(Execution()).execute(invalid)
    except ValueError as exc:
        assert str(exc) == "vector_index_task_operation_invalid"
    else:
        raise AssertionError("search must not enter the mutation handler")
