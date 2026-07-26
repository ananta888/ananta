from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent.services.recovery_hub_run_evidence_service import (
    RecoveryHubRunEvidenceError,
    RecoveryHubRunEvidenceService,
)
from agent.services.recovery_worker_result_service import (
    RecoveryWorkerResultService,
)
from agent.services.tool_run_catalog_service import (
    ToolRunCatalogService,
)


class TaskRepository:
    def __init__(self, task: SimpleNamespace) -> None:
        self.task = task
        self.save_calls: list[SimpleNamespace] = []

    def get_by_id(self, task_id: str) -> SimpleNamespace | None:
        return self.task if self.task.id == task_id else None

    def save(self, task: SimpleNamespace) -> SimpleNamespace:
        self.task = task
        self.save_calls.append(task)
        return task


def _lease(*, state: str = "active") -> dict[str, Any]:
    return {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "token_digest": "a" * 64,
        "phase": "execute",
        "state": state,
        "revision": 2,
        "issued_at": 1.0,
        "expires_at": 9999999999.0,
        "worker_url": "http://worker:5000",
        "source_task_id": "source-1",
        "plan_id": "plan-1",
        "release_epoch": "epoch-1",
        "request_fingerprint": "b" * 64,
    }


def _answer() -> str:
    return json.dumps(
        {
            "schema": "grounded_answer.v1",
            "answer": "The tool run completed.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "The tool run completed.",
                    "claim_type": "tool_result",
                    "citation_refs": ["RUN_0001"],
                    "confidence": "verified",
                }
            ],
            "unsupported_notes": [],
        },
        sort_keys=True,
    )


def _fixture(
    *,
    include_record: bool = True,
    tamper_candidate: bool = False,
) -> tuple[
    RecoveryHubRunEvidenceService,
    TaskRepository,
    SimpleNamespace,
    dict[str, Any],
    dict[str, Any],
]:
    service = RecoveryHubRunEvidenceService(
        clock=lambda: 10.0,
        record_id_factory=lambda: "hub-tool-record-1",
    )
    details: dict[str, Any] = {}
    active_lease = _lease()
    if include_record:
        details = service.reserve_context(
            task_id="recovery-child-1",
            details=details,
            worker_url="http://worker:5000",
            replace=True,
        )
        details = service.prepare_for_dispatch_lease(
            task_id="recovery-child-1",
            details=details,
            phase="execute",
            lease=active_lease,
        )
    details["recovery_dispatch_lease"] = {
        **active_lease,
        "state": "worker_admitted",
    }
    record = dict(
        details.get("recovery_hub_tool_run_record") or {}
    )
    output = _answer()
    candidate = ToolRunCatalogService().build_run_entry(
        task_id="recovery-child-1",
        index=99,
        tool_name="shell",
        command="pytest -q",
        exit_code=0,
        stdout=output,
        stderr="",
        started_at=2.0,
        ended_at=3.0,
        source_id=(
            str(record.get("source_id") or "RUN_0001")
        ),
        run_id=(
            str(record.get("record_id") or "worker-forged-record")
        ),
    )
    if tamper_candidate:
        candidate["stdout_hash"] = "f" * 32
    boundary = SimpleNamespace(
        task_id="recovery-child-1",
        phase="execute",
        mutations=[
            {
                "verification_projection": {
                    "answer_verification": {
                        "tool_run_refs": [candidate]
                    }
                }
            }
        ],
    )
    envelope = RecoveryWorkerResultService().build(boundary)
    task = SimpleNamespace(
        id="recovery-child-1",
        derivation_reason="goal_task_recovery",
        status="in_progress",
        last_output=output,
        last_exit_code=0,
        history=[
            {
                "event_type": "execution_result",
                "status": "completed",
                "command": "pytest -q",
                "output": output,
                "exit_code": 0,
            }
        ],
        status_reason_details=details,
        verification_status={
            "recovery_worker_results": {
                "execute": envelope,
            }
        },
        updated_at=1.0,
    )
    repository = TaskRepository(task)
    service._repository_provider = lambda: SimpleNamespace(
        task_repo=repository
    )
    response = {
        "status": "completed",
        "output": output,
        "exit_code": 0,
        "recovery_worker_result": envelope,
    }
    request = {
        "task_id": task.id,
        "command": "pytest -q",
        "tool_calls": None,
        "timeout": 60,
        "retries": 0,
        "retry_delay": 1,
        "retry_policy_override": None,
    }
    from agent.services.recovery_dispatch_gate_service import (
        recovery_dispatch_request_fingerprint,
    )

    fingerprint = recovery_dispatch_request_fingerprint(
        "execute",
        request,
    )
    task.status_reason_details["recovery_dispatch_lease"][
        "request_fingerprint"
    ] = fingerprint
    if include_record:
        task.status_reason_details[
            "recovery_hub_tool_run_record"
        ]["execute_lease"]["request_fingerprint"] = fingerprint
        record_value = task.status_reason_details[
            "recovery_hub_tool_run_record"
        ]
        record_value["record_digest"] = service._record_digest(
            record_value
        )
    return service, repository, task, response, request


def test_hub_persists_only_pre_reserved_result_bound_run_evidence() -> None:
    service, repository, task, response, request = _fixture()

    entries = service.accept_worker_result(
        task_id=task.id,
        response=response,
        request_data=request,
    )

    assert len(entries) == 1
    assert entries[0]["source_id"] == "RUN_0001"
    assert entries[0]["run_id"] == "hub-tool-record-1"
    assert entries[0]["task_id"] == task.id
    assert entries[0]["evidence_binding"][
        "lease_revision"
    ] == 2
    assert entries[0]["evidence_binding"][
        "worker_result_digest"
    ] == response["recovery_worker_result"]["digest"]
    assert service.for_task(task.id) == entries
    assert len(repository.save_calls) == 1


def test_worker_run_candidate_tampering_is_rejected() -> None:
    service, repository, task, response, request = _fixture(
        tamper_candidate=True
    )

    with pytest.raises(
        RecoveryHubRunEvidenceError,
        match="recovery_worker_run_evidence_mismatch",
    ):
        service.accept_worker_result(
            task_id=task.id,
            response=response,
            request_data=request,
        )

    assert repository.save_calls == []
    assert "recovery_hub_run_evidence" not in (
        task.verification_status
    )


def test_worker_run_candidate_without_hub_record_never_becomes_authority() -> None:
    service, repository, task, response, request = _fixture(
        include_record=False
    )

    assert service.accept_worker_result(
        task_id=task.id,
        response=response,
        request_data=request,
    ) == []
    assert service.for_task(task.id) is None
    assert repository.save_calls == []


def test_alternate_valid_worker_context_is_not_hub_authority() -> None:
    service, _repository, task, _response, _request = _fixture()
    alternate = RecoveryHubRunEvidenceService(
        record_id_factory=lambda: "worker-substituted-record",
    ).reserve_context(
        task_id=task.id,
        details={},
        worker_url="http://worker:5000",
        replace=True,
    )["recovery_tool_run_context"]

    with pytest.raises(
        RecoveryHubRunEvidenceError,
        match="recovery_tool_run_context_mismatch",
    ):
        service.bind_request_context(
            task=task,
            value=alternate,
        )


def test_same_result_replay_is_idempotent_and_changed_replay_fails() -> None:
    service, repository, task, response, request = _fixture()
    first = service.accept_worker_result(
        task_id=task.id,
        response=response,
        request_data=request,
    )
    second = service.accept_worker_result(
        task_id=task.id,
        response=response,
        request_data=request,
    )

    assert second == first
    assert len(repository.save_calls) == 1

    changed = copy.deepcopy(response)
    changed["output"] = '{"changed":true}'
    task.last_output = changed["output"]
    task.history[-1]["output"] = changed["output"]
    with pytest.raises(
        RecoveryHubRunEvidenceError,
        match="recovery_hub_run_result_replay_mismatch",
    ):
        service.accept_worker_result(
            task_id=task.id,
            response=changed,
            request_data=request,
        )


def test_persisted_catalog_tampering_fails_closed() -> None:
    service, _repository, task, response, request = _fixture()
    service.accept_worker_result(
        task_id=task.id,
        response=response,
        request_data=request,
    )
    task.verification_status["recovery_hub_run_evidence"][
        "entries"
    ][0]["stdout_hash"] = "0" * 32

    with pytest.raises(
        RecoveryHubRunEvidenceError,
        match="recovery_hub_run_evidence_catalog_digest_mismatch",
    ):
        service.for_task(task.id)


def test_grounding_default_port_reads_hub_persisted_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _repository, task, response, request = _fixture()
    expected = service.accept_worker_result(
        task_id=task.id,
        response=response,
        request_data=request,
    )
    monkeypatch.setattr(
        "agent.services.recovery_hub_run_evidence_service."
        "get_recovery_hub_run_evidence_service",
        lambda: service,
    )
    from agent.services.recovery_grounding_verification_service import (
        RecoveryGroundingVerificationService,
    )

    values, available, error = (
        RecoveryGroundingVerificationService()
        ._hub_run_evidence(task.id)
    )

    assert values == expected
    assert available is True
    assert error is None


def test_split_db_worker_uses_fingerprint_bound_request_context_without_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing Worker Task receives new Hub context request-locally."""

    from agent.config import settings
    from agent.models import TaskStepExecuteRequest
    from agent.services._task_scoped_step_orchestrator import (
        _admit_task_scoped_dispatch,
        _apply_request_run_evidence_context,
    )
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateDecision,
        recovery_dispatch_request_fingerprint,
    )

    hub_service = RecoveryHubRunEvidenceService(
        record_id_factory=lambda: "hub-tool-record-split-db",
    )
    hub_details = hub_service.reserve_context(
        task_id="split-db-child",
        details={},
        worker_url="http://worker:5000",
        replace=True,
    )
    context = hub_details["recovery_tool_run_context"]
    # This represents an older row in the isolated Worker database.  It has
    # the release marker but not the context reserved later by the Hub.
    worker_task = {
        "id": "split-db-child",
        "source_task_id": "source-1",
        "plan_id": "plan-1",
        "derivation_reason": "goal_task_recovery",
        "status_reason_details": {
            "model_recovery_release": {"release_epoch": "epoch-1"}
        },
    }
    request = TaskStepExecuteRequest(
        task_id=worker_task["id"],
        command="pytest -q",
        recovery_run_evidence_context=context,
        dispatch_lease_token="opaque-lease",
        dispatch_lease_phase="execute",
    )
    fingerprints: list[str] = []

    class WorkerGate:
        @staticmethod
        def is_recovery_child(_task: Any) -> bool:
            return True

        @staticmethod
        def admit_incoming_dispatch(
            *,
            task: Any,
            token: str,
            phase: str,
            request_fingerprint: str,
        ) -> RecoveryDispatchGateDecision:
            del task, token, phase
            fingerprints.append(request_fingerprint)
            return RecoveryDispatchGateDecision(
                True,
                "recovery_dispatch_lease_valid",
            )

    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(
        "agent.services.recovery_dispatch_gate_service."
        "get_recovery_dispatch_gate_service",
        lambda: WorkerGate(),
    )

    admission = _admit_task_scoped_dispatch(
        tid=worker_task["id"],
        task=worker_task,
        request_data=request,
        phase="execute",
    )
    assert admission["error"] is None
    assert fingerprints == [
        recovery_dispatch_request_fingerprint("execute", request)
    ]
    without_context = request.model_dump()
    without_context["recovery_run_evidence_context"] = None
    assert fingerprints[0] != recovery_dispatch_request_fingerprint(
        "execute",
        without_context,
    )

    _apply_request_run_evidence_context(
        task=worker_task,
        request_data=request,
    )
    assert worker_task["status_reason_details"][
        "recovery_tool_run_context"
    ] == context
    from ananta_contracts.recovery_run_evidence import (
        recovery_tool_run_context_from_task,
    )

    supplied = recovery_tool_run_context_from_task(worker_task)
    assert supplied is not None
    assert supplied["records"][0] == {
        "record_id": "hub-tool-record-split-db",
        "source_id": "RUN_0001",
        "source_type": "tool_run",
        "allowed_for_llm_scope": True,
    }
