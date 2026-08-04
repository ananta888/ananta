from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from agent.common.errors import WorkerForwardingError
from agent.config import settings
from agent.services import _task_scoped_forwarding as forwarding
from agent.services.worker_forward_transport import (
    WorkerForwardAmbiguousTransportError,
    WorkerForwardDeadlineExceeded,
    WorkerForwardTransportError,
    WorkerTransportDeadline,
)
from ananta_contracts.knowledge_index_dispatch import (
    KNOWLEDGE_INDEX_DISPATCH_SCHEMA,
    KNOWLEDGE_INDEX_TASK_KIND,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON,
    SOURCE_ACCESS_MANIFEST_FIELD,
)


def _pending_response() -> dict:
    return {
        "status": "error",
        "message": (
            KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
        ),
        "http_status": (
            KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS
        ),
        "details": {
            "error_type": (
                KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE
            ),
            "retryable": True,
            "details": {
                "reason_code": (
                    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
                ),
            },
        },
    }


def _prepared_payload(*, grant_expires_epoch_ms: int) -> dict:
    return {
        "task_id": "knowledge-index-job-1",
        "knowledge_index_dispatch": {
            "schema": KNOWLEDGE_INDEX_DISPATCH_SCHEMA,
            "job_id": "knowledge-index-job-1",
            "task_kind": KNOWLEDGE_INDEX_TASK_KIND,
            "phase": "execute",
            SOURCE_ACCESS_MANIFEST_FIELD: {
                "grant_expires_at_epoch_ms": grant_expires_epoch_ms,
                "capability": {"preserved": True},
            },
        },
    }


def _governed_task(*, lease_expires_epoch_ms: int) -> dict:
    return {
        "id": "knowledge-index-job-1",
        "task_kind": KNOWLEDGE_INDEX_TASK_KIND,
        "assigned_agent_url": "http://worker:5001",
        "assigned_agent_token": "stale-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "resources": {"max_runtime_seconds": 60},
                "assignment": {
                    "lease_expires_epoch_ms": lease_expires_epoch_ms,
                },
            },
            "destination_selection": {"worker_id": "worker-index-01"},
        },
    }


def _configure_forwarding_boundary(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deadline: WorkerTransportDeadline,
    lease_expires_epoch_ms: int,
    grant_expires_epoch_ms: int,
) -> tuple[dict, list[dict]]:
    class RecoveryGate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: SimpleNamespace(
            agent_repo=SimpleNamespace(
                get_by_url=lambda _url: SimpleNamespace(
                    name="worker-index-01",
                    url="http://worker:5001",
                    token="current-worker-token",
                    registration_validated=True,
                    role="worker",
                    status="online",
                )
            )
        ),
    )
    monkeypatch.setattr(
        "agent.services.recovery_dispatch_gate_service."
        "get_recovery_dispatch_gate_service",
        lambda: RecoveryGate(),
    )
    monkeypatch.setattr(
        forwarding,
        "_codecompass_execute_deadline",
        lambda **_kwargs: deadline,
    )
    monkeypatch.setattr(
        forwarding,
        "_record_forwarded_worker_failure",
        lambda *_args, **_kwargs: None,
    )
    prepare_calls: list[dict] = []

    def prepare(**values):
        prepare_calls.append(dict(values))
        values["task"]["worker_execution_context"][
            "knowledge_index_job"
        ]["assignment"] = {
            "lease_expires_epoch_ms": lease_expires_epoch_ms,
        }
        values["payload"].update(
            _prepared_payload(
                grant_expires_epoch_ms=grant_expires_epoch_ms
            )
        )

    monkeypatch.setattr(
        forwarding,
        "_prepare_codecompass_worker_dispatch",
        prepare,
    )
    return (
        _governed_task(
            lease_expires_epoch_ms=lease_expires_epoch_ms
        ),
        prepare_calls,
    )


def test_lost_first_response_exact_replay_reuses_prepared_request(
    app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline = WorkerTransportDeadline.after_seconds(90)
    task, prepare_calls = _configure_forwarding_boundary(
        monkeypatch,
        deadline=deadline,
        lease_expires_epoch_ms=9_999_999_999_999,
        grant_expires_epoch_ms=9_999_999_999_999,
    )
    transport_calls = []
    accepted = []
    execution_calls = 0
    durable_result = {
        "status": "success",
        "data": {"status": "completed", "artifact_refs": []},
    }

    def forwarder(
        worker_url,
        endpoint,
        data,
        *,
        token,
        transport_deadline,
    ):
        nonlocal execution_calls
        transport_calls.append(
            (
                worker_url,
                endpoint,
                copy.deepcopy(data),
                token,
                transport_deadline,
            )
        )
        if len(transport_calls) == 1:
            execution_calls += 1
            raise WorkerForwardAmbiguousTransportError()
        return copy.deepcopy(durable_result)

    def accept(response, _task, *, transport_deadline):
        accepted.append((copy.deepcopy(response), transport_deadline))

    with app.app_context():
        response = forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=forwarder,
            on_success=accept,
        )

    assert response is not None
    assert response.data == durable_result["data"]
    assert execution_calls == 1
    assert len(prepare_calls) == 1
    assert len(transport_calls) == 2
    assert transport_calls[0][0:4] == transport_calls[1][0:4]
    assert transport_calls[0][4] is deadline
    assert transport_calls[1][4] is deadline
    assert accepted == [(durable_result["data"], deadline)]


def test_typed_pending_poll_is_bounded_and_reuses_exact_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline = WorkerTransportDeadline.after_seconds(90)
    task = _governed_task(
        lease_expires_epoch_ms=9_999_999_999_999
    )
    payload = _prepared_payload(
        grant_expires_epoch_ms=9_999_999_999_999
    )
    calls = []
    sleeps = []
    monkeypatch.setattr(
        forwarding,
        "_GOVERNED_KNOWLEDGE_INDEX_MAX_FORWARD_ATTEMPTS",
        3,
    )
    monkeypatch.setattr(
        forwarding.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    def forwarder(*_args, **kwargs):
        calls.append(
            (
                copy.deepcopy(_args[2]),
                kwargs["token"],
                kwargs["transport_deadline"],
            )
        )
        return _pending_response()

    with pytest.raises(WorkerForwardTransportError) as raised:
        forwarding._invoke_governed_knowledge_index_forwarder(
            enabled=True,
            task=task,
            forwarder=forwarder,
            worker_url="http://worker:5001",
            endpoint=f"/tasks/{task['id']}/step/execute",
            prepared_payload=payload,
            token="current-worker-token",
            transport_deadline=deadline,
        )

    assert raised.value.reason_code == (
        "knowledge_index_worker_dispatch_result_pending_retry_exhausted"
    )
    assert raised.value.retryable is True
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert all(call[0] == payload for call in calls)
    assert all(call[1] == "current-worker-token" for call in calls)
    assert all(call[2] is deadline for call in calls)


@pytest.mark.parametrize("expired_window", ["deadline", "lease"])
def test_exhausted_deadline_or_lease_prevents_exact_retry(
    monkeypatch: pytest.MonkeyPatch,
    expired_window: str,
) -> None:
    monotonic_now = [0.0]
    deadline = WorkerTransportDeadline.after_seconds(
        10,
        monotonic_clock=lambda: monotonic_now[0],
    )
    lease_expires = (
        10_000 if expired_window == "lease" else 20_000
    )
    task = _governed_task(
        lease_expires_epoch_ms=lease_expires
    )
    payload = _prepared_payload(grant_expires_epoch_ms=20_000)
    calls = []
    monkeypatch.setattr(forwarding.time, "time", lambda: 10.0)

    def forwarder(*_args, **_kwargs):
        calls.append(True)
        if expired_window == "deadline":
            monotonic_now[0] = 11.0
        raise WorkerForwardAmbiguousTransportError()

    expected_error = (
        WorkerForwardDeadlineExceeded
        if expired_window == "deadline"
        else WorkerForwardingError
    )
    with pytest.raises(expected_error) as raised:
        forwarding._invoke_governed_knowledge_index_forwarder(
            enabled=True,
            task=task,
            forwarder=forwarder,
            worker_url="http://worker:5001",
            endpoint=f"/tasks/{task['id']}/step/execute",
            prepared_payload=payload,
            token="current-worker-token",
            transport_deadline=deadline,
        )

    assert calls == [True]
    if expired_window == "lease":
        assert str(raised.value) == "knowledge_index_execution_lease_stale"
        assert raised.value.retryable is False


@pytest.mark.parametrize(
    "response",
    [
        None,
        {
            **_pending_response(),
            "http_status": 500,
        },
        {
            **_pending_response(),
            "details": {
                **_pending_response()["details"],
                "error_type": "DifferentError",
            },
        },
    ],
)
def test_untyped_or_absent_response_is_never_exact_retried(
    response,
) -> None:
    deadline = WorkerTransportDeadline.after_seconds(90)
    calls = []

    def forwarder(*_args, **_kwargs):
        calls.append(True)
        return response

    returned = forwarding._invoke_governed_knowledge_index_forwarder(
        enabled=True,
        task=_governed_task(
            lease_expires_epoch_ms=9_999_999_999_999
        ),
        forwarder=forwarder,
        worker_url="http://worker:5001",
        endpoint="/tasks/knowledge-index-job-1/step/execute",
        prepared_payload=_prepared_payload(
            grant_expires_epoch_ms=9_999_999_999_999
        ),
        token="current-worker-token",
        transport_deadline=deadline,
    )

    assert returned == response
    assert calls == [True]


def test_propose_never_uses_execute_result_retry() -> None:
    calls = []

    def forwarder(*_args, **_kwargs):
        calls.append(True)
        raise WorkerForwardAmbiguousTransportError()

    with pytest.raises(WorkerForwardAmbiguousTransportError):
        forwarding._invoke_governed_knowledge_index_forwarder(
            enabled=False,
            task=_governed_task(
                lease_expires_epoch_ms=9_999_999_999_999
            ),
            forwarder=forwarder,
            worker_url="http://worker:5001",
            endpoint="/tasks/knowledge-index-job-1/step/propose",
            prepared_payload={
                "task_id": "knowledge-index-job-1",
                "knowledge_index_dispatch": {
                    "schema": KNOWLEDGE_INDEX_DISPATCH_SCHEMA,
                    "job_id": "knowledge-index-job-1",
                    "task_kind": KNOWLEDGE_INDEX_TASK_KIND,
                    "phase": "propose",
                },
            },
            token="current-worker-token",
            transport_deadline=None,
        )

    assert calls == [True]
