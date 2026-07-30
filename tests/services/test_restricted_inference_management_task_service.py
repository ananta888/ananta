from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.restricted_inference_management_service import (
    RestrictedInferenceManagementError,
)
from agent.services.restricted_inference_management_task_service import (
    RestrictedInferenceManagementTaskService,
)
from agent.services.voice_governance_domain import VoicePrincipal


def test_failed_persisted_task_retry_records_one_terminal_fail_fast_event(
    monkeypatch,
) -> None:
    import agent.repository
    import agent.services.task_queue_service
    import agent.services.task_runtime_service

    ingested: list[dict] = []
    updates: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        agent.repository.task_repo,
        "get_by_id",
        lambda _task_id: SimpleNamespace(status="failed"),
    )
    monkeypatch.setattr(
        agent.services.task_queue_service,
        "get_task_queue_service",
        lambda: SimpleNamespace(
            ingest_task=lambda **values: ingested.append(values)
        ),
    )
    monkeypatch.setattr(
        agent.services.task_runtime_service,
        "update_local_task_status",
        lambda task_id, status, **values: updates.append(
            (task_id, status, values)
        ),
    )

    def circuit_open() -> dict:
        raise RestrictedInferenceManagementError(
            "worker_circuit_open",
            "worker unavailable",
            status_code=503,
        )

    with pytest.raises(RestrictedInferenceManagementError):
        RestrictedInferenceManagementTaskService().execute(
            VoicePrincipal(tenant_id="tenant-a", subject="hub-recovery"),
            operation="model_cache_gc",
            target_id="runtime-cache",
            request_id="retry-request",
            callback=circuit_open,
        )

    assert ingested == []
    assert len(updates) == 1
    _task_id, status, values = updates[0]
    assert status == "failed"
    assert values["event_type"] == "restricted_management_failed"
    assert values["event_details"]["failure_reason_code"] == "worker_circuit_open"
    assert values["event_details"]["retry_attempt"] is True
    assert values["event_details"]["retryable"] is True
