"""Hub-controlled binding of fenced workflow leases to registered Workers."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import WorkflowWorkerAssignmentDB
from agent.services.workflow_runtime.ownership import ExecutionOwnershipStore
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)
from agent.services.workflow_worker_service_auth import (
    STRICT_WORKER_REGISTRATION_PROVENANCE,
)


class WorkflowWorkerAssignmentError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkflowWorkerAssignment:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    attempt_id: str
    fencing_token: int
    hub_task_id: str
    worker_id: str
    worker_url: str
    revision: int = 1
    assigned_at: float = 0.0

    def assert_valid(self) -> None:
        identifiers = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.attempt_id,
            self.hub_task_id,
            self.worker_id,
        )
        if any(
            not value or len(value) > 256 or "\x00" in value
            for value in identifiers
        ):
            raise WorkflowWorkerAssignmentError(
                "workflow_worker_assignment_binding_invalid",
                status_code=422,
            )
        if self.fencing_token < 1 or self.revision < 1:
            raise WorkflowWorkerAssignmentError(
                "workflow_worker_assignment_fencing_invalid",
                status_code=422,
            )
        if _normalize_url(self.worker_url) != self.worker_url:
            raise WorkflowWorkerAssignmentError(
                "workflow_worker_assignment_url_invalid",
                status_code=422,
            )


class WorkflowWorkerAssignmentStore(Protocol):
    def bind(
        self,
        assignment: WorkflowWorkerAssignment,
    ) -> WorkflowWorkerAssignment: ...

    def get(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
    ) -> WorkflowWorkerAssignment | None: ...


class InMemoryWorkflowWorkerAssignmentStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], WorkflowWorkerAssignment] = {}
        self._lock = threading.RLock()

    def bind(
        self,
        assignment: WorkflowWorkerAssignment,
    ) -> WorkflowWorkerAssignment:
        assignment.assert_valid()
        key = (assignment.tenant_id, assignment.run_id, assignment.step_id)
        with self._lock:
            current = self._values.get(key)
            value = _next_assignment(current, assignment)
            self._values[key] = value
            return value

    def get(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
    ) -> WorkflowWorkerAssignment | None:
        with self._lock:
            return self._values.get((str(tenant_id), str(run_id), str(step_id)))


class SQLAlchemyWorkflowWorkerAssignmentStore(SQLAlchemyStoreSupport):
    def __init__(self, bind: Engine | SessionFactory) -> None:
        super().__init__(bind)

    def bind(
        self,
        assignment: WorkflowWorkerAssignment,
    ) -> WorkflowWorkerAssignment:
        assignment.assert_valid()
        try:
            return self._bind_once(assignment)
        except IntegrityError:
            # Two Hub dispatcher threads can both observe an empty slot before
            # the first INSERT commits.  The database uniqueness constraint is
            # authoritative; retrying converts an identical race into the
            # normal idempotent read and a conflicting race into a fail-closed
            # identity conflict.
            return self._bind_once(assignment)

    def _bind_once(
        self,
        assignment: WorkflowWorkerAssignment,
    ) -> WorkflowWorkerAssignment:
        with self._transaction() as session:
            row = self._read_row(
                session,
                tenant_id=assignment.tenant_id,
                run_id=assignment.run_id,
                step_id=assignment.step_id,
                lock=True,
            )
            current = _from_row(row) if row is not None else None
            value = _next_assignment(current, assignment)
            if row is None:
                session.add(_to_row(value))
            elif value != current:
                result = session.execute(
                    sa.update(WorkflowWorkerAssignmentDB)
                    .where(
                        WorkflowWorkerAssignmentDB.id == row.id,
                        WorkflowWorkerAssignmentDB.revision == row.revision,
                    )
                    .values(
                        workflow_id=value.workflow_id,
                        attempt_id=value.attempt_id,
                        fencing_token=value.fencing_token,
                        hub_task_id=value.hub_task_id,
                        worker_id=value.worker_id,
                        worker_url=value.worker_url,
                        revision=value.revision,
                        assigned_at=value.assigned_at,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise WorkflowWorkerAssignmentError(
                        "workflow_worker_assignment_compare_and_set_failed"
                    )
            session.flush()
            return value

    def get(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
    ) -> WorkflowWorkerAssignment | None:
        with self._transaction() as session:
            row = self._read_row(
                session,
                tenant_id=str(tenant_id),
                run_id=str(run_id),
                step_id=str(step_id),
                lock=False,
            )
            return _from_row(row) if row is not None else None

    def _read_row(
        self,
        session,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        lock: bool,
    ) -> WorkflowWorkerAssignmentDB | None:
        statement = sa.select(WorkflowWorkerAssignmentDB).where(
            WorkflowWorkerAssignmentDB.tenant_id == tenant_id,
            WorkflowWorkerAssignmentDB.run_id == run_id,
            WorkflowWorkerAssignmentDB.step_id == step_id,
        )
        if lock:
            statement = self._for_update(statement)
        return session.execute(statement).scalar_one_or_none()


class WorkflowWorkerAssignmentService:
    """Bind only a Hub-created task lease to a Hub-selected registry row."""

    def __init__(
        self,
        *,
        ownership: ExecutionOwnershipStore,
        assignments: WorkflowWorkerAssignmentStore,
        clock=time.time,
    ) -> None:
        self._ownership = ownership
        self._assignments = assignments
        self._clock = clock

    def bind_dispatched_task(
        self,
        *,
        task: Any,
        worker: Any,
    ) -> WorkflowWorkerAssignment | None:
        task_id = str(_value(task, "id") or "").strip()
        context = _mapping(_value(task, "worker_execution_context"))
        binding = _task_binding(task_id=task_id, context=context)
        if binding is None:
            return None
        worker_id = str(_value(worker, "name") or "").strip()
        worker_url = _normalize_url(_value(worker, "url"))
        authorized = {
            str(value).strip()
            for value in (_value(worker, "authorized_capabilities") or [])
            if str(value).strip()
        }
        if (
            str(_value(worker, "role") or "").strip().lower() != "worker"
            or not bool(_value(worker, "registration_validated"))
            or str(_value(worker, "registration_provenance") or "")
            != STRICT_WORKER_REGISTRATION_PROVENANCE
            or binding["required_capability"] not in authorized
            or not worker_id
            or not worker_url
        ):
            raise WorkflowWorkerAssignmentError(
                "workflow_worker_assignment_registry_identity_denied",
                status_code=403,
            )
        ownership = self._ownership.get(
            tenant_id=binding["tenant_id"],
            run_id=binding["run_id"],
            step_id=binding["step_id"],
        )
        now = float(self._clock())
        if ownership is None:
            raise WorkflowWorkerAssignmentError(
                "workflow_worker_assignment_lease_not_found",
                status_code=404,
            )
        if (
            ownership.workflow_id != binding["workflow_id"]
            or ownership.attempt_id != binding["attempt_id"]
            or ownership.fencing_token != binding["fencing_token"]
            or ownership.owner_id != binding["hub_owner_id"]
            or ownership.status != "active"
            or ownership.lease_expires_at <= now
        ):
            raise WorkflowWorkerAssignmentError(
                "workflow_worker_assignment_lease_mismatch",
                status_code=409,
            )
        return self._assignments.bind(
            WorkflowWorkerAssignment(
                tenant_id=binding["tenant_id"],
                workflow_id=binding["workflow_id"],
                run_id=binding["run_id"],
                step_id=binding["step_id"],
                attempt_id=binding["attempt_id"],
                fencing_token=binding["fencing_token"],
                hub_task_id=task_id,
                worker_id=worker_id,
                worker_url=worker_url,
                assigned_at=now,
            )
        )


def _task_binding(*, task_id: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    if context.get("schema") == "ananta.native_graph_worker_context.v1":
        command = _mapping(context.get("native_node_command"))
        node = _mapping(command.get("node"))
        run_id = str(command.get("run_id") or "")
        step_id = str(node.get("node_id") or command.get("node_id") or "")
        return {
            "tenant_id": str(command.get("tenant_id") or ""),
            "workflow_id": str(command.get("workflow_id") or ""),
            "run_id": run_id,
            "step_id": step_id,
            "attempt_id": str(command.get("attempt_id") or ""),
            "fencing_token": _positive_int(command.get("fencing_token")),
            "hub_owner_id": f"hub-native:{run_id}:{step_id}",
            "required_capability": "workflow.adapter.native",
        }
    if context.get("schema") == "ananta.workflow-adapter-worker-task.v1":
        return {
            "tenant_id": str(context.get("tenant_id") or ""),
            "workflow_id": str(context.get("workflow_id") or ""),
            "run_id": str(context.get("run_id") or ""),
            "step_id": str(context.get("step_id") or ""),
            "attempt_id": str(context.get("attempt_id") or ""),
            "fencing_token": _positive_int(context.get("fencing_token")),
            "hub_owner_id": str(context.get("owner_id") or ""),
            "required_capability": "workflow.adapter.langgraph",
        }
    return None


def _next_assignment(
    current: WorkflowWorkerAssignment | None,
    candidate: WorkflowWorkerAssignment,
) -> WorkflowWorkerAssignment:
    if current is None:
        return candidate
    same_lease = (
        current.workflow_id == candidate.workflow_id
        and current.attempt_id == candidate.attempt_id
        and current.fencing_token == candidate.fencing_token
        and current.hub_task_id == candidate.hub_task_id
    )
    if same_lease:
        if (
            current.worker_id == candidate.worker_id
            and current.worker_url == candidate.worker_url
        ):
            return current
        raise WorkflowWorkerAssignmentError(
            "workflow_worker_assignment_identity_conflict"
        )
    if candidate.fencing_token <= current.fencing_token:
        raise WorkflowWorkerAssignmentError("workflow_worker_assignment_stale")
    return replace(candidate, revision=current.revision + 1)


def _to_row(value: WorkflowWorkerAssignment) -> WorkflowWorkerAssignmentDB:
    return WorkflowWorkerAssignmentDB(
        id=stable_row_id("wfra", value.tenant_id, value.run_id, value.step_id),
        **value.__dict__,
    )


def _from_row(row: WorkflowWorkerAssignmentDB) -> WorkflowWorkerAssignment:
    return WorkflowWorkerAssignment(
        tenant_id=row.tenant_id,
        workflow_id=row.workflow_id,
        run_id=row.run_id,
        step_id=row.step_id,
        attempt_id=row.attempt_id,
        fencing_token=row.fencing_token,
        hub_task_id=row.hub_task_id,
        worker_id=row.worker_id,
        worker_url=row.worker_url,
        revision=row.revision,
        assigned_at=row.assigned_at,
    )


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: object, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _positive_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _normalize_url(value: object) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    hostname = str(parsed.hostname).lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", "")
    )


__all__ = [
    "InMemoryWorkflowWorkerAssignmentStore",
    "SQLAlchemyWorkflowWorkerAssignmentStore",
    "WorkflowWorkerAssignment",
    "WorkflowWorkerAssignmentError",
    "WorkflowWorkerAssignmentService",
    "WorkflowWorkerAssignmentStore",
]
