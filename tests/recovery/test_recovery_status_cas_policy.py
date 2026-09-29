from __future__ import annotations

import gc
import threading
import time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.common.recovery_child_cancellation_write_boundary import (
    authorize_recovery_child_cancellation_write,
)
from agent.common.recovery_source_approval_rebind_write_boundary import (
    authorize_recovery_source_approval_rebind_write,
)
from agent.db_models import TaskDB
from agent.repositories.tasks import (
    TaskRepository,
    _detached_task_row_copy,
)
from agent.services.recovery_dispatch_gate_service import (
    RecoveryDispatchGateService,
)
from agent.services.task_runtime_service import (
    compare_and_set_local_task_status,
)
from agent.services.task_recovery_planning_service import (
    TaskRecoveryPlanningService,
)
from agent.services.voice_task_terminal_service import (
    VoiceTaskTerminalService,
)


def _engine(tmp_path, name: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={
            "check_same_thread": False,
            "timeout": 10,
        },
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _persist_recovery_pair(
    engine,
    *,
    suffix: str,
    lease: dict | None = None,
) -> tuple[str, str, str, str]:
    source_id = f"cas-source-{suffix}"
    child_id = f"cas-child-{suffix}"
    goal_id = f"cas-goal-{suffix}"
    plan_id = f"cas-plan-{suffix}"
    child_details = {
        "model_recovery_release": {
            "source_task_id": source_id,
            "goal_id": goal_id,
            "plan_id": plan_id,
        }
    }
    if lease is not None:
        child_details["recovery_dispatch_lease"] = dict(lease)
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=source_id,
                goal_id=goal_id,
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "plan_id": plan_id,
                        "status": (
                            "materialized_waiting_for_children"
                        ),
                    }
                },
            )
        )
        session.add(
            TaskDB(
                id=child_id,
                source_task_id=source_id,
                goal_id=goal_id,
                plan_id=plan_id,
                derivation_reason="goal_task_recovery",
                status="todo",
                status_reason_details=child_details,
            )
        )
        session.commit()
    return source_id, child_id, goal_id, plan_id


def _active_lease(
    *,
    child_id: str,
    source_id: str,
    plan_id: str,
) -> dict:
    issued_at = time.time()
    return {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "task_id": child_id,
        "token_digest": "a" * 64,
        "phase": "execute",
        "state": "active",
        "revision": 1,
        "issued_at": issued_at,
        "expires_at": issued_at + 300,
        "worker_url": "http://cas-worker:5000",
        "source_task_id": source_id,
        "plan_id": plan_id,
        "release_epoch": "cas-release-epoch",
        "request_fingerprint": "b" * 64,
    }


def test_generic_cas_cannot_terminalize_recovery_source_or_child(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "terminal-denied.db")
    source_id, child_id, _goal_id, _plan_id = (
        _persist_recovery_pair(engine, suffix="terminal")
    )
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    assert not compare_and_set_local_task_status(
        source_id,
        "completed",
        expected_statuses={"blocked_by_dependency"},
        force=True,
    )
    assert not compare_and_set_local_task_status(
        child_id,
        "cancelled",
        expected_statuses={"todo"},
        status_reason_code="forged_cancel",
        force=True,
    )
    with Session(engine) as session:
        assert session.get(TaskDB, source_id).status == (
            "blocked_by_dependency"
        )
        assert session.get(TaskDB, child_id).status == "todo"


def test_generic_cas_denies_verification_only_recovery_roles(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "verification-only-roles.db")
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="verification-only-source",
                goal_id="verification-only-goal",
                status="blocked_by_dependency",
                verification_status={
                    "model_recovery": {
                        "plan_id": "verification-only-plan",
                    }
                },
            )
        )
        session.add(
            TaskDB(
                id="verification-only-child",
                source_task_id="verification-only-source",
                goal_id="verification-only-goal",
                plan_id="verification-only-plan",
                status="todo",
                verification_status={
                    "model_recovery_release": {
                        "source_task_id": (
                            "verification-only-source"
                        ),
                        "plan_id": "verification-only-plan",
                    }
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    assert not compare_and_set_local_task_status(
        "verification-only-source",
        "completed",
        expected_statuses={"blocked_by_dependency"},
        force=True,
    )
    assert not compare_and_set_local_task_status(
        "verification-only-child",
        "cancelled",
        expected_statuses={"todo"},
        force=True,
    )
    with Session(engine) as session:
        assert session.get(
            TaskDB,
            "verification-only-source",
        ).status == "blocked_by_dependency"
        assert session.get(
            TaskDB,
            "verification-only-child",
        ).status == "todo"


def test_generic_cas_cannot_mutate_recovery_lease_or_result_payload(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "payload-denied.db")
    source_id, child_id, _goal_id, plan_id = (
        _persist_recovery_pair(engine, suffix="payload")
    )
    lease = _active_lease(
        child_id=child_id,
        source_id=source_id,
        plan_id=plan_id,
    )
    with Session(engine) as session:
        child = session.get(TaskDB, child_id)
        child.status_reason_details = {
            **dict(child.status_reason_details or {}),
            "recovery_dispatch_lease": lease,
        }
        session.add(child)
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    admitted = {
        **lease,
        "state": "worker_admitted",
        "admitted_at": time.time(),
        "admitted_worker_url": lease["worker_url"],
    }
    assert not compare_and_set_local_task_status(
        child_id,
        "todo",
        expected_statuses={"todo"},
        status_reason_details={
            "model_recovery_release": {
                "source_task_id": source_id,
                "plan_id": plan_id,
            },
            "recovery_dispatch_lease": admitted,
        },
        force=True,
    )
    assert not compare_and_set_local_task_status(
        child_id,
        "todo",
        expected_statuses={"todo"},
        last_output="forged worker result",
        last_exit_code=0,
        force=True,
    )
    assert not compare_and_set_local_task_status(
        source_id,
        "blocked_by_dependency",
        expected_statuses={"blocked_by_dependency"},
        verification_status={
            "model_recovery_result": {
                "schema": "ananta.recovery_source_result.v2",
                "status": "passed",
            }
        },
        force=True,
    )
    with Session(engine) as session:
        child = session.get(TaskDB, child_id)
        source = session.get(TaskDB, source_id)
        assert child.status_reason_details[
            "recovery_dispatch_lease"
        ]["state"] == "active"
        assert not child.last_output
        assert "model_recovery_result" not in dict(
            source.verification_status or {}
        )


def test_dispatch_gate_child_cancellation_requires_exact_authority(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "child-cancel.db")
    source_id, child_id, goal_id, plan_id = (
        _persist_recovery_pair(engine, suffix="cancel")
    )
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repository = TaskRepository()
    gate = RecoveryDispatchGateService(
        repository_provider=lambda: SimpleNamespace(
            task_repo=repository
        )
    )

    assert gate.invalidate_task(
        child_id,
        reason_code="recovery_parent_terminal",
    )
    cancelled = repository.get_by_id(child_id)
    marker = cancelled.status_reason_details[
        "recovery_child_cancellation"
    ]
    assert cancelled.status == "cancelled"
    assert marker["task_id"] == child_id
    assert marker["source_task_id"] == source_id
    assert marker["reason_code"] == "recovery_parent_terminal"

    _, foreign_child_id, _, _ = _persist_recovery_pair(
        engine,
        suffix="foreign",
    )
    authorized_marker = {
        "schema": "ananta.recovery_child_cancellation.v1",
        "task_id": foreign_child_id,
        "source_task_id": "cas-source-foreign",
        "goal_id": "cas-goal-foreign",
        "plan_id": "cas-plan-foreign",
        "previous_status": "todo",
        "target_status": "cancelled",
        "reason_code": "recovery_parent_terminal",
        "cancelled_at": time.time(),
    }
    forged_marker = {
        **authorized_marker,
        "reason_code": "forged_reason",
    }
    with authorize_recovery_child_cancellation_write(
        task_id=foreign_child_id,
        marker=authorized_marker,
    ):
        assert not compare_and_set_local_task_status(
            foreign_child_id,
            "cancelled",
            expected_statuses={"todo"},
            status_reason_code="forged_reason",
            status_reason_details={
                "model_recovery_release": {
                    "source_task_id": "cas-source-foreign",
                    "goal_id": goal_id,
                    "plan_id": plan_id,
                },
                "recovery_child_cancellation": forged_marker,
            },
            force=True,
        )
    assert repository.get_by_id(foreign_child_id).status == "todo"


@pytest.mark.parametrize(
    "smuggle_case",
    (
        "assigned_agent_url",
        "manual_override_until",
        "unknown_field",
        "unrelated_status_reason_detail",
        "caller_updated_at",
        "caller_history",
    ),
)
def test_exact_child_cancellation_authority_rejects_smuggled_delta(
    monkeypatch,
    tmp_path,
    smuggle_case: str,
) -> None:
    engine = _engine(
        tmp_path,
        f"child-cancel-smuggle-{smuggle_case}.db",
    )
    source_id, child_id, goal_id, plan_id = (
        _persist_recovery_pair(
            engine,
            suffix=f"smuggle-{smuggle_case}",
        )
    )
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repository = TaskRepository()
    child = repository.get_by_id(child_id)
    marker = {
        "schema": "ananta.recovery_child_cancellation.v1",
        "task_id": child_id,
        "source_task_id": source_id,
        "goal_id": goal_id,
        "plan_id": plan_id,
        "previous_status": "todo",
        "target_status": "cancelled",
        "reason_code": "recovery_parent_terminal",
        "cancelled_at": time.time(),
    }
    details = dict(child.status_reason_details or {})
    details["recovery_child_cancellation"] = marker
    values = {
        "status_reason_code": "recovery_parent_terminal",
        "status_reason_details": details,
    }
    if smuggle_case == "assigned_agent_url":
        values["assigned_agent_url"] = "http://attacker"
    elif smuggle_case == "manual_override_until":
        values["manual_override_until"] = 9_999_999_999
    elif smuggle_case == "unknown_field":
        values["attacker_controlled_unknown_field"] = True
    elif smuggle_case == "caller_updated_at":
        values["updated_at"] = 9_999_999_999
    elif smuggle_case == "caller_history":
        values["history"] = [
            {
                "event_type": "attacker_controlled",
            }
        ]
    else:
        details["unrelated_metadata"] = {
            "attacker_controlled": True
        }

    with authorize_recovery_child_cancellation_write(
        task_id=child_id,
        marker=marker,
    ):
        assert not compare_and_set_local_task_status(
            child_id,
            "cancelled",
            expected_statuses={"todo"},
            event_type="recovery_dispatch_gate_invalidated",
            event_actor="hub_dispatch_gate",
            event_details={
                "reason_code": "recovery_parent_terminal"
            },
            force=True,
            **values,
        )

    persisted = repository.get_by_id(child_id)
    assert persisted.status == "todo"
    assert persisted.assigned_agent_url is None
    assert persisted.manual_override_until is None
    assert "recovery_child_cancellation" not in dict(
        persisted.status_reason_details or {}
    )
    assert "unrelated_metadata" not in dict(
        persisted.status_reason_details or {}
    )


def test_repository_status_cas_has_exactly_one_concurrent_winner(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "concurrent-cas.db")
    with Session(engine) as session:
        session.add(TaskDB(id="ordinary-cas-race", status="todo"))
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, bool]] = []

    def _claim(worker_url: str) -> None:
        barrier.wait(timeout=5)
        changed = compare_and_set_local_task_status(
            "ordinary-cas-race",
            "assigned",
            expected_statuses={"todo"},
            assigned_agent_url=worker_url,
            force=True,
        )
        outcomes.append((worker_url, changed))

    threads = [
        threading.Thread(
            target=_claim,
            args=(f"http://worker-{index}:5000",),
        )
        for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert sorted(changed for _worker, changed in outcomes) == [
        False,
        True,
    ]
    winner = next(
        worker for worker, changed in outcomes if changed
    )
    with Session(engine) as session:
        persisted = session.get(TaskDB, "ordinary-cas-race")
        assert persisted.status == "assigned"
        assert persisted.assigned_agent_url == winner


def test_repository_status_cas_preserves_legacy_nullable_list_fields(
    monkeypatch,
    tmp_path,
) -> None:
    authoritative = TaskDB(
        id="detached-copy-state-check",
        status="todo",
    )
    authoritative.depends_on = None
    candidate = _detached_task_row_copy(authoritative)
    assert candidate._sa_instance_state is not (
        authoritative._sa_instance_state
    )
    del authoritative
    gc.collect()
    candidate.status = "assigned"
    assert candidate.depends_on is None

    engine = _engine(tmp_path, "legacy-null-list-cas.db")
    with Session(engine) as session:
        task = TaskDB(id="legacy-null-list-cas", status="todo")
        task.depends_on = None
        session.add(task)
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    assert compare_and_set_local_task_status(
        "legacy-null-list-cas",
        "assigned",
        expected_statuses={"todo"},
        assigned_agent_url="http://legacy-compatible-worker",
        event_type="task_claimed",
        event_actor="http://legacy-compatible-worker",
        event_details={
            "agent_url": "http://legacy-compatible-worker",
        },
    )
    with Session(engine) as session:
        persisted = session.get(
            TaskDB,
            "legacy-null-list-cas",
        )
        assert persisted.status == "assigned"
        assert persisted.depends_on is None


def test_source_approval_rebind_is_exact_task_bound_and_closed(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "source-approval-rebind.db")
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repository = TaskRepository()

    def _state(
        *,
        approval_id: str,
        plan_id: str = "rebind-plan",
        team_id: str = "rebind-team",
        status: str = "pending_approval",
    ) -> dict:
        return {
            "schema": "ananta.task_recovery_state.v1",
            "status": status,
            "plan_id": plan_id,
            "approval_request_id": approval_id,
            "recovery_key": "rebind-key",
            "recovery_depth": 1,
            "node_count": 2,
            "team_id": team_id,
        }

    def _persist_source(suffix: str) -> tuple[str, dict]:
        task_id = f"approval-rebind-{suffix}"
        current = _state(
            approval_id=f"old-approval-{suffix}"
        )
        with Session(engine) as session:
            session.add(
                TaskDB(
                    id=task_id,
                    goal_id="rebind-goal",
                    goal_trace_id="rebind-trace",
                    status="waiting_for_review",
                    status_reason_code=(
                        "model_recovery_plan_pending_approval"
                    ),
                    status_reason_details={
                        "preserved": {"value": suffix},
                        "model_recovery": current,
                    },
                    verification_status={
                        "preserved": {"value": suffix},
                        "model_recovery": current,
                    },
                )
            )
            session.commit()
        return task_id, current

    def _attempt(
        *,
        task_id: str,
        proposed: dict,
        assigned_agent_url: str | None = None,
    ) -> bool:
        current_task = repository.get_by_id(task_id)
        values = {
            "verification_status": {
                **dict(
                    current_task.verification_status or {}
                ),
                "model_recovery": proposed,
            },
            "status_reason_code": (
                "model_recovery_plan_pending_approval"
            ),
            "status_reason_details": {
                **dict(
                    current_task.status_reason_details or {}
                ),
                "model_recovery": proposed,
            },
        }
        if assigned_agent_url is not None:
            values["assigned_agent_url"] = assigned_agent_url
        return compare_and_set_local_task_status(
            task_id,
            "waiting_for_review",
            expected_statuses={"waiting_for_review"},
            event_type=(
                "task_recovery_plan_pending_approval"
            ),
            event_actor="hub_recovery_planner",
            event_details={
                "plan_id": proposed["plan_id"],
                "approval_request_id": (
                    proposed["approval_request_id"]
                ),
            },
            **values,
        )

    task_id, current = _persist_source("positive")
    service = TaskRecoveryPlanningService(
        role_provider=lambda: "hub"
    )
    assert service._mark_source_waiting_for_approval(
        source_task=repository.get_by_id(task_id),
        plan_id=current["plan_id"],
        approval_request_id="new-approval-positive",
        recovery_key=current["recovery_key"],
        node_count=current["node_count"],
        team_id=current["team_id"],
    )
    rebound = repository.get_by_id(task_id)
    rebound_state = rebound.status_reason_details[
        "model_recovery"
    ]
    assert rebound_state["approval_request_id"] == (
        "new-approval-positive"
    )
    assert rebound.verification_status["model_recovery"] == (
        rebound_state
    )
    assert rebound.status_reason_details["preserved"] == {
        "value": "positive"
    }
    assert rebound.history[-1]["event_type"] == (
        "task_recovery_plan_pending_approval"
    )

    for case in (
        "detached",
        "wrong_task",
        "wrong_plan",
        "wrong_team",
        "wrong_state",
        "smuggled_assignment",
    ):
        case_task_id, case_current = _persist_source(case)
        valid_proposed = {
            **case_current,
            "approval_request_id": f"new-approval-{case}",
        }
        candidate = dict(valid_proposed)
        authority_task_id = case_task_id
        smuggled_assignment = None
        if case == "wrong_task":
            authority_task_id = "foreign-rebind-task"
        elif case == "wrong_plan":
            candidate["plan_id"] = "wrong-plan"
        elif case == "wrong_team":
            candidate["team_id"] = "wrong-team"
        elif case == "wrong_state":
            candidate["status"] = "denied"
        elif case == "smuggled_assignment":
            smuggled_assignment = "http://attacker"

        if case == "detached":
            with authorize_recovery_source_approval_rebind_write(
                task_id=case_task_id,
                current_state=case_current,
                proposed_state=valid_proposed,
            ):
                pass
            changed = _attempt(
                task_id=case_task_id,
                proposed=candidate,
            )
        else:
            with authorize_recovery_source_approval_rebind_write(
                task_id=authority_task_id,
                current_state=case_current,
                proposed_state=valid_proposed,
            ):
                changed = _attempt(
                    task_id=case_task_id,
                    proposed=candidate,
                    assigned_agent_url=(
                        smuggled_assignment
                    ),
                )
        assert not changed, case
        unchanged = repository.get_by_id(case_task_id)
        assert unchanged.status_reason_details[
            "model_recovery"
        ] == case_current
        assert unchanged.verification_status[
            "model_recovery"
        ] == case_current
        assert unchanged.assigned_agent_url is None


def test_voice_terminal_cas_stays_positive_for_owned_non_recovery_task(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "voice-cas.db")
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="ordinary-voice-task",
                task_kind="voice_live_run",
                status="in_progress",
            )
        )
        session.add(
            TaskDB(
                id="voice-kind-race",
                task_kind="voice_live_run",
                status="in_progress",
            )
        )
        session.add(
            TaskDB(
                id="recovery-voice-source",
                goal_id="recovery-voice-goal",
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "plan_id": "recovery-voice-plan",
                    }
                },
            )
        )
        session.add(
            TaskDB(
                id="recovery-voice-child",
                task_kind="voice_live_run",
                source_task_id="recovery-voice-source",
                goal_id="recovery-voice-goal",
                plan_id="recovery-voice-plan",
                derivation_reason="goal_task_recovery",
                status="in_progress",
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "agent.services.voice_task_terminal_service.engine",
        engine,
    )
    service = VoiceTaskTerminalService()

    assert service.update_existing(
        "ordinary-voice-task",
        "completed",
        event_type="voice_completed",
        last_output="owned voice result",
    )
    assert not service.update_existing(
        "recovery-voice-child",
        "completed",
        event_type="voice_completed",
        last_output="unaccepted Recovery result",
    )

    def _change_kind_before_cas(*args, **kwargs):
        with Session(engine) as session:
            raced = session.get(TaskDB, "voice-kind-race")
            raced.task_kind = "coding"
            session.add(raced)
            session.commit()
        return compare_and_set_local_task_status(*args, **kwargs)

    monkeypatch.setattr(
        (
            "agent.services.task_runtime_service."
            "compare_and_set_local_task_status"
        ),
        _change_kind_before_cas,
    )
    assert not service.update_existing(
        "voice-kind-race",
        "completed",
        event_type="voice_completed",
        last_output="must not cross ownership race",
    )
    with Session(engine) as session:
        ordinary = session.get(TaskDB, "ordinary-voice-task")
        recovery = session.get(TaskDB, "recovery-voice-child")
        raced = session.get(TaskDB, "voice-kind-race")
        assert ordinary.status == "completed"
        assert ordinary.last_output == "owned voice result"
        assert recovery.status == "in_progress"
        assert not recovery.last_output
        assert raced.task_kind == "coding"
        assert raced.status == "in_progress"
        assert not raced.last_output
