from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from agent.common.recovery_result_write_boundary import (
    defer_recovery_task_writes,
    defer_task_status_mutation,
)
from agent.repositories.tasks import TaskRepository
from agent.services._task_scoped_forwarding import (
    persist_forwarded_execution,
    persist_forwarded_proposal,
)
from agent.services.recovery_dispatch_gate_service import (
    RecoveryDispatchGateDecision,
    RecoveryDispatchGateService,
    build_recovery_result_candidate,
    recovery_accepted_result_digest,
)
from agent.services.recovery_plan_contract import (
    build_recovery_dependency_binding,
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
    calculate_recovery_task_payload_digest,
)
from agent.services.recovery_result_verification_service import (
    RecoveryResultVerificationService,
)
from agent.services.recovery_source_callback_delivery import (
    RecoverySourceCallbackDelivery,
)
from agent.services.recovery_source_finalization_service import (
    RecoverySourceFinalizationService,
)
from agent.services.recovery_source_post_commit_service import (
    RecoverySourcePostCommitService,
)
from agent.services.recovery_worker_result_service import (
    RecoveryWorkerResultError,
    RecoveryWorkerResultService,
)
from agent.services.verification_service import VerificationService


class Record(SimpleNamespace):
    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(vars(self))


class MemoryRepository:
    def __init__(self, rows: list[Record] | None = None) -> None:
        self.rows = {
            str(row.id): row for row in list(rows or [])
        }
        self.save_calls: list[Record] = []

    def get_by_id(self, row_id: str) -> Record | None:
        return self.rows.get(str(row_id))

    def save(self, row: Record) -> Record:
        self.rows[str(row.id)] = row
        self.save_calls.append(row)
        return row


class FailingSaveRepository(MemoryRepository):
    def save(self, row: Record) -> Record:
        raise RuntimeError("injected_result_commit_failure")


class PlanNodeRepository(MemoryRepository):
    def get_by_plan_id(self, plan_id: str) -> list[Record]:
        return sorted(
            [
                row
                for row in self.rows.values()
                if str(row.plan_id) == str(plan_id)
            ],
            key=lambda row: (int(row.position), str(row.id)),
        )


class LockPort:
    @contextlib.contextmanager
    def mutation_lock(self, _task_id: str):
        yield True

    @contextlib.contextmanager
    def mutation_locks(self, _task_ids: set[str]):
        yield True


def test_dispatch_abort_preserves_an_already_terminal_result() -> None:
    task = Record(
        id="recovery-child",
        source_task_id="recovery-source",
        derivation_reason="goal_task_recovery",
        status="completed",
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "recovery-source"
            },
            "recovery_dispatch_lease": {
                "state": "result_accepted",
                "revision": 4,
                "accepted_result_phase": "execute",
                "accepted_result_status": "completed",
                "accepted_result_terminal": True,
            },
        },
        verification_status={},
        last_output="accepted",
        last_exit_code=0,
    )
    task.status_reason_details["recovery_dispatch_lease"][
        "accepted_result_digest"
    ] = recovery_accepted_result_digest(task)
    repos = Record(task_repo=MemoryRepository([task]))
    service = RecoveryDispatchGateService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
    )

    status = service.abort_dispatch_lease(
        task.id,
        target_status="paused",
        reason_code="recovery_dispatch_timeout",
        error="timeout",
    )

    assert status == "completed"
    assert task.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "result_accepted"


def test_dispatch_abort_revokes_lease_before_status_transition() -> None:
    task = Record(
        id="recovery-child",
        goal_id="goal-recovery",
        goal_trace_id="trace-recovery",
        plan_id="plan-recovery",
        source_task_id="recovery-source",
        derivation_reason="goal_task_recovery",
        status="in_progress",
        status_reason_code=None,
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "recovery-source"
            },
            "recovery_dispatch_lease": {
                "state": "worker_admitted",
                "phase": "execute",
                "revision": 4,
                "token_digest": "a" * 64,
                "request_fingerprint": "b" * 64,
            },
        },
        history=[],
        updated_at=0.0,
        error=None,
    )
    repos = Record(task_repo=MemoryRepository([task]))

    service = RecoveryDispatchGateService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
    )

    status = service.abort_dispatch_lease(
        task.id,
        target_status="paused",
        reason_code="recovery_dispatch_timeout",
        error="timeout",
    )

    persisted = repos.task_repo.get_by_id(task.id)
    lease = persisted.status_reason_details[
        "recovery_dispatch_lease"
    ]
    assert status == "paused"
    assert persisted.status == "paused"
    assert lease["state"] == "revoked"
    assert lease["revision"] == 5
    assert lease["revocation_reason"] == (
        "recovery_dispatch_timeout"
    )


def test_dispatch_abort_rejects_unproven_terminal_result() -> None:
    task = Record(
        id="recovery-child",
        goal_id="goal-recovery",
        goal_trace_id="trace-recovery",
        plan_id="plan-recovery",
        source_task_id="recovery-source",
        derivation_reason="goal_task_recovery",
        status="completed",
        status_reason_code=None,
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "recovery-source"
            },
            "recovery_dispatch_lease": {
                "state": "worker_admitted",
                "phase": "execute",
                "revision": 7,
                "token_digest": "a" * 64,
                "request_fingerprint": "b" * 64,
            },
        },
        verification_status={"status": "passed"},
        last_output="uncommitted",
        last_exit_code=0,
        history=[],
        updated_at=0.0,
        error=None,
    )
    repos = Record(task_repo=MemoryRepository([task]))
    service = RecoveryDispatchGateService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
    )

    status = service.abort_dispatch_lease(
        task.id,
        target_status="paused",
        reason_code="recovery_dispatch_timeout",
        error="timeout",
    )

    persisted = repos.task_repo.get_by_id(task.id)
    lease = persisted.status_reason_details[
        "recovery_dispatch_lease"
    ]
    assert status == "verification_failed"
    assert persisted.status == "verification_failed"
    assert (
        persisted.status_reason_code
        == "recovery_result_verification_failed"
    )
    assert lease["state"] == "revoked"
    assert (
        lease["revocation_reason"]
        == "recovery_terminal_without_accepted_result"
    )


def test_revoke_reports_false_when_terminal_acceptance_wins_save_race() -> None:
    task = Record(
        id="recovery-child",
        source_task_id="recovery-source",
        derivation_reason="goal_task_recovery",
        status="in_progress",
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "recovery-source"
            },
            "recovery_dispatch_lease": {
                "schema": "ananta.recovery_dispatch_lease.v1",
                "state": "worker_admitted",
                "phase": "execute",
                "revision": 3,
                "token_digest": "a" * 64,
                "request_fingerprint": "b" * 64,
            },
        },
        verification_status={},
        last_output=None,
        last_exit_code=None,
    )
    winner = Record(
        **{
            **vars(task),
            "status": "completed",
            "last_output": "accepted",
            "last_exit_code": 0,
            "verification_status": {
                "status": "passed",
                "record_id": "verification-record",
                "results": {"final_passed": True},
            },
            "status_reason_details": {
                **task.status_reason_details,
                "recovery_dispatch_lease": {
                    **task.status_reason_details[
                        "recovery_dispatch_lease"
                    ],
                    "state": "result_accepted",
                    "accepted_result_phase": "execute",
                    "accepted_result_status": "completed",
                    "accepted_result_terminal": True,
                },
            },
        }
    )
    winner.status_reason_details[
        "recovery_dispatch_lease"
    ]["accepted_result_digest"] = recovery_accepted_result_digest(
        winner
    )

    class RacingRepository(MemoryRepository):
        def save(self, _row: Record) -> Record:
            self.rows[winner.id] = winner
            return winner

    repository = RacingRepository([task])
    service = RecoveryDispatchGateService(
        repository_provider=lambda: Record(
            task_repo=repository
        ),
        mutation_lock_provider=LockPort,
    )

    assert service.revoke_dispatch_lease(
        task.id,
        reason_code="late_transport_timeout",
    ) is False
    assert repository.get_by_id(task.id).status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "result_accepted"


def test_worker_result_boundary_suppresses_all_authoritative_write_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import task_runtime_service

    authoritative = Record(id="recovery-child", status="todo")
    repository = TaskRepository()
    monkeypatch.setattr(
        repository,
        "get_by_id",
        lambda task_id: (
            authoritative
            if task_id == authoritative.id
            else None
        ),
    )

    with defer_recovery_task_writes(
        task_id=authoritative.id,
        phase="execute",
    ) as boundary:
        task_runtime_service.update_local_task_status(
            authoritative.id,
            "completed",
            last_output="worker result must stay staged",
        )
        cas_result = (
            task_runtime_service.compare_and_set_local_task_status(
                authoritative.id,
                "completed",
                expected_statuses={"in_progress"},
                last_exit_code=0,
            )
        )
        repository_result = repository.save(
            Record(id=authoritative.id, status="completed")
        )
        verification_result = (
            VerificationService().create_or_update_record(
                authoritative.id,
                trace_id="trace-worker",
                output="worker verification",
                exit_code=0,
                gate_results={"passed": True},
            )
        )

    assert authoritative.status == "todo"
    assert cas_result is True
    assert repository_result is authoritative
    assert verification_result is None
    assert [
        mutation["operation"]
        for mutation in boundary.mutations
    ] == [
        "status_update",
        "compare_and_set",
        "repository_save",
        "verification_record",
    ]
    assert defer_task_status_mutation(
        authoritative.id,
        "failed",
        event_type=None,
        event_actor="worker",
        event_details=None,
        force=False,
        values={},
    ) is False


def test_worker_result_projection_is_typed_bounded_and_digest_bound() -> None:
    with defer_recovery_task_writes(
        task_id="recovery-child",
        phase="propose",
    ) as boundary:
        assert defer_task_status_mutation(
            "recovery-child",
            "proposing",
            event_type=None,
            event_actor="worker",
            event_details=None,
            force=False,
            values={
                "verification_status": {
                    "source_catalog": {
                        "schema": "source_catalog.v2",
                        "sources": [{"source_id": "SRC_1"}],
                    },
                    "answer_verification": {
                        "citation_verification_status": "pending"
                    },
                    "status": "passed",
                    "record_id": "worker-forged-authority",
                }
            },
        )

    service = RecoveryWorkerResultService()
    envelope = service.build(boundary)

    assert envelope["task_id"] == "recovery-child"
    assert envelope["phase"] == "propose"
    assert len(envelope["digest"]) == 64
    projection = envelope["verification_projection"]
    assert set(projection) == {
        "source_catalog",
        "answer_verification",
    }
    assert "record_id" not in projection
    assert (
        service.validate(
            envelope,
            task_id="recovery-child",
            phase="propose",
        )
        == envelope
    )

    tampered = {
        **envelope,
        "verification_projection": {
            **projection,
            "answer_verification": {
                "citation_verification_status": "verified"
            },
        },
    }
    with pytest.raises(
        RecoveryWorkerResultError,
        match="digest_mismatch",
    ):
        service.validate(
            tampered,
            task_id="recovery-child",
            phase="propose",
        )


def test_proposal_projection_is_execution_context_not_hub_authority() -> None:
    service = RecoveryWorkerResultService()
    boundary = SimpleNamespace(
        task_id="recovery-child",
        phase="propose",
        mutations=[
            {
                "verification_projection": {
                    "source_catalog": {
                        "schema": "source_catalog.v2",
                        "sources": [{"source_id": "SRC_1"}],
                    }
                }
            }
        ],
    )
    envelope = service.build(boundary)
    local_task = {
        "id": "recovery-child",
        "verification_status": {
            "status": "pending",
            "record_id": "hub-record",
        },
    }

    service.apply_proposal_context(
        task=local_task,
        value=envelope,
    )

    verification = local_task["verification_status"]
    assert verification["status"] == "pending"
    assert verification["record_id"] == "hub-record"
    assert verification["source_catalog"]["sources"][0][
        "source_id"
    ] == "SRC_1"
    assert verification["recovery_worker_results"][
        "propose"
    ] == envelope


def test_invalid_proposal_envelope_has_no_hub_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = RecoveryWorkerResultService().build(
        SimpleNamespace(
            task_id="recovery-child",
            phase="propose",
            mutations=[],
        )
    )
    envelope["digest"] = "0" * 64
    persistence_calls: list[str] = []
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.get_core_services",
        lambda: persistence_calls.append("persist") or None,
    )

    with pytest.raises(
        RecoveryWorkerResultError,
        match="digest_mismatch",
    ):
        persist_forwarded_proposal(
            {
                "command": "true",
                "reason": "invalid envelope",
                "recovery_worker_result": envelope,
            },
            {
                "id": "recovery-child",
                "status": "proposing",
                "derivation_reason": "goal_task_recovery",
                "verification_status": {},
                "status_reason_details": {
                    "model_recovery_release": {
                        "source_task_id": "source-task"
                    }
                },
            },
            allow_synthetic_llm_profile_fallback=lambda: False,
        )

    assert persistence_calls == []


def test_execute_result_stays_nonterminal_until_result_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_result_service = RecoveryWorkerResultService()
    proposal_envelope = worker_result_service.build(
        SimpleNamespace(
            task_id="recovery-child",
            phase="propose",
            mutations=[],
        )
    )
    execute_envelope = worker_result_service.build(
        SimpleNamespace(
            task_id="recovery-child",
            phase="execute",
            mutations=[],
        )
    )
    task = Record(
        id="recovery-child",
        status="in_progress",
        derivation_reason="goal_task_recovery",
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "source-task"
            },
            "recovery_dispatch_lease": {
                "state": "worker_admitted",
                "revision": 2,
                "token_digest": "a" * 64,
                "request_fingerprint": "b" * 64,
            },
        },
        verification_status={
            "recovery_worker_results": {
                "propose": proposal_envelope
            }
        },
        history=[],
        last_output=None,
        last_exit_code=None,
        updated_at=0.0,
    )
    repository = MemoryRepository([task])
    repos = Record(task_repo=repository)
    observed_statuses: list[str] = []

    def update_status(
        task_id: str,
        status: str,
        **values: Any,
    ) -> None:
        row = repository.get_by_id(task_id)
        observed_statuses.append(status)
        assert status != "completed"
        row.status = status
        for key, value in values.items():
            setattr(row, key, value)
        repository.save(row)

    class Verifier:
        @staticmethod
        def verify_and_record(**_kwargs: Any) -> dict[str, Any]:
            row = repository.get_by_id(task.id)
            assert row.status == "in_progress"
            row.verification_status = {
                **dict(row.verification_status or {}),
                "status": "passed",
                "record_id": "verification-record",
                "results": {"final_passed": True},
            }
            repository.save(row)
            return {
                "record_id": "verification-record",
                "status": "passed",
            }

    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.get_repository_registry",
        lambda *_args, **_kwargs: repos,
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        update_status,
    )
    monkeypatch.setattr(
        "agent.services.recovery_result_verification_service.get_recovery_result_verification_service",
        lambda: Verifier(),
    )

    persist_forwarded_execution(
        tid=task.id,
        response={
            "status": "completed",
            "output": "verified output",
            "exit_code": 0,
            "recovery_worker_result": execute_envelope,
        },
        task={
            "id": task.id,
            "status": task.status,
            "derivation_reason": task.derivation_reason,
            "status_reason_details": task.status_reason_details,
            "verification_status": {},
            "history": [],
            "last_proposal": {},
            "assigned_agent_url": "http://worker-alpha:5000",
            "description": "Execute approved recovery step.",
        },
        request_data=SimpleNamespace(command="true"),
    )

    persisted = repository.get_by_id(task.id)
    assert observed_statuses == ["in_progress"]
    assert persisted.status == "in_progress"
    assert persisted.status_reason_details[
        "recovery_result_candidate"
    ]["status"] == "completed"
    assert persisted.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "worker_admitted"
    assert persisted.verification_status[
        "recovery_worker_results"
    ] == {
        "propose": proposal_envelope,
        "execute": execute_envelope,
    }


def test_result_candidate_is_bound_to_exact_dispatch_lease() -> None:
    task = Record(
        id="recovery-child",
        status="in_progress",
        status_reason_details={
            "recovery_dispatch_lease": {
                "state": "worker_admitted",
                "revision": 3,
                "token_digest": "c" * 64,
                "request_fingerprint": "d" * 64,
            },
            "recovery_result_candidate": (
                build_recovery_result_candidate(
                    task_id="recovery-child",
                    status="completed",
                    verification_record_id="verification-record",
                    lease_revision=2,
                    lease_token_digest="a" * 64,
                    request_fingerprint="b" * 64,
                )
            ),
        },
        verification_status={
            "status": "passed",
            "record_id": "verification-record",
            "results": {"final_passed": True},
        },
    )

    with pytest.raises(
        RuntimeError,
        match="recovery_result_candidate_binding_invalid",
    ):
        RecoveryDispatchGateService._validated_result_candidate(
            task,
            phase="execute",
        )


def _forwarded_recovery_receipt(
    *,
    task_id: str,
    source_index: int = 0,
) -> dict[str, Any]:
    identity = f"{source_index + 1:032x}"
    return {
        "kind": "workspace_file",
        "task_id": task_id,
        "artifact_id": f"recovery-artifact-{identity}",
        "artifact_version_id": (
            f"recovery-artifact-version-{identity}"
        ),
        "filename": f"result-{source_index}.txt",
        "media_type": "text/plain",
        "workspace_relative_path": (
            f"result-{source_index}.txt"
        ),
        "content_hash": f"{source_index + 1:064x}",
        "size_bytes": 1,
        "provenance_summary": {
            "schema": (
                "ananta.recovery_artifact_provenance.v1"
            ),
            "authority": "hub",
            "ingress": "workspace",
            "worker_url": "http://worker-alpha:5000",
            "manifest_digest": "a" * 64,
            "source_index": source_index,
        },
    }


@pytest.mark.parametrize(
    ("artifacts_factory", "reason"),
    [
        (
            lambda task_id: [
                _forwarded_recovery_receipt(
                    task_id=task_id,
                    source_index=index,
                )
                for index in range(33)
            ],
            "recovery_artifact_receipt_count_exceeded",
        ),
        (
            lambda task_id: [
                {
                    **_forwarded_recovery_receipt(
                        task_id=task_id
                    ),
                    "untrusted_padding": "x" * 262_145,
                }
            ],
            "recovery_artifact_receipt_fields_invalid",
        ),
        (
            lambda task_id: [
                {
                    **_forwarded_recovery_receipt(
                        task_id=task_id
                    ),
                    "worker_verdict": "passed",
                }
            ],
            "recovery_artifact_receipt_fields_invalid",
        ),
    ],
)
def test_forwarded_recovery_artifacts_are_bounded_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    artifacts_factory: Any,
    reason: str,
) -> None:
    task_id = "recovery-child-forwarding-boundary"
    authoritative = Record(
        id=task_id,
        status="in_progress",
        derivation_reason="goal_task_recovery",
        assigned_agent_url="http://worker-alpha:5000",
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "source-task"
            }
        },
        history=[],
        last_proposal={},
        verification_status={},
    )
    repository = MemoryRepository([authoritative])
    repos = Record(task_repo=repository)
    status_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.get_repository_registry",
        lambda *_args, **_kwargs: repos,
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: status_calls.append(
            (*args, kwargs)
        ),
    )
    task = {
        "id": task_id,
        "status": "in_progress",
        "derivation_reason": "goal_task_recovery",
        "assigned_agent_url": "http://worker-alpha:5000",
        "status_reason_details": (
            authoritative.status_reason_details
        ),
        "history": [],
        "last_proposal": {},
        "verification_status": {},
        "description": "Bound the Recovery result.",
    }

    with pytest.raises(ValueError, match=reason):
        persist_forwarded_execution(
            tid=task_id,
            response={
                "status": "completed",
                "output": "untrusted",
                "exit_code": 0,
                "artifacts": artifacts_factory(task_id),
            },
            task=task,
            request_data=SimpleNamespace(command="true"),
        )

    assert status_calls == []
    assert authoritative.history == []
    assert authoritative.verification_status == {}
    assert repository.save_calls == []


@pytest.mark.parametrize("save_fails", [False, True])
def test_execute_result_status_and_lease_commit_as_one_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    save_fails: bool,
) -> None:
    token = "opaque-result-token"
    task = Record(
        id="recovery-child",
        source_task_id="source-task",
        derivation_reason="goal_task_recovery",
        status="in_progress",
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": "source-task"
            },
            "recovery_dispatch_lease": {
                "state": "worker_admitted",
                "phase": "execute",
                "revision": 2,
                "token_digest": "a" * 64,
                "request_fingerprint": "b" * 64,
            },
            "recovery_result_candidate": (
                build_recovery_result_candidate(
                    task_id="recovery-child",
                    status="completed",
                    verification_record_id="verification-record",
                    lease_revision=2,
                    lease_token_digest="a" * 64,
                    request_fingerprint="b" * 64,
                )
            ),
        },
        verification_status={
            "status": "passed",
            "record_id": "verification-record",
            "results": {"final_passed": True},
        },
        last_output="verified output",
        last_exit_code=0,
        history=[],
        updated_at=0.0,
    )
    repository: MemoryRepository = (
        FailingSaveRepository([task])
        if save_fails
        else MemoryRepository([task])
    )
    repos = Record(task_repo=repository)
    service = RecoveryDispatchGateService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
    )
    allowed = RecoveryDispatchGateDecision(
        True,
        "recovery_release_gate_valid",
        source_task_id="source-task",
    )

    @contextlib.contextmanager
    def allowed_dispatch_guard(*_args: Any, **_kwargs: Any):
        yield allowed

    service.dispatch_guard = allowed_dispatch_guard  # type: ignore[method-assign]
    service.evaluate_task = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: allowed
    )
    service._evaluate_lease_binding = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: allowed
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.append_task_history_event",
        lambda row, **_kwargs: setattr(
            row,
            "history",
            [*list(row.history or []), {"event_type": "committed"}],
        ),
    )
    monkeypatch.setattr(
        "agent.services.autopilot_wake_service.request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )

    result_context = service.result_guard(
        task.id,
        token=token,
        phase="execute",
        request_fingerprint="f" * 64,
        worker_url="http://worker-alpha:5000",
    )
    if save_fails:
        with pytest.raises(
            RuntimeError,
            match="injected_result_commit_failure",
        ):
            with result_context as decision:
                assert decision.allowed is True
        persisted = repository.get_by_id(task.id)
        assert persisted.status == "in_progress"
        assert persisted.status_reason_details[
            "recovery_dispatch_lease"
        ]["state"] == "worker_admitted"
        return

    with result_context as decision:
        assert decision.allowed is True

    persisted = repository.get_by_id(task.id)
    lease = persisted.status_reason_details[
        "recovery_dispatch_lease"
    ]
    assert persisted.status == "completed"
    assert lease["state"] == "result_accepted"
    assert lease["accepted_result_phase"] == "execute"
    assert lease["accepted_result_status"] == "completed"
    assert lease["accepted_result_terminal"] is True
    assert lease["accepted_result_digest"] == (
        recovery_accepted_result_digest(persisted)
    )


def _build_recovery_fixture(
    *,
    external_dependency: bool = False,
) -> Record:
    goal = Record(
        id="goal-recovery",
        goal="Recover the source task",
        team_id="team-recovery",
        status="in_progress",
        mode="existing_project",
        mode_data={},
        execution_preferences={},
    )
    release_epoch = "release-epoch-1"
    source = Record(
        id="source-task",
        goal_id=goal.id,
        goal_trace_id="trace-recovery",
        plan_id="plan-recovery",
        team_id=goal.team_id,
        title="Recovery source",
        description="Aggregate approved recovery evidence.",
        status="blocked_by_dependency",
        status_reason_code="model_recovery_tasks_materialized",
        status_reason_details={
            "model_recovery": {
                "plan_id": "plan-recovery",
                "release_epoch": release_epoch,
                "approval_request_id": "approval-recovery",
                "recovery_key": "recovery-key",
                "created_task_ids": ["recovery-child"],
            },
            "model_recovery_strategy": {"status": "running"},
        },
        verification_status={},
        verification_spec={},
        depends_on=["recovery-child"],
        history=[],
        last_output=None,
        last_exit_code=None,
        updated_at=0.0,
    )
    child = Record(
        id="recovery-child",
        goal_id=goal.id,
        goal_trace_id=source.goal_trace_id,
        plan_id=source.plan_id,
        plan_node_id="recovery-node",
        parent_task_id=None,
        source_task_id=source.id,
        team_id=goal.team_id,
        title="Approved recovery task",
        description="Execute the approved recovery step.",
        priority="Medium",
        derivation_reason="goal_task_recovery",
        derivation_depth=1,
        task_kind="analysis",
        retrieval_intent="",
        required_context_scope="",
        preferred_bundle_mode="",
        required_capabilities=[],
        context_bundle_id=None,
        worker_execution_context={},
        worker_execution_contract={},
        expected_artifacts=[],
        verification_spec={},
        depends_on=[],
        status="completed",
        status_reason_details={
            "model_recovery_release": {
                "release_epoch": release_epoch,
                "plan_id": source.plan_id,
                "source_task_id": source.id,
                "goal_id": goal.id,
                "team_id": goal.team_id,
                "approval_request_id": "approval-recovery",
                "recovery_key": "recovery-key",
            },
        },
        verification_status={
            "status": "passed",
            "record_id": "verification-record-1",
            "results": {"final_passed": True},
            "execution_artifacts": [],
        },
        last_output="Recovery tests passed successfully.",
        last_exit_code=0,
        history=[],
        updated_at=0.0,
    )
    node = Record(
        id=child.plan_node_id,
        plan_id=source.plan_id,
        node_key="recovery-node-key",
        title=child.title,
        description=child.description,
        priority=child.priority,
        position=0,
        depends_on=[],
        editable=True,
        rationale={
            "task_kind": child.task_kind,
            "retrieval_intent": child.retrieval_intent,
            "required_context_scope": child.required_context_scope,
            "preferred_bundle_mode": child.preferred_bundle_mode,
            "required_capabilities": [],
        },
        verification_spec={},
        materialized_task_id=child.id,
    )
    plan = Record(
        id=source.plan_id,
        goal_id=goal.id,
        trace_id=source.goal_trace_id,
        status="materialized",
        planning_mode="generic",
        rationale={
            "team_id": goal.team_id,
            "source_task_id": source.id,
            "recovery_key": "recovery-key",
            "materialization_release_state": "committed",
            "materialization_release_epoch": release_epoch,
            "materialization_release_approval_id": (
                "approval-recovery"
            ),
            "materialization_release_source_task_id": source.id,
            "materialization_release_goal_id": goal.id,
            "materialization_release_team_id": goal.team_id,
        },
    )
    plan.rationale["materialization_inputs_digest"] = (
        calculate_recovery_materialization_inputs_digest(goal)
    )
    plan.rationale["plan_digest"] = calculate_recovery_plan_digest(
        plan,
        [node],
    )
    child.status_reason_details[
        "model_recovery_release"
    ]["task_payload_digest"] = (
        calculate_recovery_task_payload_digest(child)
    )
    child.status_reason_details["recovery_dispatch_lease"] = {
        "phase": "execute",
        "state": "result_accepted",
        "accepted_at": 10.0,
        "accepted_result_phase": "execute",
        "accepted_result_status": "completed",
        "accepted_result_terminal": True,
    }
    child.status_reason_details[
        "recovery_dispatch_lease"
    ]["accepted_result_digest"] = recovery_accepted_result_digest(
        child
    )

    tasks = [source, child]
    external = None
    if external_dependency:
        external = Record(
            id="preexisting-dependency",
            status="completed",
        )
        source.depends_on.insert(0, external.id)
        tasks.append(external)
    dependency_binding = build_recovery_dependency_binding(
        source_task_id=source.id,
        preexisting_dependency_ids=(
            [external.id] if external is not None else []
        ),
        child_task_ids=[child.id],
    )
    source.status_reason_details["model_recovery"][
        "dependency_binding"
    ] = dependency_binding
    plan.rationale[
        "materialization_dependency_binding_digest"
    ] = dependency_binding["digest"]
    plan.rationale["plan_digest"] = calculate_recovery_plan_digest(
        plan,
        [node],
    )
    repos = Record(
        task_repo=MemoryRepository(tasks),
        goal_repo=MemoryRepository([goal]),
        plan_repo=MemoryRepository([plan]),
        plan_node_repo=PlanNodeRepository([node]),
    )
    service = RecoverySourceFinalizationService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
    )
    return Record(
        service=service,
        repos=repos,
        source=source,
        child=child,
        external=external,
        goal=goal,
        plan=plan,
        node=node,
    )


def test_finalizer_uses_exact_approved_plan_nodes_and_accepts_completed_external_dependency() -> None:
    fixture = _build_recovery_fixture(
        external_dependency=True,
    )

    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
        child_task_ids=["caller-selected-forged-child"],
    )

    assert decision.transitioned is True
    assert decision.status == "completed"
    assert decision.reason_code == "recovery_children_verified"
    assert decision.child_ids == (fixture.child.id,)
    assert fixture.external.status == "completed"
    assert fixture.source.status == "completed"
    evidence = fixture.source.verification_status[
        "model_recovery_result"
    ]
    assert evidence["status"] == "passed"
    assert evidence["child_ids"] == [fixture.child.id]
    assert evidence["accepted_results"][fixture.child.id][
        "verification_record_id"
    ] == "verification-record-1"
    assert fixture.source.status_reason_details[
        "recovery_source_post_commit"
    ]["state"] == "pending"


def test_finalizer_uses_task_bound_hub_write_authority() -> None:
    from agent.common.recovery_source_finalization_write_boundary import (
        recovery_source_finalization_write_authorized,
    )

    fixture = _build_recovery_fixture()
    authority_checks: list[bool] = []

    class AuthorityCheckingTaskRepository(MemoryRepository):
        def save(self, row: Record) -> Record:
            if (
                row.id == fixture.source.id
                and row.status
                in {"completed", "verification_failed"}
            ):
                authority_checks.append(
                    recovery_source_finalization_write_authorized(
                        row.id
                    )
                )
            return super().save(row)

    fixture.repos.task_repo = AuthorityCheckingTaskRepository(
        list(fixture.repos.task_repo.rows.values())
    )
    service = RecoverySourceFinalizationService(
        repository_provider=lambda: fixture.repos,
        mutation_lock_provider=LockPort,
    )

    decision = service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )

    assert decision.transitioned is True
    assert authority_checks == [True]
    assert (
        recovery_source_finalization_write_authorized(
            fixture.source.id
        )
        is False
    )


def _capture_task_callback(
    monkeypatch: pytest.MonkeyPatch,
    task: Record,
) -> dict[str, Any]:
    import agent.common.context
    from agent.services import task_runtime_service

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        agent.common.context,
        "shutdown_requested",
        False,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "notify_task_update",
        lambda _task_id: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_cancel_recovery_children_for_terminal_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_maybe_finalize_goal",
        lambda _goal_id: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_http_post",
        lambda url, data, headers: calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
            }
        ),
    )
    task_runtime_service._run_task_status_post_commit(
        task=task,
        tid=task.id,
        old_status="in_progress",
        normalized_status=task.status,
        event_type="test_callback_projection",
        force=True,
        synchronous_delivery=True,
    )
    assert len(calls) == 1
    return calls[0]["data"]


@pytest.mark.parametrize(
    ("transport_result", "expected_error"),
    [
        ("timeout", "callback timed out"),
        (None, "recovery_source_callback_no_response"),
        (500, "recovery_source_callback_http_500"),
    ],
)
def test_strict_recovery_callback_rejects_unconfirmed_delivery(
    monkeypatch: pytest.MonkeyPatch,
    transport_result: str | int | None,
    expected_error: str,
) -> None:
    import agent.common.context
    from agent.services import task_runtime_service

    task = Record(
        id="recovery-source",
        status="completed",
        goal_id=None,
        callback_url="https://callback.invalid/task",
        callback_token=None,
        parent_task_id=None,
        current_worker_job_id=None,
        last_output=None,
        last_exit_code=0,
        status_reason_details={
            "model_recovery": {"plan_id": "plan-recovery"},
            "recovery_source_post_commit": {
                "transition_id": "f" * 64,
            },
        },
        verification_status={},
    )
    monkeypatch.setattr(
        agent.common.context,
        "shutdown_requested",
        False,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "notify_task_update",
        lambda _task_id: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_cancel_recovery_children_for_terminal_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_maybe_finalize_goal",
        lambda _goal_id: None,
    )

    def post_callback(
        _url: str,
        **values: Any,
    ) -> Record | None:
        assert values["return_response"] is True
        assert values["idempotency_key"] == "f" * 64
        if transport_result == "timeout":
            raise TimeoutError("callback timed out")
        if transport_result is None:
            return None
        return Record(status_code=transport_result)

    monkeypatch.setattr(
        task_runtime_service,
        "_http_post",
        post_callback,
    )

    with pytest.raises(Exception, match=expected_error):
        task_runtime_service._run_task_status_post_commit(
            task=task,
            tid=task.id,
            old_status="blocked_by_dependency",
            normalized_status="completed",
            event_type="recovery_source_finalized",
            force=True,
            synchronous_delivery=True,
            strict_callback_delivery=True,
        )


def test_strict_recovery_callback_accepts_only_2xx_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.common.context
    from agent.services import task_runtime_service

    task = Record(
        id="recovery-source",
        status="completed",
        goal_id=None,
        callback_url="https://callback.invalid/task",
        callback_token=None,
        parent_task_id=None,
        current_worker_job_id=None,
        last_output=None,
        last_exit_code=0,
        status_reason_details={
            "model_recovery": {"plan_id": "plan-recovery"},
            "recovery_source_post_commit": {
                "transition_id": "f" * 64,
            },
        },
        verification_status={},
    )
    monkeypatch.setattr(
        agent.common.context,
        "shutdown_requested",
        False,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "notify_task_update",
        lambda _task_id: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_cancel_recovery_children_for_terminal_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_maybe_finalize_goal",
        lambda _goal_id: None,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "_http_post",
        lambda *_args, **_kwargs: Record(status_code=204),
    )

    delivery = task_runtime_service._run_task_status_post_commit(
        task=task,
        tid=task.id,
        old_status="blocked_by_dependency",
        normalized_status="completed",
        event_type="recovery_source_finalized",
        force=True,
        synchronous_delivery=True,
        strict_callback_delivery=True,
    )

    assert delivery == RecoverySourceCallbackDelivery(
        delivered=True,
        callback_required=True,
        reason_code="recovery_source_callback_delivered",
        status_code=204,
    )


def _hub_verified_callback_artifact(
    *,
    index: int = 0,
) -> dict[str, Any]:
    identity = f"{index + 1:032x}"
    return {
        "kind": "task_output",
        "task_id": "recovery-child",
        "artifact_id": f"recovery-artifact-{identity}",
        "artifact_version_id": (
            f"recovery-artifact-version-{identity}"
        ),
        "filename": f"result-{index}.txt",
        "media_type": "text/plain",
        "workspace_relative_path": (
            f"reports/result-{index}.txt"
        ),
        "relative_path": f"reports/result-{index}.txt",
        "content_hash": f"{index + 1:064x}",
        "size_bytes": 12,
        "provenance_summary": {
            "schema": "ananta.recovery_artifact_provenance.v1",
            "authority": "hub",
            "ingress": "workspace",
            "worker_url": "http://worker:5000",
            "manifest_digest": "c" * 64,
            "source_index": index,
        },
        "_exists": True,
        "_hash_verified": True,
        "required": True,
    }


def _finalized_source_with_artifacts(
    artifacts: list[dict[str, Any]],
) -> Record:
    fixture = _build_recovery_fixture()
    fixture.child.verification_status[
        "execution_artifacts"
    ] = artifacts
    fixture.child.status_reason_details[
        "recovery_dispatch_lease"
    ]["accepted_result_digest"] = recovery_accepted_result_digest(
        fixture.child
    )
    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )
    assert decision.transitioned is True
    return fixture


def test_finalizer_rejects_multi_child_artifact_aggregate_over_global_bound() -> None:
    fixture = _build_recovery_fixture()
    second_child = Record(
        **{
            key: value
            for key, value in vars(fixture.child).items()
        }
    )
    second_child.id = "recovery-child-2"
    second_child.plan_node_id = "recovery-node-2"
    second_child.title = "Approved recovery task 2"
    second_child.status_reason_details = {
        key: (
            dict(value)
            if isinstance(value, dict)
            else value
        )
        for key, value in fixture.child.status_reason_details.items()
    }
    second_child.verification_status = {
        key: (
            dict(value)
            if isinstance(value, dict)
            else list(value)
            if isinstance(value, list)
            else value
        )
        for key, value in fixture.child.verification_status.items()
    }
    second_node = Record(
        **{
            key: value
            for key, value in vars(fixture.node).items()
        }
    )
    second_node.id = second_child.plan_node_id
    second_node.node_key = "recovery-node-key-2"
    second_node.title = second_child.title
    second_node.position = 1
    second_node.materialized_task_id = second_child.id

    first_receipts = [
        _hub_verified_callback_artifact(index=index)
        for index in range(32)
    ]
    second_receipt = _hub_verified_callback_artifact(index=32)
    second_receipt["task_id"] = second_child.id
    second_receipt["provenance_summary"] = {
        **second_receipt["provenance_summary"],
        "source_index": 0,
    }
    fixture.child.verification_status[
        "execution_artifacts"
    ] = first_receipts
    second_child.verification_status[
        "execution_artifacts"
    ] = [second_receipt]

    child_ids = [fixture.child.id, second_child.id]
    fixture.source.depends_on = child_ids
    fixture.source.status_reason_details["model_recovery"][
        "created_task_ids"
    ] = child_ids
    dependency_binding = build_recovery_dependency_binding(
        source_task_id=fixture.source.id,
        preexisting_dependency_ids=[],
        child_task_ids=child_ids,
    )
    fixture.source.status_reason_details["model_recovery"][
        "dependency_binding"
    ] = dependency_binding
    fixture.plan.rationale[
        "materialization_dependency_binding_digest"
    ] = dependency_binding["digest"]
    fixture.plan.rationale["plan_digest"] = (
        calculate_recovery_plan_digest(
            fixture.plan,
            [fixture.node, second_node],
        )
    )
    second_child.status_reason_details[
        "model_recovery_release"
    ]["task_payload_digest"] = (
        calculate_recovery_task_payload_digest(second_child)
    )
    for child in (fixture.child, second_child):
        child.status_reason_details[
            "recovery_dispatch_lease"
        ]["accepted_result_digest"] = (
            recovery_accepted_result_digest(child)
        )
    fixture.repos.task_repo.rows[second_child.id] = second_child
    fixture.repos.plan_node_repo.rows[
        second_node.id
    ] = second_node

    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )

    assert decision.transitioned is True
    assert decision.status == "verification_failed"
    assert decision.reason_code == (
        "recovery_source_artifact_aggregate_invalid"
    )
    assert fixture.source.status == "verification_failed"
    result = fixture.source.verification_status[
        "model_recovery_result"
    ]
    assert result["artifact_count"] == 0
    assert result["artifacts"] == []
    from agent.services.recovery_source_result_projection import (
        project_recovery_source_callback_artifacts,
    )

    assert project_recovery_source_callback_artifacts(
        fixture.source
    ) == []


def test_callback_projects_hub_aggregated_recovery_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _finalized_source_with_artifacts(
        [_hub_verified_callback_artifact()]
    )
    fixture.source.callback_url = "https://callback.invalid/task"
    fixture.source.callback_token = "callback-secret"
    fixture.source.parent_task_id = "parent-task"
    fixture.source.current_worker_job_id = "worker-job-1"
    # A legacy/foreign projection on the source must not override the
    # Hub-finalized aggregate.
    fixture.source.verification_status[
        "execution_artifacts"
    ] = [{"artifact_id": "foreign-artifact"}]

    payload = _capture_task_callback(
        monkeypatch,
        fixture.source,
    )

    assert payload["status"] == "completed"
    assert payload["artifacts"] == [
        {
            "kind": "task_output",
            "task_id": fixture.child.id,
            "artifact_id": (
                "recovery-artifact-" + f"{1:032x}"
            ),
            "id": "recovery-artifact-" + f"{1:032x}",
            "artifact_version_id": (
                "recovery-artifact-version-" + f"{1:032x}"
            ),
            "filename": "result-0.txt",
            "media_type": "text/plain",
            "workspace_relative_path": "reports/result-0.txt",
            "relative_path": "reports/result-0.txt",
            "path": "reports/result-0.txt",
            "content_hash": f"{1:064x}",
            "size_bytes": 12,
            "provenance_summary": {
                "schema": (
                    "ananta.recovery_artifact_provenance.v1"
                ),
                "authority": "hub",
                "ingress": "workspace",
                "worker_url": "http://worker:5000",
                "manifest_digest": "c" * 64,
                "source_index": 0,
            },
            "_exists": True,
            "_hash_verified": True,
            "required": True,
        }
    ]
    assert payload["status_transition_id"] == payload[
        "idempotency_key"
    ]


def test_callback_keeps_legacy_execution_artifact_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_artifacts = [
        {
            "artifact_id": "legacy-artifact",
            "path": "legacy/output.txt",
            "compatibility_field": "preserved",
        }
    ]
    task = Record(
        id="ordinary-task",
        status="completed",
        goal_id=None,
        callback_url="https://callback.invalid/task",
        callback_token=None,
        parent_task_id=None,
        current_worker_job_id=None,
        last_output="done",
        last_exit_code=0,
        status_reason_details={},
        verification_status={
            "execution_artifacts": legacy_artifacts,
        },
    )

    payload = _capture_task_callback(monkeypatch, task)

    assert payload["artifacts"] == legacy_artifacts


def test_owner_terminal_recovery_source_never_uses_legacy_artifact_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Record(
        id="owner-terminal-source",
        status="cancelled",
        goal_id="goal-recovery",
        callback_url="https://callback.invalid/task",
        callback_token=None,
        parent_task_id=None,
        current_worker_job_id=None,
        last_output=None,
        last_exit_code=1,
        status_reason_details={
            "model_recovery": {
                "plan_id": "plan-recovery",
                "status": "failed",
            },
            "recovery_owner_terminal_invalidation": {
                "schema": (
                    "ananta.recovery_owner_terminal_"
                    "invalidation.v1"
                ),
            },
        },
        verification_status={
            "execution_artifacts": [
                {
                    "artifact_id": (
                        "legacy-or-attacker-controlled"
                    )
                }
            ]
        },
    )

    payload = _capture_task_callback(monkeypatch, task)

    assert payload["status"] == "cancelled"
    assert payload["artifacts"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "prefix_only_id",
        "oversize_size",
        "oversize_filename",
        "oversize_media_type",
        "oversize_worker_url",
        "unknown_field",
    ],
)
def test_recovery_callback_projection_rejects_unclosed_or_oversize_receipts(
    mutation: str,
) -> None:
    from agent.services.recovery_source_result_projection import (
        project_recovery_source_callback_artifacts,
    )
    from ananta_contracts.recovery_artifact_ingress import (
        MAX_RECOVERY_ARTIFACT_BYTES,
    )

    artifact = _hub_verified_callback_artifact()
    if mutation == "prefix_only_id":
        artifact["artifact_id"] = "recovery-artifact-not-a-digest"
    elif mutation == "oversize_size":
        artifact["size_bytes"] = MAX_RECOVERY_ARTIFACT_BYTES + 1
    elif mutation == "oversize_filename":
        artifact["filename"] = "x" * 256
    elif mutation == "oversize_media_type":
        artifact["media_type"] = "x" * 128
    elif mutation == "oversize_worker_url":
        artifact["provenance_summary"]["worker_url"] = (
            "http://worker/" + "x" * 300_000
        )
    else:
        artifact["untrusted_extra"] = "not-in-receipt-contract"
    fixture = _finalized_source_with_artifacts([artifact])

    assert project_recovery_source_callback_artifacts(
        fixture.source
    ) == []


def test_recovery_callback_projection_rejects_aggregate_count_over_bound() -> None:
    from agent.services.recovery_source_result_projection import (
        project_recovery_source_callback_artifacts,
    )
    from ananta_contracts.recovery_artifact_ingress import (
        MAX_RECOVERY_ARTIFACT_COUNT,
    )

    artifacts = [
        _hub_verified_callback_artifact(index=index)
        for index in range(MAX_RECOVERY_ARTIFACT_COUNT + 1)
    ]
    fixture = _finalized_source_with_artifacts(artifacts)

    assert project_recovery_source_callback_artifacts(
        fixture.source
    ) == []


def test_finalizer_rejects_child_set_not_matching_approved_plan_nodes() -> None:
    fixture = _build_recovery_fixture()
    fixture.source.status_reason_details["model_recovery"][
        "created_task_ids"
    ] = [fixture.child.id, "unapproved-child"]

    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )

    assert decision.transitioned is True
    assert decision.status == "verification_failed"
    assert (
        decision.reason_code
        == "recovery_source_child_set_mismatch"
    )
    assert fixture.source.status == "verification_failed"


@pytest.mark.parametrize("mutation", ["added", "removed"])
def test_finalizer_rejects_dependency_mutation_after_materialization(
    mutation: str,
) -> None:
    fixture = _build_recovery_fixture(
        external_dependency=True,
    )
    if mutation == "added":
        fixture.source.depends_on.append("late-dependency")
    else:
        fixture.source.depends_on.remove(fixture.external.id)

    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )

    assert decision.transitioned is True
    assert decision.status == "verification_failed"
    assert (
        decision.reason_code
        == "recovery_source_dependency_binding_mismatch"
    )


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (
            "propose_phase",
            "recovery_child_terminal_result_not_accepted",
        ),
        (
            "missing_result",
            "recovery_child_result_not_accepted",
        ),
        (
            "missing_digest",
            "recovery_child_result_digest_mismatch",
        ),
        (
            "mismatched_digest",
            "recovery_child_result_digest_mismatch",
        ),
    ],
)
def test_finalizer_fails_closed_for_unaccepted_or_unbound_child_results(
    mutation: str,
    reason_code: str,
) -> None:
    fixture = _build_recovery_fixture()
    lease = fixture.child.status_reason_details[
        "recovery_dispatch_lease"
    ]
    if mutation == "propose_phase":
        lease["accepted_result_phase"] = "propose"
    elif mutation == "missing_result":
        lease["state"] = "worker_admitted"
    elif mutation == "missing_digest":
        lease.pop("accepted_result_digest")
    else:
        lease["accepted_result_digest"] = "0" * 64

    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )

    assert decision.transitioned is True
    assert decision.status == "verification_failed"
    assert decision.reason_code == reason_code
    assert fixture.source.status == "verification_failed"


def test_finalizer_routes_failed_child_to_source_verification_failure() -> None:
    fixture = _build_recovery_fixture()
    fixture.child.status = "failed"
    fixture.child.last_exit_code = 1

    decision = fixture.service.finalize_if_ready(
        source_task_id=fixture.source.id,
    )

    assert decision.transitioned is True
    assert decision.status == "verification_failed"
    assert decision.reason_code == "recovery_child_failed"
    assert fixture.source.status == "verification_failed"
    assert fixture.source.verification_status[
        "model_recovery_result"
    ]["quality_gate_reason"] == "child_terminal_failure"


def _post_commit_task(
    *,
    marker_state: str,
    processing_at: float | None = None,
    attempt_count: int = 0,
) -> Record:
    marker = {
        "schema": "ananta.recovery_source_post_commit.v1",
        "state": marker_state,
        "transition_id": "transition-1",
        "old_status": "blocked_by_dependency",
        "attempt_count": attempt_count,
    }
    if processing_at is not None:
        marker["processing_at"] = processing_at
    return Record(
        id="source-post-commit",
        status="completed",
        status_reason_details={
            "model_recovery": {"plan_id": "plan-recovery"},
            "recovery_source_post_commit": marker,
        },
        updated_at=0.0,
    )


@pytest.mark.parametrize(
    ("state", "processing_at", "attempt_count", "expected_attempts"),
    [
        ("pending", None, 0, 1),
        ("processing", 1.0, 2, 3),
    ],
)
def test_source_post_commit_delivers_pending_and_retries_stale_processing(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    processing_at: float | None,
    attempt_count: int,
    expected_attempts: int,
) -> None:
    from agent.services import recovery_source_post_commit_service, task_runtime_service

    task = _post_commit_task(
        marker_state=state,
        processing_at=processing_at,
        attempt_count=attempt_count,
    )
    repo = MemoryRepository([task])
    repos = Record(task_repo=repo)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        recovery_source_post_commit_service.time,
        "time",
        lambda: 100.0,
    )
    def successful_delivery(
        task_id: str,
        **values: Any,
    ) -> RecoverySourceCallbackDelivery:
        calls.append((task_id, values))
        return RecoverySourceCallbackDelivery(
            delivered=True,
            callback_required=False,
            reason_code=(
                "recovery_source_callback_not_configured"
            ),
        )

    monkeypatch.setattr(
        task_runtime_service,
        "run_external_task_status_post_commit",
        successful_delivery,
    )
    service = RecoverySourcePostCommitService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
        retry_after_seconds=30.0,
    )

    decision = service.deliver_if_pending(task.id)

    assert decision.delivered is True
    assert (
        decision.reason_code
        == "recovery_source_post_commit_delivered"
    )
    assert calls == [
        (
            task.id,
            {
                "old_status": "blocked_by_dependency",
                "event_type": "recovery_source_finalized",
                "force": True,
                "synchronous_delivery": True,
                "strict_callback_delivery": True,
            },
        )
    ]
    marker = task.status_reason_details[
        "recovery_source_post_commit"
    ]
    assert marker["state"] == "completed"
    assert marker["attempt_count"] == expected_attempts
    assert marker["completed_at"] == 100.0


def test_source_post_commit_retries_failed_synchronous_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import recovery_source_post_commit_service, task_runtime_service

    task = _post_commit_task(marker_state="pending")
    repo = MemoryRepository([task])
    repos = Record(task_repo=repo)
    observed_values: list[dict[str, Any]] = []

    def fail_delivery(_task_id: str, **values: Any) -> None:
        observed_values.append(values)
        raise RuntimeError("callback unavailable")

    monkeypatch.setattr(
        recovery_source_post_commit_service.time,
        "time",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        task_runtime_service,
        "run_external_task_status_post_commit",
        fail_delivery,
    )
    service = RecoverySourcePostCommitService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
    )

    decision = service.deliver_if_pending(task.id)

    assert decision.delivered is False
    assert (
        decision.reason_code
        == "recovery_source_post_commit_failed"
    )
    assert observed_values[0]["synchronous_delivery"] is True
    marker = task.status_reason_details[
        "recovery_source_post_commit"
    ]
    assert marker["state"] == "pending"
    assert marker["attempt_count"] == 1
    assert marker["last_error"] == "callback unavailable"


@pytest.mark.parametrize("outer_raises", [False, True])
def test_source_post_commit_stale_attempt_cannot_overwrite_newer_success(
    monkeypatch: pytest.MonkeyPatch,
    outer_raises: bool,
) -> None:
    from agent.services import recovery_source_post_commit_service, task_runtime_service

    task = _post_commit_task(marker_state="pending")
    repo = MemoryRepository([task])
    repos = Record(task_repo=repo)
    attempt_ids = iter(("attempt-old", "attempt-new"))
    delivery_count = 0
    nested_decisions: list[Any] = []

    monkeypatch.setattr(
        recovery_source_post_commit_service.time,
        "time",
        lambda: 100.0,
    )
    service = RecoverySourcePostCommitService(
        repository_provider=lambda: repos,
        mutation_lock_provider=LockPort,
        retry_after_seconds=1.0,
        attempt_id_factory=lambda: next(attempt_ids),
    )

    def overlapping_delivery(
        _task_id: str,
        **_values: Any,
    ) -> RecoverySourceCallbackDelivery:
        nonlocal delivery_count
        delivery_count += 1
        if delivery_count != 1:
            return RecoverySourceCallbackDelivery(
                delivered=True,
                callback_required=False,
                reason_code=(
                    "recovery_source_callback_not_configured"
                ),
            )
        marker = task.status_reason_details[
            "recovery_source_post_commit"
        ]
        marker["processing_at"] = 0.0
        nested_decisions.append(
            service.deliver_if_pending(task.id)
        )
        if outer_raises:
            raise RuntimeError("stale attempt failed late")
        return RecoverySourceCallbackDelivery(
            delivered=True,
            callback_required=False,
            reason_code=(
                "recovery_source_callback_not_configured"
            ),
        )

    monkeypatch.setattr(
        task_runtime_service,
        "run_external_task_status_post_commit",
        overlapping_delivery,
    )

    outer_decision = service.deliver_if_pending(task.id)

    assert nested_decisions[0].delivered is True
    assert outer_decision.delivered is False
    assert (
        outer_decision.reason_code
        == "recovery_source_post_commit_superseded"
    )
    marker = task.status_reason_details[
        "recovery_source_post_commit"
    ]
    assert marker["state"] == "completed"
    assert marker["attempt_id"] == "attempt-new"
    assert marker["attempt_count"] == 2
    assert marker["last_error"] is None


def test_source_post_commit_rejects_reused_attempt_id() -> None:
    task = _post_commit_task(
        marker_state="processing",
        processing_at=0.0,
        attempt_count=1,
    )
    task.status_reason_details["recovery_source_post_commit"][
        "attempt_id"
    ] = "attempt-reused"
    repo = MemoryRepository([task])
    service = RecoverySourcePostCommitService(
        repository_provider=lambda: Record(task_repo=repo),
        mutation_lock_provider=LockPort,
        retry_after_seconds=1.0,
        attempt_id_factory=lambda: "attempt-reused",
    )

    decision = service.deliver_if_pending(task.id)

    assert decision.delivered is False
    assert decision.reason_code == (
        "recovery_source_post_commit_attempt_id_invalid"
    )
    marker = task.status_reason_details[
        "recovery_source_post_commit"
    ]
    assert marker["state"] == "processing"
    assert marker["attempt_count"] == 1


class CapturingVerification:
    def __init__(self) -> None:
        self.record_calls: list[dict[str, Any]] = []

    def verify_from_artifacts(self, **values: Any) -> dict[str, Any]:
        return VerificationService().verify_from_artifacts(**values)

    def create_or_update_record(
        self,
        task_id: str,
        **values: Any,
    ) -> Record:
        self.record_calls.append(
            {"task_id": task_id, **values}
        )
        return Record(id="hub-verification-record", status="failed")


def test_result_verifier_rejects_worker_forged_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services import task_runtime_service

    child = Record(
        id="artifact-child",
        derivation_reason="goal_task_recovery",
        assigned_agent_url="http://worker:5000",
        verification_spec={
            "expected_artifacts": [
                {
                    "relative_path": "result.txt",
                    "required": True,
                }
            ]
        },
        expected_artifacts=[],
        verification_status={},
    )
    artifact = Record(
        id="artifact-1",
        latest_version_id="version-1",
        created_by="http://worker:5000",
    )
    version = Record(
        id="version-1",
        artifact_id=artifact.id,
        sha256="a" * 64,
    )
    task_repo = MemoryRepository([child])
    repos = Record(
        task_repo=task_repo,
        artifact_repo=MemoryRepository([artifact]),
        artifact_version_repo=MemoryRepository([version]),
    )
    verification = CapturingVerification()
    status_calls: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        task_runtime_service,
        "update_local_task_status",
        lambda task_id, status, **values: status_calls.append(
            (task_id, status, values)
        ),
    )
    service = RecoveryResultVerificationService(
        repository_provider=lambda: repos,
        verification_service_provider=lambda: verification,
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": "worker claims success",
            "trace": {"trace_id": "trace-artifact"},
        },
        artifacts=[
            {
                "artifact_id": artifact.id,
                "artifact_version_id": version.id,
                "task_id": child.id,
                "relative_path": "result.txt",
                "content_hash": "b" * 64,
                "_exists": True,
                "_hash_verified": True,
            }
        ],
    )

    assert result is not None
    assert result["status"] == "failed"
    assert result["gate_results"]["passed"] is False
    assert result["artifacts"][0]["_exists"] is False
    assert result["artifacts"][0]["_hash_verified"] is False
    assert result["artifacts"][0]["required"] is True
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False
    assert status_calls[0][:2] == (
        child.id,
        "verification_failed",
    )
    assert child.verification_status[
        "execution_artifacts"
    ][0]["_hash_verified"] is False
