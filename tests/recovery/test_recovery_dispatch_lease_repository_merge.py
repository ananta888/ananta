from __future__ import annotations

import contextlib
import threading
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.common.recovery_dependency_reconciliation_write_boundary import (
    authorize_recovery_dependency_reconciliation_write,
    recovery_dependency_reconciliation_write_authorized,
)
from agent.common.recovery_dispatch_invalidation_write_boundary import (
    authorize_recovery_dispatch_invalidation_write,
)
from agent.common.recovery_owner_terminal_write_boundary import (
    authorize_recovery_owner_terminal_write,
)
from agent.common.recovery_result_commit_write_boundary import (
    authorize_recovery_result_commit_write,
)
from agent.common.recovery_source_finalization_write_boundary import (
    authorize_recovery_source_finalization_write,
)
from agent.common.recovery_source_post_commit_write_boundary import (
    authorize_recovery_source_post_commit_write,
)
from agent.db_models import TaskDB
from agent.repositories.tasks import (
    TaskRepository,
    _merge_dispatch_lease,
    _merge_recovery_source_post_commit,
)
from agent.services.recovery_dispatch_gate_service import (
    RecoveryDispatchGateService,
    recovery_accepted_result_digest,
)


def _lease(
    *,
    revision: int = 1,
    state: str = "active",
    expires_at: float | None = None,
    phase: str = "propose",
    token: str = "a",
    worker_url: str = "http://worker-a:5000",
    request: str = "b",
) -> dict:
    issued_at = time.time() - 10
    return {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "task_id": "recovery-child",
        "token_digest": token * 64,
        "phase": phase,
        "state": state,
        "revision": revision,
        "issued_at": issued_at,
        "expires_at": (
            expires_at
            if expires_at is not None
            else time.time() + 300
        ),
        "worker_url": worker_url,
        "source_task_id": "recovery-source",
        "goal_id": "recovery-goal",
        "plan_id": "recovery-plan",
        "team_id": "recovery-team",
        "release_epoch": "recovery-release",
        "request_fingerprint": request * 64,
    }


def _dependency_reconciliation_marker(
    *,
    task_id: str = "recovery-child",
    source_task_id: str = "recovery-source",
    dependency_id: str = "failed-dependency",
    reconciled_at: float | None = None,
) -> dict:
    return {
        "schema": (
            "ananta.recovery_dependency_reconciliation.v1"
        ),
        "task_id": task_id,
        "source_task_id": source_task_id,
        "previous_status": "blocked_by_dependency",
        "target_status": "failed",
        "reason_code": "recovery_dependency_terminal",
        "dependency_statuses": [
            {
                "task_id": dependency_id,
                "status": "failed",
            }
        ],
        "failed_dependency_ids": [dependency_id],
        "reconciled_at": (
            time.time()
            if reconciled_at is None
            else reconciled_at
        ),
    }


def test_unexpired_capability_rejects_higher_revision_replacement() -> None:
    current = _lease(phase="execute")
    replacement = _lease(
        revision=2,
        phase="execute",
        token="c",
        worker_url="http://worker-b:5000",
        request="d",
    )

    assert _merge_dispatch_lease(
        current,
        replacement,
        task_id="recovery-child",
    ) == current

    forged_lineage = {
        **replacement,
        "source_task_id": "attacker-source",
        "plan_id": "attacker-plan",
        "release_epoch": "attacker-release",
    }
    assert _merge_dispatch_lease(
        current,
        forged_lineage,
        task_id="recovery-child",
    ) == current


def test_first_lease_requires_canonical_active_hub_capability() -> None:
    active = _lease()
    assert _merge_dispatch_lease(
        {},
        active,
        task_id="recovery-child",
    ) == active

    forged_accepted = {
        **active,
        "state": "result_accepted",
        "accepted_result_terminal": True,
        "accepted_result_digest": "e" * 64,
    }
    assert _merge_dispatch_lease(
        {},
        forged_accepted,
        task_id="recovery-child",
    ) == {}
    assert _merge_dispatch_lease(
        {},
        {**active, "task_id": "foreign-task"},
        task_id="recovery-child",
    ) == {}
    assert _merge_dispatch_lease(
        {},
        {**active, "token_digest": "not-a-digest"},
        task_id="recovery-child",
    ) == {}


def test_expired_capability_allows_one_new_bound_active_epoch() -> None:
    current = _lease(expires_at=time.time() - 1)
    replacement = _lease(
        revision=2,
        phase="execute",
        token="c",
        worker_url="http://worker-b:5000",
        request="d",
    )
    replacement["admitted_at"] = time.time()
    replacement["accepted_result_digest"] = "forged"

    merged = _merge_dispatch_lease(
        current,
        replacement,
        task_id="recovery-child",
    )

    assert merged["revision"] == 2
    assert merged["state"] == "active"
    assert merged["phase"] == "execute"
    assert merged["token_digest"] == "c" * 64
    assert merged["worker_url"] == "http://worker-b:5000"
    assert merged["request_fingerprint"] == "d" * 64
    assert merged["source_task_id"] == "recovery-source"
    assert merged["plan_id"] == "recovery-plan"
    assert merged["release_epoch"] == "recovery-release"
    assert "admitted_at" not in merged
    assert "accepted_result_digest" not in merged


def test_only_exact_lifecycle_transitions_can_advance_a_lease() -> None:
    active = _lease()
    admitted_at = time.time()
    admitted = {
        **active,
        "state": "worker_admitted",
        "admitted_at": admitted_at,
        "admitted_worker_url": active["worker_url"],
    }
    merged_admitted = _merge_dispatch_lease(
        active,
        admitted,
        task_id="recovery-child",
    )
    assert merged_admitted["state"] == "worker_admitted"
    assert merged_admitted["admitted_at"] == admitted_at

    accepted = {
        **merged_admitted,
        "state": "result_accepted",
        "accepted_at": time.time(),
        "accepted_result_phase": "propose",
        "accepted_result_status": "in_progress",
        "accepted_result_terminal": False,
    }
    merged_accepted = _merge_dispatch_lease(
        merged_admitted,
        accepted,
        task_id="recovery-child",
    )
    assert merged_accepted["state"] == "result_accepted"

    shortcut = {
        **active,
        "revision": 2,
        "state": "result_accepted",
        "accepted_at": time.time(),
        "accepted_result_phase": "propose",
        "accepted_result_status": "completed",
        "accepted_result_terminal": True,
    }
    assert _merge_dispatch_lease(
        active,
        shortcut,
        task_id="recovery-child",
    ) == active
    assert _merge_dispatch_lease(
        active,
        {**active, "revision": 3, "state": "revoked"},
        task_id="recovery-child",
    ) == active


def test_next_revision_invalidation_retains_capability_authority() -> None:
    current = {
        **_lease(state="worker_admitted"),
        "admitted_at": time.time() - 5,
        "admitted_worker_url": "http://worker-a:5000",
    }
    forged = {
        **current,
        "revision": 2,
        "state": "revoked",
        "token_digest": "c" * 64,
        "revoked_at": time.time(),
        "revocation_reason": "timeout",
    }
    assert _merge_dispatch_lease(
        current,
        forged,
        task_id="recovery-child",
    ) == current

    revoked = {
        **current,
        "revision": 2,
        "state": "revoked",
        "revoked_at": time.time(),
        "revocation_reason": "timeout",
        "accepted_result_digest": "forged",
    }
    assert _merge_dispatch_lease(
        current,
        revoked,
        task_id="recovery-child",
    ) == current
    with authorize_recovery_dispatch_invalidation_write(
        task_id="recovery-child",
        current_lease=current,
        proposed_lease=revoked,
    ):
        merged = _merge_dispatch_lease(
            current,
            revoked,
            task_id="recovery-child",
        )
    assert merged["state"] == "revoked"
    assert merged["revision"] == 2
    assert merged["token_digest"] == current["token_digest"]
    assert merged["revocation_reason"] == "timeout"
    assert "accepted_result_digest" not in merged

    cancelled = {
        **current,
        "revision": 2,
        "state": "cancelled",
        "cancelled_at": time.time(),
        "cancellation_reason": "owner_cancelled",
    }
    assert _merge_dispatch_lease(
        current,
        cancelled,
        task_id="recovery-child",
    ) == current
    with authorize_recovery_dispatch_invalidation_write(
        task_id="recovery-child",
        current_lease=current,
        proposed_lease=cancelled,
    ):
        merged_cancelled = _merge_dispatch_lease(
            current,
            cancelled,
            task_id="recovery-child",
        )
    assert merged_cancelled["state"] == "cancelled"
    assert merged_cancelled["revision"] == 2
    assert (
        merged_cancelled["cancellation_reason"]
        == "owner_cancelled"
    )

    non_finite = {
        **revoked,
        "revoked_at": float("nan"),
    }
    with pytest.raises(
        ValueError,
        match=(
            "recovery_dispatch_invalidation_authority_invalid"
        ),
    ):
        with authorize_recovery_dispatch_invalidation_write(
            task_id="recovery-child",
            current_lease=current,
            proposed_lease=non_finite,
        ):
            pass


def test_terminal_acceptance_is_sticky_and_requires_complete_proof() -> None:
    active = _lease(phase="execute")
    admitted = {
        **active,
        "state": "worker_admitted",
        "admitted_at": time.time() - 2,
        "admitted_worker_url": active["worker_url"],
    }
    incomplete = {
        **admitted,
        "state": "result_accepted",
        "accepted_at": time.time(),
        "accepted_result_phase": "execute",
        "accepted_result_status": "completed",
        "accepted_result_terminal": False,
    }
    assert _merge_dispatch_lease(
        admitted,
        incomplete,
        task_id="recovery-child",
    ) == admitted

    accepted = {
        **incomplete,
        "accepted_result_terminal": True,
        "accepted_result_digest": "e" * 64,
    }
    assert _merge_dispatch_lease(
        admitted,
        accepted,
        task_id="recovery-child",
    ) == admitted
    with authorize_recovery_result_commit_write(
        task_id="recovery-child",
        lease=accepted,
    ):
        merged = _merge_dispatch_lease(
            admitted,
            accepted,
            task_id="recovery-child",
        )
    assert merged["state"] == "result_accepted"

    late_revoke = {
        **merged,
        "state": "revoked",
        "revision": 2,
        "revoked_at": time.time(),
        "revocation_reason": "late_transport_timeout",
    }
    assert _merge_dispatch_lease(
        merged,
        late_revoke,
        task_id="recovery-child",
    ) == merged


def test_dependency_reconciliation_authority_is_exactly_bound() -> None:
    marker = _dependency_reconciliation_marker()
    foreign_dependency_marker = (
        _dependency_reconciliation_marker(
            dependency_id="foreign-dependency",
            reconciled_at=marker["reconciled_at"],
        )
    )

    with authorize_recovery_dependency_reconciliation_write(
        task_id="recovery-child",
        marker=marker,
    ):
        assert (
            recovery_dependency_reconciliation_write_authorized(
                task_id="recovery-child",
                marker=marker,
            )
            is True
        )
        assert (
            recovery_dependency_reconciliation_write_authorized(
                task_id="foreign-child",
                marker={
                    **marker,
                    "task_id": "foreign-child",
                },
            )
            is False
        )
        assert (
            recovery_dependency_reconciliation_write_authorized(
                task_id="recovery-child",
                marker=foreign_dependency_marker,
            )
            is False
        )

    with pytest.raises(
        ValueError,
        match=(
            "recovery_dependency_reconciliation_authority_invalid"
        ),
    ):
        with authorize_recovery_dependency_reconciliation_write(
            task_id="recovery-child",
            marker={
                **marker,
                "reconciled_at": float("nan"),
            },
        ):
            pass


def test_source_post_commit_marker_allows_only_owned_delivery_progression() -> None:
    pending = {
        "schema": "ananta.recovery_source_post_commit.v1",
        "state": "pending",
        "transition_status": "completed",
        "transition_reason": "recovery_children_verified",
        "transition_id": "f" * 64,
        "old_status": "blocked_by_dependency",
        "created_at": 1.0,
    }
    forged_shortcut = {
        **pending,
        "state": "completed",
        "completed_at": 2.0,
        "last_error": None,
    }
    assert _merge_recovery_source_post_commit(
        pending,
        forged_shortcut,
    ) == pending

    processing = {
        **pending,
        "state": "processing",
        "processing_at": 2.0,
        "attempt_id": "attempt-1",
        "attempt_count": 1,
    }
    assert _merge_recovery_source_post_commit(
        pending,
        processing,
        task_id="recovery-source",
    ) == pending
    with authorize_recovery_source_post_commit_write(
        task_id="recovery-source",
        current=pending,
        proposed=processing,
    ):
        assert _merge_recovery_source_post_commit(
            pending,
            processing,
            task_id="recovery-source",
        ) == processing

    failed = {
        **processing,
        "state": "pending",
        "last_error": "callback unavailable",
        "failed_at": 3.0,
    }
    with authorize_recovery_source_post_commit_write(
        task_id="recovery-source",
        current=processing,
        proposed=failed,
    ):
        assert _merge_recovery_source_post_commit(
            processing,
            failed,
            task_id="recovery-source",
        ) == failed

    retried = {
        **failed,
        "state": "processing",
        "processing_at": 4.0,
        "attempt_id": "attempt-2",
        "attempt_count": 2,
    }
    with authorize_recovery_source_post_commit_write(
        task_id="recovery-source",
        current=failed,
        proposed=retried,
    ):
        assert _merge_recovery_source_post_commit(
            failed,
            retried,
            task_id="recovery-source",
        ) == retried

    completed = {
        **retried,
        "state": "completed",
        "completed_at": 5.0,
        "last_error": None,
    }
    with authorize_recovery_source_post_commit_write(
        task_id="recovery-source",
        current=retried,
        proposed=completed,
    ):
        assert _merge_recovery_source_post_commit(
            retried,
            completed,
            task_id="recovery-source",
        ) == completed

    foreign_attempt = {
        **completed,
        "attempt_id": "foreign-attempt",
    }
    with authorize_recovery_source_post_commit_write(
        task_id="recovery-source",
        current=retried,
        proposed=completed,
    ):
        assert _merge_recovery_source_post_commit(
            retried,
            foreign_attempt,
            task_id="recovery-source",
        ) == retried

    foreign_failure = {
        **retried,
        "state": "pending",
        "attempt_id": "foreign-attempt",
        "last_error": "forged failure",
        "failed_at": 6.0,
    }
    with authorize_recovery_source_post_commit_write(
        task_id="recovery-source",
        current=retried,
        proposed={
            **retried,
            "state": "pending",
            "last_error": "real failure",
            "failed_at": 6.0,
        },
    ):
        assert _merge_recovery_source_post_commit(
            retried,
            foreign_failure,
            task_id="recovery-source",
        ) == retried


def test_concurrent_detached_saves_cannot_replace_unexpired_lease(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery-lease.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    current = _lease(phase="execute")
    release = {
        "source_task_id": current["source_task_id"],
        "goal_id": current["goal_id"],
        "plan_id": current["plan_id"],
        "team_id": current["team_id"],
        "release_epoch": current["release_epoch"],
    }
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=current["source_task_id"],
                goal_id=current["goal_id"],
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "plan_id": current["plan_id"],
                    }
                },
            )
        )
        session.add(
            TaskDB(
                id=current["task_id"],
                goal_id=current["goal_id"],
                plan_id=current["plan_id"],
                team_id=current["team_id"],
                source_task_id=current["source_task_id"],
                derivation_reason="goal_task_recovery",
                status="todo",
                status_reason_details={
                    "model_recovery_release": release,
                    "recovery_dispatch_lease": current,
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repository_lock = threading.RLock()

    class RepositoryLockPort:
        @contextlib.contextmanager
        def mutation_locks(self, _task_ids):
            with repository_lock:
                yield True

    monkeypatch.setattr(
        (
            "agent.common.task_mutation_lock."
            "get_task_mutation_lock_port"
        ),
        lambda: RepositoryLockPort(),
    )
    repository = TaskRepository()
    benign = repository.get_by_id(current["task_id"])
    forged = repository.get_by_id(current["task_id"])
    common_updated_at = time.time() + 10
    benign.updated_at = common_updated_at
    benign.last_output = "benign detached update"
    forged.updated_at = common_updated_at
    forged_details = deepcopy(forged.status_reason_details)
    forged_details["recovery_dispatch_lease"] = {
        **current,
        "revision": 2,
        "phase": "execute",
        "token_digest": "c" * 64,
        "worker_url": "http://worker-b:5000",
        "request_fingerprint": "d" * 64,
    }
    forged.status_reason_details = forged_details

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def save_detached(task: TaskDB) -> None:
        try:
            barrier.wait(timeout=5)
            repository.save(task)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=save_detached, args=(benign,)),
        threading.Thread(target=save_detached, args=(forged,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    persisted = repository.get_by_id(current["task_id"])
    assert persisted.status_reason_details[
        "recovery_dispatch_lease"
    ] == current

    admitted = repository.get_by_id(current["task_id"])
    admitted_details = deepcopy(
        admitted.status_reason_details
    )
    admitted_details["recovery_dispatch_lease"] = {
        **current,
        "state": "worker_admitted",
        "admitted_at": time.time(),
        "admitted_worker_url": current["worker_url"],
    }
    admitted.status_reason_details = admitted_details
    admitted.updated_at = common_updated_at + 10
    repository.save(admitted)

    unproven_terminal = repository.get_by_id(
        current["task_id"]
    )
    unproven_terminal.status = "completed"
    unproven_terminal.updated_at = common_updated_at + 15
    with pytest.raises(
        ValueError,
        match=(
            "recovery_result_commit_write_authority_required"
        ),
    ):
        repository.save(unproven_terminal)

    retained = repository.get_by_id(current["task_id"])
    assert retained.status == "todo"
    assert retained.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "worker_admitted"

    forged_acceptance = repository.get_by_id(
        current["task_id"]
    )
    forged_details = deepcopy(
        forged_acceptance.status_reason_details
    )
    forged_details["recovery_dispatch_lease"] = {
        **forged_details["recovery_dispatch_lease"],
        "state": "result_accepted",
        "accepted_at": time.time(),
        "accepted_result_phase": "execute",
        "accepted_result_status": "completed",
        "accepted_result_terminal": True,
        "accepted_result_digest": "e" * 64,
    }
    forged_acceptance.status = "completed"
    forged_acceptance.status_reason_details = forged_details
    forged_acceptance.updated_at = common_updated_at + 20
    with pytest.raises(
        ValueError,
        match=(
            "recovery_result_commit_write_authority_required"
        ),
    ):
        repository.save(forged_acceptance)

    retained = repository.get_by_id(current["task_id"])
    assert retained.status == "todo"
    assert retained.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "worker_admitted"

    genuine = repository.get_by_id(current["task_id"])
    genuine.status = "completed"
    genuine.last_output = "Hub-verified output"
    genuine.last_exit_code = 0
    genuine.verification_status = {
        "status": "passed",
        "record_id": "verification-record",
        "results": {"final_passed": True},
    }
    genuine_details = deepcopy(genuine.status_reason_details)
    genuine_lease = {
        **genuine_details["recovery_dispatch_lease"],
        "state": "result_accepted",
        "accepted_at": time.time(),
        "accepted_result_phase": "execute",
        "accepted_result_status": "completed",
        "accepted_result_terminal": True,
    }
    genuine_details["recovery_dispatch_lease"] = genuine_lease
    genuine.status_reason_details = genuine_details
    genuine_lease["accepted_result_digest"] = (
        recovery_accepted_result_digest(genuine)
    )
    genuine.updated_at = common_updated_at + 30
    with authorize_recovery_result_commit_write(
        task_id=genuine.id,
        lease=genuine_lease,
    ):
        accepted = repository.save(genuine)
    assert accepted.status == "completed"
    assert accepted.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "result_accepted"
    assert accepted.status_reason_details[
        "recovery_dispatch_lease"
    ]["accepted_result_digest"] == (
        recovery_accepted_result_digest(accepted)
    )
    accepted_output = accepted.last_output
    accepted_exit_code = accepted.last_exit_code
    accepted_verification = deepcopy(
        accepted.verification_status
    )
    mutations = (
        ("last_output", "detached overwrite"),
        ("last_exit_code", 99),
        (
            "verification_status",
            {
                **accepted_verification,
                "status": "failed",
                "foreign": True,
            },
        ),
    )
    for index, (field, value) in enumerate(mutations, start=1):
        detached_result = repository.get_by_id(
            current["task_id"]
        )
        setattr(detached_result, field, value)
        detached_result.updated_at = (
            common_updated_at + 30 + index
        )
        retained_result = repository.save(detached_result)
        assert retained_result.last_output == accepted_output
        assert retained_result.last_exit_code == accepted_exit_code
        assert (
            retained_result.verification_status
            == accepted_verification
        )
        assert retained_result.status_reason_details[
            "recovery_dispatch_lease"
        ]["accepted_result_digest"] == (
            recovery_accepted_result_digest(retained_result)
        )


def test_bound_abort_and_owner_terminal_are_the_only_non_result_terminal_writes(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery-terminal-writes.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    source_id = "owner-terminal-source"
    goal_id = "owner-terminal-goal"

    def lease_for(
        task_id: str,
        *,
        phase: str,
        state: str,
    ) -> dict:
        lease = {
            **_lease(phase=phase, state=state),
            "task_id": task_id,
            "source_task_id": source_id,
            "goal_id": goal_id,
        }
        if state == "worker_admitted":
            lease["admitted_at"] = time.time()
            lease["admitted_worker_url"] = lease["worker_url"]
        return lease

    abort_lease = lease_for(
        "abort-child",
        phase="execute",
        state="worker_admitted",
    )
    owner_lease = lease_for(
        "owner-child",
        phase="propose",
        state="active",
    )
    revoke_lease = lease_for(
        "revoke-child",
        phase="execute",
        state="worker_admitted",
    )

    def child(task_id: str, lease: dict) -> TaskDB:
        return TaskDB(
            id=task_id,
            goal_id=goal_id,
            plan_id=lease["plan_id"],
            team_id=lease["team_id"],
            source_task_id=source_id,
            derivation_reason="goal_task_recovery",
            status="in_progress",
            status_reason_details={
                "model_recovery_release": {
                    "source_task_id": source_id,
                    "goal_id": goal_id,
                    "plan_id": lease["plan_id"],
                    "team_id": lease["team_id"],
                    "release_epoch": lease["release_epoch"],
                },
                "recovery_dispatch_lease": lease,
            },
        )

    with Session(engine) as session:
        session.add(
            TaskDB(
                id=source_id,
                goal_id=goal_id,
                plan_id="recovery-plan",
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "plan_id": "recovery-plan",
                        "status": "materialized",
                    }
                },
            )
        )
        session.add(child("abort-child", abort_lease))
        session.add(child("owner-child", owner_lease))
        session.add(child("revoke-child", revoke_lease))
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    class RepositoryLockPort:
        @contextlib.contextmanager
        def mutation_locks(self, _task_ids):
            yield True

    monkeypatch.setattr(
        (
            "agent.common.task_mutation_lock."
            "get_task_mutation_lock_port"
        ),
        lambda: RepositoryLockPort(),
    )
    repository = TaskRepository()

    detached_revoke = repository.get_by_id("owner-child")
    detached_revoke_details = deepcopy(
        detached_revoke.status_reason_details
    )
    detached_revoke_details["recovery_dispatch_lease"] = {
        **detached_revoke_details[
            "recovery_dispatch_lease"
        ],
        "state": "revoked",
        "revision": 2,
        "revoked_at": time.time(),
        "revocation_reason": "detached_dos",
    }
    detached_revoke.status_reason_details = (
        detached_revoke_details
    )
    detached_revoke.updated_at = time.time() + 5
    retained_active = repository.save(detached_revoke)
    assert retained_active.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "active"
    assert retained_active.status_reason_details[
        "recovery_dispatch_lease"
    ]["revision"] == 1

    forged = repository.get_by_id("owner-child")
    forged.status = "failed"
    forged.updated_at = time.time() + 10
    with pytest.raises(
        ValueError,
        match=(
            "recovery_result_commit_write_authority_required"
        ),
    ):
        repository.save(forged)

    abort_service = RecoveryDispatchGateService(
        repository_provider=lambda: SimpleNamespace(
            task_repo=repository
        ),
        mutation_lock_provider=RepositoryLockPort,
    )
    assert abort_service.revoke_dispatch_lease(
        "revoke-child",
        reason_code="worker_transport_failed",
    )
    service_revoked = repository.get_by_id("revoke-child")
    assert service_revoked.status == "in_progress"
    assert service_revoked.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "revoked"
    assert service_revoked.status_reason_details[
        "recovery_dispatch_lease"
    ]["revision"] == 2
    assert (
        abort_service.abort_dispatch_lease(
            "abort-child",
            target_status="failed",
            reason_code="recovery_dispatch_timeout",
            error="hard timeout",
        )
        == "failed"
    )
    aborted = repository.get_by_id("abort-child")
    assert aborted.status == "failed"
    assert aborted.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "revoked"
    assert aborted.status_reason_details[
        "recovery_dispatch_lease"
    ]["revision"] == 2

    owner_candidate = repository.get_by_id("owner-child")
    invalidated_at = time.time() + 20
    reason_code = "goal_terminal:cancelled"
    owner_marker = {
        "schema": (
            "ananta.recovery_owner_terminal_invalidation.v1"
        ),
        "task_id": owner_candidate.id,
        "goal_id": goal_id,
        "goal_status": "cancelled",
        "previous_status": "in_progress",
        "target_status": "cancelled",
        "reason_code": reason_code,
        "invalidated_at": invalidated_at,
    }
    owner_details = deepcopy(
        owner_candidate.status_reason_details
    )
    owner_details["recovery_dispatch_lease"] = {
        **owner_details["recovery_dispatch_lease"],
        "state": "revoked",
        "revision": 2,
        "revoked_at": invalidated_at,
        "revocation_reason": reason_code,
    }
    owner_details[
        "recovery_owner_terminal_invalidation"
    ] = owner_marker
    owner_candidate.status = "cancelled"
    owner_candidate.status_reason_details = owner_details
    owner_candidate.updated_at = invalidated_at
    with authorize_recovery_owner_terminal_write(
        task_id=owner_candidate.id,
        marker=owner_marker,
    ):
        with authorize_recovery_dispatch_invalidation_write(
            task_id=owner_candidate.id,
            current_lease=owner_lease,
            proposed_lease=owner_details[
                "recovery_dispatch_lease"
            ],
        ):
            owner_persisted = repository.save(owner_candidate)
    assert owner_persisted.status == "cancelled"
    assert owner_persisted.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "revoked"
    assert owner_persisted.status_reason_details[
        "recovery_owner_terminal_invalidation"
    ] == owner_marker

    source_candidate = repository.get_by_id(source_id)
    source_invalidated_at = time.time() + 30
    source_marker = {
        "schema": (
            "ananta.recovery_owner_terminal_invalidation.v1"
        ),
        "task_id": source_id,
        "goal_id": goal_id,
        "goal_status": "cancelled",
        "previous_status": "blocked_by_dependency",
        "target_status": "cancelled",
        "reason_code": reason_code,
        "invalidated_at": source_invalidated_at,
    }
    source_candidate.status = "cancelled"
    source_candidate.status_reason_details = {
        **deepcopy(source_candidate.status_reason_details),
        "recovery_owner_terminal_invalidation": source_marker,
    }
    source_candidate.updated_at = source_invalidated_at
    with authorize_recovery_owner_terminal_write(
        task_id=source_id,
        marker=source_marker,
    ):
        source_persisted = repository.save(source_candidate)
    assert source_persisted.status == "cancelled"
    assert source_persisted.status_reason_details[
        "recovery_owner_terminal_invalidation"
    ] == source_marker


def test_dependency_terminalization_rejects_detached_or_foreign_authority(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery-dependency-terminal.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    source_id = "dependency-terminal-source"
    child_id = "dependency-terminal-child"
    dependency_id = "dependency-terminal-failure"
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=source_id,
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "status": (
                            "materialized_waiting_for_children"
                        )
                    }
                },
            )
        )
        session.add(
            TaskDB(
                id=dependency_id,
                source_task_id=source_id,
                derivation_reason="goal_task_recovery",
                status="failed",
            )
        )
        session.add(
            TaskDB(
                id=child_id,
                source_task_id=source_id,
                derivation_reason="goal_task_recovery",
                status="blocked_by_dependency",
                depends_on=[dependency_id],
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    class RepositoryLockPort:
        @contextlib.contextmanager
        def mutation_locks(self, _task_ids):
            yield True

    monkeypatch.setattr(
        (
            "agent.common.task_mutation_lock."
            "get_task_mutation_lock_port"
        ),
        lambda: RepositoryLockPort(),
    )
    repository = TaskRepository()
    marker = _dependency_reconciliation_marker(
        task_id=child_id,
        source_task_id=source_id,
        dependency_id=dependency_id,
    )

    def terminal_candidate() -> TaskDB:
        candidate = repository.get_by_id(child_id)
        candidate.status = "failed"
        candidate.status_reason_code = (
            "recovery_dependency_terminal"
        )
        candidate.status_reason_details = {
            **deepcopy(candidate.status_reason_details),
            "recovery_dependency_reconciliation": marker,
        }
        candidate.updated_at = marker["reconciled_at"]
        return candidate

    with pytest.raises(
        ValueError,
        match=(
            "recovery_result_commit_write_authority_required"
        ),
    ):
        repository.save(terminal_candidate())

    foreign_marker = _dependency_reconciliation_marker(
        task_id="foreign-child",
        source_task_id=source_id,
        dependency_id=dependency_id,
        reconciled_at=marker["reconciled_at"],
    )
    with authorize_recovery_dependency_reconciliation_write(
        task_id="foreign-child",
        marker=foreign_marker,
    ):
        with pytest.raises(
            ValueError,
            match=(
                "recovery_result_commit_write_authority_required"
            ),
        ):
            repository.save(terminal_candidate())

    retained = repository.get_by_id(child_id)
    assert retained.status == "blocked_by_dependency"
    assert (
        "recovery_dependency_reconciliation"
        not in retained.status_reason_details
    )

    with authorize_recovery_dependency_reconciliation_write(
        task_id=child_id,
        marker=marker,
    ):
        persisted = repository.save(terminal_candidate())
    assert persisted.status == "failed"
    assert persisted.status_reason_details[
        "recovery_dependency_reconciliation"
    ] == marker


def test_finalized_source_result_is_sticky_against_same_status_detached_save(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery-source-result.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="recovery-source",
                goal_id="recovery-goal",
                plan_id="recovery-plan",
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "plan_id": "recovery-plan",
                        "status": "materialized",
                    },
                    "model_recovery_strategy": {
                        "status": "materialized",
                    },
                },
                verification_status={},
                updated_at=1.0,
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    class RepositoryLockPort:
        @contextlib.contextmanager
        def mutation_locks(self, _task_ids):
            yield True

    monkeypatch.setattr(
        (
            "agent.common.task_mutation_lock."
            "get_task_mutation_lock_port"
        ),
        lambda: RepositoryLockPort(),
    )
    repository = TaskRepository()

    missing_publication = repository.get_by_id(
        "recovery-source"
    )
    missing_publication.status = "completed"
    missing_publication.status_reason_code = (
        "recovery_children_verified"
    )
    missing_publication.updated_at = 1.5
    with pytest.raises(
        ValueError,
        match=(
            "recovery_source_finalization_write_authority_required"
        ),
    ):
        repository.save(missing_publication)

    malformed_publication = repository.get_by_id(
        "recovery-source"
    )
    malformed_publication.status = "completed"
    malformed_publication.status_reason_code = (
        "recovery_children_verified"
    )
    malformed_details = deepcopy(
        malformed_publication.status_reason_details
    )
    malformed_details["recovery_source_post_commit"] = {
        "schema": "ananta.recovery_source_post_commit.v1",
        "state": "processing",
        "transition_status": "completed",
        "transition_reason": "recovery_children_verified",
        "transition_id": "f" * 64,
        "old_status": "blocked_by_dependency",
        "created_at": 1.5,
    }
    malformed_publication.status_reason_details = (
        malformed_details
    )
    malformed_publication.verification_status = {
        "model_recovery_result": {
            "schema": "ananta.recovery_source_result.v2",
            "status": "passed",
            "reason_code": "recovery_children_verified",
            "artifact_count": 0,
            "artifacts": [],
        }
    }
    malformed_publication.updated_at = 1.75
    with pytest.raises(
        ValueError,
        match=(
            "recovery_source_finalization_write_authority_required"
        ),
    ):
        repository.save(malformed_publication)

    finalized = repository.get_by_id("recovery-source")
    authentic_result = {
        "schema": "ananta.recovery_source_result.v2",
        "status": "passed",
        "reason_code": "recovery_children_verified",
        "artifact_count": 0,
        "artifacts": [],
    }
    finalized.status = "completed"
    finalized.status_reason_code = "recovery_children_verified"
    finalized.status_reason_details = {
        **deepcopy(finalized.status_reason_details),
        "model_recovery": {
            "plan_id": "recovery-plan",
            "status": "completed",
        },
        "model_recovery_strategy": {"status": "completed"},
        "recovery_source_post_commit": {
            "schema": "ananta.recovery_source_post_commit.v1",
            "state": "pending",
            "transition_status": "completed",
            "transition_reason": "recovery_children_verified",
            "transition_id": "f" * 64,
            "old_status": "blocked_by_dependency",
            "created_at": 2.0,
        },
    }
    finalized.verification_status = {
        "model_recovery": {
            "plan_id": "recovery-plan",
            "status": "completed",
        },
        "model_recovery_strategy": {"status": "completed"},
        "model_recovery_result": authentic_result,
    }
    finalized.last_output = "Hub-finalized source output"
    finalized.last_exit_code = 0
    finalized.callback_url = "https://callback.example/final"
    finalized.callback_token = "bound-callback-token"
    finalized.parent_task_id = "bound-parent"
    finalized.current_worker_job_id = "bound-worker-job"
    finalized.updated_at = 2.0

    with pytest.raises(
        ValueError,
        match=(
            "recovery_source_finalization_write_authority_required"
        ),
    ):
        repository.save(finalized)

    rejected = repository.get_by_id("recovery-source")
    assert rejected.status == "blocked_by_dependency"
    assert "recovery_source_post_commit" not in (
        rejected.status_reason_details
    )
    assert "model_recovery_result" not in (
        rejected.verification_status
    )

    with authorize_recovery_source_finalization_write(
        finalized.id
    ):
        published = repository.save(finalized)

    assert published.verification_status[
        "model_recovery_result"
    ] == authentic_result

    unauthorized_claim = repository.get_by_id(
        "recovery-source"
    )
    pending_marker = deepcopy(
        unauthorized_claim.status_reason_details[
            "recovery_source_post_commit"
        ]
    )
    processing_marker = {
        **pending_marker,
        "state": "processing",
        "processing_at": time.time() + 10,
        "attempt_id": "delivery-attempt-1",
        "attempt_count": 1,
    }
    unauthorized_claim.status_reason_details = {
        **deepcopy(unauthorized_claim.status_reason_details),
        "recovery_source_post_commit": processing_marker,
    }
    unauthorized_claim.updated_at = time.time() + 10
    with pytest.raises(
        ValueError,
        match=(
            "recovery_source_post_commit_write_authority_required"
        ),
    ):
        repository.save(unauthorized_claim)

    authorized_claim = repository.get_by_id(
        "recovery-source"
    )
    authorized_claim.status_reason_details = {
        **deepcopy(authorized_claim.status_reason_details),
        "recovery_source_post_commit": processing_marker,
    }
    authorized_claim.updated_at = time.time() + 10
    with authorize_recovery_source_post_commit_write(
        task_id=authorized_claim.id,
        current=pending_marker,
        proposed=processing_marker,
    ):
        claimed = repository.save(authorized_claim)
    assert claimed.status_reason_details[
        "recovery_source_post_commit"
    ]["state"] == "processing"

    failed_marker = {
        **processing_marker,
        "state": "pending",
        "failed_at": time.time() + 20,
        "last_error": "callback unavailable",
    }
    failed_claim = repository.get_by_id("recovery-source")
    failed_claim.status_reason_details = {
        **deepcopy(failed_claim.status_reason_details),
        "recovery_source_post_commit": failed_marker,
    }
    failed_claim.updated_at = time.time() + 20
    with authorize_recovery_source_post_commit_write(
        task_id=failed_claim.id,
        current=processing_marker,
        proposed=failed_marker,
    ):
        retriable = repository.save(failed_claim)
    assert retriable.status_reason_details[
        "recovery_source_post_commit"
    ]["state"] == "pending"

    detached = repository.get_by_id("recovery-source")
    forged_result = {
        **authentic_result,
        "reason_code": "forged_same_status_result",
        "artifact_count": 1,
        "artifacts": [{"artifact_id": "attacker"}],
    }
    detached.verification_status = {
        **deepcopy(detached.verification_status),
        "model_recovery_result": forged_result,
        "execution_artifacts": [{"artifact_id": "attacker"}],
    }
    detached.last_output = "detached callback overwrite"
    detached.last_exit_code = 99
    detached.callback_url = "https://attacker.invalid/callback"
    detached.callback_token = "attacker-token"
    detached.parent_task_id = "attacker-parent"
    detached.current_worker_job_id = "attacker-worker-job"
    detached.updated_at = time.time() + 10

    repository.save(detached)

    persisted = repository.get_by_id("recovery-source")
    assert persisted.status == "completed"
    assert persisted.verification_status[
        "model_recovery_result"
    ] == authentic_result
    assert persisted.verification_status[
        "model_recovery_result"
    ] != forged_result
    assert persisted.last_output == "Hub-finalized source output"
    assert persisted.last_exit_code == 0
    assert (
        persisted.callback_url
        == "https://callback.example/final"
    )
    assert persisted.callback_token == "bound-callback-token"
    assert persisted.parent_task_id == "bound-parent"
    assert persisted.current_worker_job_id == "bound-worker-job"
