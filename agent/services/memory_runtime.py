"""Hub composition and bounded authenticated read sessions for legacy callers."""

import logging
import time
import uuid
from collections.abc import MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlmodel import Session

from agent.adapters.ephemeral_scratchpad_store import InMemoryScratchpadStore
from agent.db_models import GoalDB, TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.repositories.memory_audit import SQLMemoryAuditRepository
from agent.repositories.memory_policy import SQLMemoryRunPolicyRepository
from agent.repositories.memory_snapshot import SQLMemorySnapshotRepository
from agent.services.ephemeral_scratchpad_service import EphemeralScratchpadService
from agent.services.memory_access_context import MemoryAccessContext, bind_memory_access, current_memory_access
from agent.services.memory_audit_service import MemoryAuditService
from agent.services.memory_channel_registry import MemoryChannelRegistry
from agent.services.memory_policy_gateway import MemoryAccessError, MemoryPolicyGateway
from agent.services.memory_snapshot_service import MemorySnapshotService
from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryCapability,
    MemoryNamespace,
    MemoryOperation,
    MemoryRunPolicy,
    MemoryScope,
    MemorySensitivity,
)


@dataclass(frozen=True)
class MemoryRuntimeDependencies:
    """Framework access seam (DIP): production defaults read Flask lazily.

    The runtime is exercisable without an app or request context: tests
    inject plain callables while production wiring resolves the framework
    containers at call time.
    """

    extensions: Callable[[], MutableMapping[str, Any]]
    role: Callable[[], str | None]
    request_state: Callable[[], dict | None]


def _default_extensions() -> MutableMapping[str, Any]:
    from flask import current_app, has_app_context

    return current_app.extensions if has_app_context() else {}


def _default_role() -> str | None:
    from flask import current_app, has_app_context

    if not has_app_context():
        return None
    # ROLE is the production bootstrap key; AGENT_ROLE supports older explicit compositions.
    return current_app.config["ROLE"] if "ROLE" in current_app.config else current_app.config.get("AGENT_ROLE")


def _default_request_state() -> dict | None:
    from flask import g, has_request_context

    if not has_request_context():
        return None
    return {
        "service_identity": dict(getattr(g, "service_identity", {}) or {}),
        "user": dict(getattr(g, "user", {}) or {}),
        "is_admin": bool(getattr(g, "is_admin", False)),
    }


PRODUCTION_MEMORY_RUNTIME_DEPENDENCIES = MemoryRuntimeDependencies(
    extensions=_default_extensions,
    role=_default_role,
    request_state=_default_request_state,
)


class HubMemoryRuntime:
    def __init__(self, engine, *, clock=time.time, dependencies: MemoryRuntimeDependencies | None = None):
        self.engine = engine
        self.clock = clock
        self._deps = dependencies or PRODUCTION_MEMORY_RUNTIME_DEPENDENCIES
        self.policies = SQLMemoryRunPolicyRepository(engine, clock=clock, hub_read_authority=self._allow_read)
        self.snapshots = MemorySnapshotService(repository=SQLMemorySnapshotRepository(engine, clock=clock))
        catalog = Path(__file__).resolve().parents[2] / "config/security/memory-channels.v1.json"
        self.gateway = MemoryPolicyGateway(
            policies=self.policies,
            channels=MemoryChannelRegistry.from_path(catalog),
            clock=clock,
            audit=MemoryAuditService(repository=SQLMemoryAuditRepository(engine), clock=clock),
        )
        self._scratchpad_store = InMemoryScratchpadStore()
        self.scratchpad = EphemeralScratchpadService(gateway=self.gateway, store=self._scratchpad_store, clock=clock)

    def discard_task_scratchpad(self, task):
        """Trusted committed Hub task projection, never a Worker/model-supplied namespace."""
        if not task.tenant_id or not task.project_id:
            return 0
        return self._scratchpad_store.discard_task(
            tenant_id=task.tenant_id,
            workspace_id=task.project_id,
            task_id=task.id,
        )

    def _reserve(self, policy, **bindings):
        try:
            self.policies.reserve(policy, **bindings)
        except ValueError as exc:
            raise MemoryAccessError("memory_policy_admission_denied") from exc
        except Exception as exc:
            raise MemoryAccessError("memory_policy_unavailable") from exc

    def _revoke(self, actor, run_id, *, namespace=None):
        try:
            self.policies.revoke(actor=actor, run_id=run_id)
        except Exception as exc:
            # Deny optional memory on backend failure; never expose SQL diagnostics.
            raise MemoryAccessError("memory_policy_unavailable") from exc
        finally:
            if namespace is not None:
                self.scratchpad.discard_run(namespace)

    def _allow_read(self, policy, task):
        extensions = self._deps.extensions()
        if self._deps.request_state() is None:
            return False
        from agent.auth import get_authenticated_source_control_principal
        from agent.services.organization_membership_service import OrganizationMembershipService
        from agent.services.task_read_access_service import get_task_read_access_service

        principal = get_authenticated_source_control_principal()
        if principal.subject_id != policy.actor.subject:
            return False
        access = extensions.get("task_read_access_service") or get_task_read_access_service()
        access.require(
            task=task,
            principal=principal,
            project_access=extensions.get("project_access_authority"),
            organization_membership=extensions.get("organization_membership_service")
            or OrganizationMembershipService(),
        )
        if any(grant.scope == MemoryScope.goal for grant in policy.capabilities):
            with Session(self.engine) as session:
                goal = session.get(GoalDB, policy.namespace.goal_id)
            if not self._allow_goal(goal, policy.namespace):
                return False
        return True

    def _allow_goal(self, goal, namespace):
        if goal is None or (goal.tenant_id, goal.project_id, goal.id) != (
            namespace.tenant_id,
            namespace.workspace_id,
            namespace.goal_id,
        ):
            return False
        from agent.services.goal_service import get_goal_service

        state = self._deps.request_state() or {}
        return get_goal_service().can_access_goal(goal, dict(state.get("user") or {}), bool(state.get("is_admin")))

    @contextmanager
    def worker_result(self, task_id, worker_job_id):
        """Authorize result admission before the actual dispatch lease is terminalized."""
        service_identity = self._deps.request_state()
        if service_identity is None:
            raise MemoryAccessError("memory_run_context_required")
        with Session(self.engine) as session:
            task = session.get(TaskDB, task_id)
            job = session.get(WorkerJobDB, worker_job_id) if worker_job_id else None
            lease = session.get(WorkerSlotLeaseDB, job.slot_lease_id) if job and job.slot_lease_id else None
            if not task or not job or not lease or not task.tenant_id or not task.project_id or not lease.worker_id:
                raise MemoryAccessError("memory_dispatch_binding_required")
        authenticated_worker = (service_identity.get("service_identity") or {}).get("worker_id")
        if authenticated_worker != lease.worker_id:
            from agent.auth import get_authenticated_source_control_principal
            from agent.services.project_access_authority import ProjectCapability

            principal = get_authenticated_source_control_principal()
            if not principal.is_admin or not principal.subject_id:
                raise MemoryAccessError("memory_worker_actor_mismatch")
            if principal.tenant_id and principal.tenant_id != task.tenant_id:
                raise MemoryAccessError("memory_worker_actor_mismatch")
            if principal.project_id and principal.project_id != task.project_id:
                raise MemoryAccessError("memory_worker_actor_mismatch")
            authority = self._deps.extensions().get("project_access_authority")
            if authority is None:
                raise MemoryAccessError("memory_policy_unavailable")
            from agent.services.project_access_authority import ProjectAccessError

            try:
                authority.require(
                    tenant_id=task.tenant_id,
                    project_id=task.project_id,
                    subject_id=principal.subject_id,
                    capability=ProjectCapability.WRITE,
                    tenant_admin=True,
                )
            except ProjectAccessError as exc:
                raise MemoryAccessError("memory_worker_actor_mismatch") from exc
        actor = MemoryActor(task.tenant_id, lease.worker_id)
        namespace = MemoryNamespace(
            task.tenant_id, task.project_id, task.goal_id, task.id, "memory-session-" + uuid.uuid4().hex
        )
        expiry = min(self.clock() + 60, lease.deadline_at)
        capability = MemoryCapability(
            "hub-result-memory",
            namespace,
            MemoryScope.task,
            (MemoryOperation.append,),
            MemorySensitivity.internal,
            expiry,
        )
        policy = MemoryRunPolicy(actor, namespace, "hub-memory-result-v1", expiry, capabilities=(capability,))
        self._reserve(policy, worker_job_id=job.id, dispatch_lease_id=lease.id)
        outcome = "failed"
        before_captured = False
        try:
            self.snapshots.capture(policy, "before")
            before_captured = True
            with bind_memory_access(MemoryAccessContext(actor, namespace.run_id, self.gateway)) as context:
                yield context
                outcome = "completed"
        finally:
            try:
                if before_captured:
                    self.snapshots.capture(policy, "after", scope_outcome=outcome)
            finally:
                self._revoke(actor, namespace.run_id, namespace=namespace)

    @contextmanager
    def request_read(self, task_id):
        """Only authenticated Hub request authority, reusing current Task/project ACLs."""
        extensions = self._deps.extensions()
        if self._deps.request_state() is None:
            raise MemoryAccessError("memory_run_context_required")
        from agent.auth import get_authenticated_source_control_principal
        from agent.services.organization_membership_service import OrganizationMembershipService
        from agent.services.task_read_access_service import get_task_read_access_service

        principal = get_authenticated_source_control_principal()
        with Session(self.engine) as session:
            task = session.get(TaskDB, task_id)
            if task is None or not task.tenant_id or not task.project_id:
                raise MemoryAccessError("memory_namespace_unclassified")
            payload = task.model_dump()
            goal = session.get(GoalDB, task.goal_id) if task.goal_id else None
        access = extensions.get("task_read_access_service") or get_task_read_access_service()
        access.require(
            task=payload,
            principal=principal,
            project_access=extensions.get("project_access_authority"),
            organization_membership=extensions.get("organization_membership_service")
            or OrganizationMembershipService(),
        )
        actor = MemoryActor(task.tenant_id, principal.subject_id)
        # Hub domain session identifier, never SRC_*/RUN_* or production evidence.
        namespace = MemoryNamespace(
            task.tenant_id, task.project_id, task.goal_id, task.id, "memory-session-" + uuid.uuid4().hex
        )
        expiry = self.clock() + 60
        scopes = (MemoryScope.task, MemoryScope.goal) if self._allow_goal(goal, namespace) else (MemoryScope.task,)
        capabilities = tuple(
            MemoryCapability(
                "hub-result-memory",
                namespace,
                scope,
                (MemoryOperation.read,),
                MemorySensitivity.internal,
                expiry,
            )
            for scope in scopes
        )
        policy = MemoryRunPolicy(actor, namespace, "hub-memory-read-v1", expiry, capabilities=capabilities)
        self._reserve(policy)
        try:
            with bind_memory_access(MemoryAccessContext(actor, namespace.run_id, self.gateway)) as context:
                yield context
        finally:
            self._revoke(actor, namespace.run_id, namespace=namespace)


def discard_terminal_task_scratchpad(task):
    """Hub post-commit adapter; no runtime allocation for tasks without scratchpads."""
    if PRODUCTION_MEMORY_RUNTIME_DEPENDENCIES.extensions().get("hub_memory_runtime") is None:
        return 0
    try:
        return get_memory_runtime().discard_task_scratchpad(task)
    except Exception:
        # Terminal SQL binding denies Worker grants even if local cleanup fails.
        # TTL/reaping bounds retention; never expose content or backend diagnostics.
        logging.getLogger(__name__).warning("Hub scratchpad cleanup failed for terminal task %s", task.id)
        return 0


def get_memory_runtime():
    extensions = PRODUCTION_MEMORY_RUNTIME_DEPENDENCIES.extensions()
    if not extensions:
        raise MemoryAccessError("memory_hub_context_required")
    role = PRODUCTION_MEMORY_RUNTIME_DEPENDENCIES.role()
    if str(role or "").strip().lower() != "hub":
        raise MemoryAccessError("memory_hub_context_required")
    runtime = extensions.get("hub_memory_runtime")
    if runtime is None:
        from agent.database import engine

        try:
            runtime = HubMemoryRuntime(engine)
        except Exception as exc:
            raise MemoryAccessError("memory_policy_unavailable") from exc
        extensions["hub_memory_runtime"] = runtime
    return runtime


class CurrentMemoryGateway:
    """Late composition preserves test DI without creating a legacy allow path."""

    def authorize_current(self, **request):
        try:
            context = current_memory_access()
        except MemoryAccessError:
            gateway = get_memory_runtime().gateway
        else:
            gateway = context.gateway
        return gateway.authorize_current(**request)


@contextmanager
def memory_read_context(task_id):
    try:
        context = current_memory_access()
    except MemoryAccessError:
        with get_memory_runtime().request_read(task_id) as context:
            yield context
    else:
        yield context


@contextmanager
def memory_result_context(task_id, worker_job_id):
    try:
        context = current_memory_access()
    except MemoryAccessError:
        with get_memory_runtime().worker_result(task_id, worker_job_id) as context:
            yield context
    else:
        yield context
