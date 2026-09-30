import copy
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from sqlalchemy import or_
from sqlmodel import Session, delete, select

from agent.common.recovery_task_merge_policy import (
    _merge_dispatch_lease as _merge_dispatch_lease,
)
from agent.common.recovery_task_merge_policy import (
    _merge_recovery_source_post_commit as _merge_recovery_source_post_commit,
)
from agent.common.recovery_task_write_validation import _TERMINAL_TASK_STATUSES
from agent.db_models import (
    AgentSessionDB,
    ArchivedTaskDB,
    PolicySnapshotDB,
    TaskDB,
    ToolCallDB,
)
from agent.ports.task_completion_policy import TaskCompletionPolicyPort
from agent.repositories.task_auxiliary_repositories import (
    AgentSessionRepositoryMixin,
    ArchivedTaskRepositoryMixin,
    PolicySnapshotRepositoryMixin,
    TaskAuxiliaryRepositoryDependencies,
    ToolCallRepositoryMixin,
)
from agent.repositories.task_repository_session import task_repository_session
from agent.repositories.task_write_preparation import (
    _apply_task_completion_policy as _apply_task_completion_policy,
)
from agent.repositories.task_write_preparation import (
    _detached_task_row_copy as _detached_task_row_copy,
)
from agent.repositories.task_write_preparation import (
    _prepare_existing_task_write as _prepare_existing_task_write,
)


@dataclass(frozen=True)
class TaskStatusCompareAndSetResult:
    """Outcome of one repository-owned atomic status compare-and-set."""

    updated: bool
    task: TaskDB | None
    previous_status: str | None



def _engine():
    from agent.database import engine

    return engine


class TaskRepository:
    def __init__(self, *, completion_policy: TaskCompletionPolicyPort | None = None) -> None:
        self._completion_policy = completion_policy

    def get_all(self):
        with task_repository_session(_engine()) as session:
            return session.exec(select(TaskDB)).all()

    def get_by_id(self, task_id: str) -> Optional[TaskDB]:
        with task_repository_session(_engine()) as session:
            return session.get(TaskDB, task_id)

    def list_stale_reserved_unsloth_cleanup(
        self,
        *,
        before: float,
        limit: int,
    ) -> List[TaskDB]:
        bounded = max(1, min(int(limit), 500))
        with task_repository_session(_engine()) as session:
            statement = (
                select(TaskDB)
                .where(
                    TaskDB.status == "reserved",
                    TaskDB.task_kind == "ml.storage.cleanup",
                    TaskDB.created_at <= float(before),
                )
                .order_by(TaskDB.created_at.asc(), TaskDB.id.asc())
                .limit(bounded)
            )
            return list(session.exec(statement).all())

    def get_by_goal_id(self, goal_id: str) -> List[TaskDB]:
        with task_repository_session(_engine()) as session:
            return session.exec(select(TaskDB).where(TaskDB.goal_id == goal_id)).all()

    def save(self, task: TaskDB):
        task_id = str(getattr(task, "id", "") or "").strip()
        if not task_id:
            raise ValueError("task_id_required")
        from agent.common.recovery_result_write_boundary import (
            defer_task_repository_save,
        )

        if defer_task_repository_save(task_id, task=task):
            return self.get_by_id(task_id) or task
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        # Resolve the immutable owner hint before taking locks.  Recovery
        # writers and terminal sweeps then acquire the identical sorted
        # child/source pair; neither can hold the source and wait on a child.
        with task_repository_session(_engine()) as hint_session:
            authoritative_hint = hint_session.get(TaskDB, task_id)
            source_task_id = str(
                getattr(
                    authoritative_hint,
                    "source_task_id",
                    None,
                )
                or getattr(task, "source_task_id", None)
                or ""
            ).strip()
        lock_ids = {task_id}
        if source_task_id:
            lock_ids.add(source_task_id)
        with get_task_mutation_lock_port().mutation_locks(lock_ids) as acquired:
            if not acquired:
                raise RuntimeError(f"task_mutation_lock_unavailable:{task_id}")
            with task_repository_session(_engine(), write=True) as session:
                statement = select(TaskDB).where(TaskDB.id == task_id)
                if str(_engine().dialect.name or "").lower() == "postgresql":
                    statement = statement.with_for_update()
                authoritative = session.exec(statement).one_or_none()
                if authoritative is None:
                    task = _apply_task_completion_policy(
                        None,
                        task,
                        session=session,
                        completion_policy=self._completion_policy,
                    )
                    persisted = session.merge(task)
                    session.commit()
                    session.refresh(persisted)
                    return persisted
                prepared = _prepare_existing_task_write(
                    authoritative,
                    task,
                    session=session,
                    lock_ids=lock_ids,
                    write_operation="save",
                    completion_policy=self._completion_policy,
                )
                if prepared is None:
                    return authoritative
                task = prepared

                persisted = session.merge(task)
                session.commit()
                session.refresh(persisted)
                return persisted

    def insert_native_task_fenced(self, task: TaskDB, *, native_command, lease_guard) -> tuple[TaskDB, bool]:
        """Optional creation port; existing generic Task writes stay unchanged."""
        from agent.repositories.native_workflow_ingestion import insert_native_task_fenced

        return insert_native_task_fenced(
            engine=_engine(), task=task, native_command=native_command, lease_guard=lease_guard,
            prepare_new=lambda candidate, session: _apply_task_completion_policy(
                None, candidate, session=session, completion_policy=self._completion_policy
            ),
        )

    def replace_bound_knowledge_index_envelope(
        self,
        task_id: str,
        *,
        expected_envelope: dict,
        replacement_envelope: dict,
    ) -> TaskDB:
        """Atomically replace one Hub-bound index envelope.

        The focused merge keeps unrelated ``worker_execution_context`` keys
        written by concurrent services and provides an optimistic conflict
        boundary for changes to the authoritative envelope itself.
        """

        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id_required")
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        with get_task_mutation_lock_port().mutation_locks(
            {normalized_task_id}
        ) as acquired:
            if not acquired:
                raise RuntimeError(
                    "task_mutation_lock_unavailable:"
                    + normalized_task_id
                )
            with task_repository_session(_engine(), write=True) as session:
                statement = select(TaskDB).where(
                    TaskDB.id == normalized_task_id
                )
                if (
                    str(_engine().dialect.name or "").lower()
                    == "postgresql"
                ):
                    statement = statement.with_for_update()
                task = session.exec(statement).one_or_none()
                if task is None:
                    raise ValueError("knowledge_index_job_not_found")
                context = copy.deepcopy(
                    dict(task.worker_execution_context or {})
                )
                current_envelope = context.get("knowledge_index_job")
                if current_envelope == replacement_envelope:
                    return task
                if current_envelope != expected_envelope:
                    raise ValueError(
                        "knowledge_index_execution_queue_context_conflict"
                    )
                context["knowledge_index_job"] = copy.deepcopy(
                    replacement_envelope
                )
                task.worker_execution_context = context
                task.updated_at = max(
                    time.time(),
                    float(task.updated_at or 0.0),
                )
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

    def upsert_bound_knowledge_index_worker_snapshot(
        self,
        task_id: str,
        *,
        status: str,
        base_envelope: dict,
        worker_binding: dict,
    ) -> TaskDB:
        """Persist a capability-free Hub snapshot in an isolated Worker DB."""

        normalized_task_id = str(task_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        assignment = base_envelope.get("assignment")
        if (
            not normalized_task_id
            or not normalized_status
            or normalized_status in _TERMINAL_TASK_STATUSES
            or str(base_envelope.get("schema") or "")
            != "ananta.knowledge_index_execution_job.v2"
            or str(base_envelope.get("job_id") or "")
            != normalized_task_id
            or "source_access_enforcement_manifest" in base_envelope
            or not isinstance(assignment, dict)
            or set(worker_binding)
            != {"schema", "worker_id", "worker_url"}
            or worker_binding.get("schema")
            != "ananta.knowledge_index_worker_binding.v1"
            or str(worker_binding.get("worker_id") or "")
            != str(assignment.get("worker_id") or "")
            or not str(worker_binding.get("worker_url") or "").strip()
        ):
            raise ValueError(
                "knowledge_index_task_snapshot_persistence_invalid"
            )
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        with get_task_mutation_lock_port().mutation_locks(
            {normalized_task_id}
        ) as acquired:
            if not acquired:
                raise RuntimeError(
                    "task_mutation_lock_unavailable:"
                    + normalized_task_id
                )
            with task_repository_session(_engine(), write=True) as session:
                statement = select(TaskDB).where(
                    TaskDB.id == normalized_task_id
                )
                if (
                    str(_engine().dialect.name or "").lower()
                    == "postgresql"
                ):
                    statement = statement.with_for_update()
                task = session.exec(statement).one_or_none()
                if task is None:
                    task = TaskDB(
                        id=normalized_task_id,
                        status=normalized_status,
                        task_kind="codecompass_index_build",
                        assigned_agent_url=str(
                            worker_binding["worker_url"]
                        ).strip().rstrip("/"),
                        worker_execution_context={
                            "knowledge_index_job": copy.deepcopy(
                                base_envelope
                            ),
                            "knowledge_index_worker_binding": (
                                copy.deepcopy(worker_binding)
                            ),
                        },
                    )
                else:
                    if str(task.task_kind or "").strip().lower() != (
                        "codecompass_index_build"
                    ):
                        raise ValueError(
                            "knowledge_index_task_snapshot_task_mismatch"
                        )
                    if str(task.status or "").strip().lower() in (
                        _TERMINAL_TASK_STATUSES
                    ):
                        raise ValueError(
                            "knowledge_index_task_snapshot_task_terminal"
                        )
                    context = copy.deepcopy(
                        dict(task.worker_execution_context or {})
                    )
                    current_job = context.get("knowledge_index_job")
                    current_base = (
                        copy.deepcopy(dict(current_job))
                        if isinstance(current_job, dict)
                        else {}
                    )
                    current_base.pop(
                        "source_access_enforcement_manifest",
                        None,
                    )
                    assigned_url = str(
                        task.assigned_agent_url or ""
                    ).strip().rstrip("/")
                    expected_url = str(
                        worker_binding["worker_url"]
                    ).strip().rstrip("/")
                    existing_binding = context.get(
                        "knowledge_index_worker_binding"
                    )
                    if (
                        current_base != base_envelope
                        or assigned_url != expected_url
                        or (
                            existing_binding is not None
                            and existing_binding != worker_binding
                        )
                    ):
                        raise ValueError(
                            "knowledge_index_task_snapshot_authority_conflict"
                        )
                    if existing_binding is None:
                        if str(task.status or "").strip().lower() != (
                            normalized_status
                        ):
                            raise ValueError(
                                "knowledge_index_task_snapshot_status_conflict"
                            )
                        # A distributed Worker can share the Hub PostgreSQL
                        # database. Existing Hub Task rows are validation-only:
                        # do not add Worker projection keys or touch updated_at.
                        return task
                    # An isolated Worker stores a minimal projection only. A
                    # fresh Hub snapshot is authoritative for its lifecycle
                    # status and replaces transient local context additions.
                    task.status = normalized_status
                    task.assigned_agent_url = expected_url
                    task.worker_execution_context = {
                        "knowledge_index_job": copy.deepcopy(
                            base_envelope
                        ),
                        "knowledge_index_worker_binding": copy.deepcopy(
                            worker_binding
                        ),
                    }
                task.updated_at = max(
                    time.time(),
                    float(task.updated_at or 0.0),
                )
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

    def compare_and_set_status(
        self,
        task_id: str,
        *,
        expected_statuses: set[str],
        target_status: str,
        predicate: Callable[[TaskDB], bool] | None = None,
        mutate: Callable[[TaskDB], None] | None = None,
    ) -> TaskStatusCompareAndSetResult:
        """Atomically validate and commit one existing Task status mutation."""

        normalized_task_id = str(task_id or "").strip()
        normalized_target = str(target_status or "").strip().lower()
        normalized_expected = {
            str(value or "").strip().lower() for value in expected_statuses if str(value or "").strip()
        }
        if not normalized_task_id or not normalized_target or not normalized_expected:
            return TaskStatusCompareAndSetResult(
                updated=False,
                task=None,
                previous_status=None,
            )
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        with task_repository_session(_engine()) as hint_session:
            authoritative_hint = hint_session.get(
                TaskDB,
                normalized_task_id,
            )
            source_task_id = str(
                getattr(
                    authoritative_hint,
                    "source_task_id",
                    None,
                )
                or ""
            ).strip()
        lock_ids = {normalized_task_id}
        if source_task_id:
            lock_ids.add(source_task_id)
        with get_task_mutation_lock_port().mutation_locks(lock_ids) as acquired:
            if not acquired:
                return TaskStatusCompareAndSetResult(
                    updated=False,
                    task=None,
                    previous_status=None,
                )
            with task_repository_session(_engine(), write=True) as session:
                statement = select(TaskDB).where(TaskDB.id == normalized_task_id)
                if str(_engine().dialect.name or "").lower() == "postgresql":
                    statement = statement.with_for_update()
                authoritative = session.exec(statement).one_or_none()
                if authoritative is None:
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=None,
                        previous_status=None,
                    )
                previous_status = str(authoritative.status or "").strip().lower()
                if previous_status not in normalized_expected:
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=authoritative,
                        previous_status=previous_status,
                    )
                if predicate is not None and not predicate(authoritative):
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=authoritative,
                        previous_status=previous_status,
                    )
                # Persisted rows may contain legacy JSON nulls for fields whose
                # current model default is a list.  Re-validating the ORM dump
                # would reject such an otherwise authoritative row before the
                # CAS policy can compare it.  The detached copy preserves the
                # exact row values without copying SQLAlchemy Session state;
                # closed delta checks still reject unauthorized mutation.
                candidate = _detached_task_row_copy(authoritative)
                candidate.status = normalized_target
                mutation_timestamp = time.time()
                candidate.updated_at = mutation_timestamp
                if mutate is not None:
                    mutate(candidate)
                # Repository-owned revision time is not caller-mutable.
                candidate.updated_at = mutation_timestamp
                if (
                    str(candidate.id or "").strip() != normalized_task_id
                    or str(candidate.status or "").strip().lower() != normalized_target
                ):
                    raise ValueError("task_status_cas_candidate_binding_invalid")
                prepared = _prepare_existing_task_write(
                    authoritative,
                    candidate,
                    session=session,
                    lock_ids=lock_ids,
                    write_operation="status_cas",
                    completion_policy=self._completion_policy,
                )
                if prepared is None:
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=authoritative,
                        previous_status=previous_status,
                    )
                persisted = session.merge(prepared)
                session.commit()
                session.refresh(persisted)
                return TaskStatusCompareAndSetResult(
                    updated=(str(persisted.status or "").strip().lower() == normalized_target),
                    task=persisted,
                    previous_status=previous_status,
                )

    def delete(self, task_id: str):
        with task_repository_session(_engine(), write=True) as session:
            task = session.get(TaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def clear_team_assignments(self, team_id: str) -> int:
        with task_repository_session(_engine(), write=True) as session:
            statement = select(TaskDB).where(TaskDB.team_id == team_id)
            tasks = session.exec(statement).all()
            from agent.common.recovery_task_mutation_policy import (
                ensure_external_recovery_mutation_allowed,
            )

            for task in tasks:
                ensure_external_recovery_mutation_allowed(
                    task,
                    action="team_detach",
                )
            for task in tasks:
                task.team_id = None
                session.add(task)
            session.commit()
            return len(tasks)

    def get_old_tasks(self, cutoff: float):
        with task_repository_session(_engine()) as session:
            statement = select(TaskDB).where(TaskDB.created_at < cutoff)
            return session.exec(statement).all()

    def get_paged(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str = None,
        status_values: list[str] | None = None,
        agent: str = None,
        since: float = None,
        until: float = None,
        tenant_id: str | None = None,
        project_id: str | None = None,
        task_kind: str | None = None,
    ):
        with task_repository_session(_engine()) as session:
            statement = select(TaskDB)
            if status:
                statement = statement.where(TaskDB.status == status)
            elif status_values:
                statement = statement.where(or_(*[TaskDB.status == val for val in status_values]))
            if agent:
                statement = statement.where(TaskDB.assigned_agent_url == agent)
            if since:
                statement = statement.where(TaskDB.created_at >= since)
            if until:
                statement = statement.where(TaskDB.created_at <= until)
            if tenant_id is not None:
                statement = statement.where(TaskDB.tenant_id == tenant_id)
            if project_id is not None:
                statement = statement.where(TaskDB.project_id == project_id)
            if task_kind is not None:
                statement = statement.where(TaskDB.task_kind == task_kind)

            statement = (
                statement.order_by(
                    TaskDB.updated_at.desc(),
                    TaskDB.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return session.exec(statement).all()


def _task_auxiliary_repository_dependencies() -> TaskAuxiliaryRepositoryDependencies:
    """Resolve patchable persistence dependencies at call time."""

    return TaskAuxiliaryRepositoryDependencies(
        session_factory=Session,
        select=select,
        delete=delete,
        archived_task_model=ArchivedTaskDB,
        agent_session_model=AgentSessionDB,
        tool_call_model=ToolCallDB,
        policy_snapshot_model=PolicySnapshotDB,
    )


class ArchivedTaskRepository(ArchivedTaskRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )


class AgentSessionRepository(AgentSessionRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )


class ToolCallRepository(ToolCallRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )


class PolicySnapshotRepository(PolicySnapshotRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )
