"""Hub-owned task decomposition after bounded model fallback exhaustion.

Workers only report ``model_recovery_signal.v1`` facts.  This service is the
control-plane boundary that may turn those facts into a persisted draft plan,
request an exact Hub policy decision, and materialize the approved nodes once.

The service is the composition root of the recovery saga. The saga steps
(``RecoveryApprovalSaga``, ``RecoveryProposal``, ``RecoveryRelease``,
``RecoveryApprovalDecision``) receive only narrow ports (see
``task_recovery_ports``) built from this service's constructor providers; the
pure rules live in ``task_recovery_planning_rules`` and
``task_recovery_signal_context``. Every historic method name stays available
as a thin delegator or alias.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
from typing import Any, Callable

from agent.config import settings
from agent.services.approval_auto_grant_policy import (  # noqa: F401 - re-exported for approval/run-control callers
    RECOVERY_MATERIALIZE_TOOL,
)
from agent.services.task_recovery_approval_decision import RecoveryApprovalDecision
from agent.services.task_recovery_approval_saga import RecoveryApprovalSaga
from agent.services.task_recovery_planning_rules import (  # noqa: F401 - re-exported compatibility names
    _RECOVERY_ACTIONS,
    _TERMINAL_GOAL_STATUSES,
    _TERMINAL_TASK_STATUSES,
    RECOVERY_SIGNAL_SCHEMA,
    RECOVERY_STATE_SCHEMA,
    _record_recovery_cut,
    _recovery_context_chars,
    active_plan_for_source,
    existing_plan,
    is_terminal,
    plan_action_configured,
    plan_digest,
    record_recovery_audit,
    reject_plan,
    stored_team_binding_matches,
    task_recovery_depth,
    team_binding_matches,
)
from agent.services.task_recovery_planning_values import (
    mapping as _mapping,
)
from agent.services.task_recovery_planning_values import (
    sha256_json as _sha256_json,
)
from agent.services.task_recovery_ports import RecoveryLocks
from agent.services.task_recovery_proposal import RecoveryProposal
from agent.services.task_recovery_release import (
    DispatchGateChildCanceller,
    RecoveryChildCanceller,
    RecoveryRelease,
    StatusCasChildCanceller,
    release_epoch,
)
from agent.services.task_recovery_signal_context import (
    compact_recovery_context,
    summarize_exhaustion_signal,
)

log = logging.getLogger(__name__)


class TaskRecoveryPlanningService:
    """Coordinate one bounded recovery plan without giving workers queue ownership."""

    def __init__(
        self,
        *,
        role_provider: Callable[[], str] | None = None,
        repository_provider: Callable[[], Any] | None = None,
        planner_provider: Callable[[], Any] | None = None,
        approval_service_provider: Callable[[], Any] | None = None,
        planning_service_provider: Callable[[], Any] | None = None,
        routing_policy_provider: Callable[[], dict[str, Any]] | None = None,
        task_status_updater: Callable[..., None] | None = None,
    ) -> None:
        self._role_provider = role_provider or (lambda: str(settings.role or ""))
        self._repository_provider = repository_provider
        self._planner_provider = planner_provider
        self._approval_service_provider = approval_service_provider
        self._planning_service_provider = planning_service_provider
        self._routing_policy_provider = routing_policy_provider
        self._task_status_updater = task_status_updater
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _lock_for(self, key: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(str(key), threading.RLock())

    def _source_mutation_lock(self, task_id: str):
        # `_distributed_source_lock` is the combined local/distributed port.
        # Keep this compatibility layer so injected tests and older call sites
        # retain their shape without acquiring the locks in reverse order.
        return contextlib.nullcontext()

    @contextlib.contextmanager
    def _distributed_advisory_lock(
        self,
        *,
        namespace: str,
        key: str,
    ):
        """Use a namespaced PostgreSQL advisory lock across Hub processes."""

        if self._repository_provider is not None:
            yield True
            return
        try:
            from sqlalchemy import text

            from agent.database import engine
        except Exception:
            log.exception(
                "distributed %s lock setup failed",
                namespace,
            )
            yield False
            return

        if str(engine.dialect.name or "").lower() != "postgresql":
            yield True
            return

        connection = None
        try:
            lock_id = int(
                hashlib.sha256(f"{namespace}:{key}".encode("utf-8")).hexdigest()[:15],
                16,
            )
            connection = engine.connect()
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                ).scalar()
            )
        except Exception:
            if connection is not None:
                connection.close()
            log.exception(
                "distributed %s lock acquisition failed",
                namespace,
            )
            yield False
            return

        try:
            yield acquired
        finally:
            try:
                if acquired:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": lock_id},
                    )
            finally:
                if connection is not None:
                    connection.close()

    def _distributed_recovery_lock(self, recovery_key: str):
        return self._distributed_advisory_lock(
            namespace="task-recovery-key",
            key=recovery_key,
        )

    def _distributed_source_lock(self, source_task_id: str):
        if self._repository_provider is not None:
            return contextlib.nullcontext(True)
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port().mutation_lock(
            source_task_id
        )

    def _distributed_task_locks(
        self,
        task_ids: set[str] | list[str] | tuple[str, ...],
    ):
        if self._repository_provider is not None:
            return contextlib.nullcontext(True)
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port().mutation_locks(
            set(task_ids)
        )

    @contextlib.contextmanager
    def _plan_mutation_lock(self, plan_id: str):
        planning_service = self._planning_service()
        mutation_lock = getattr(
            planning_service,
            "plan_mutation_lock",
            None,
        )
        if callable(mutation_lock):
            with mutation_lock(plan_id) as acquired:
                yield bool(acquired)
            return
        with self._lock_for(f"plan-mutation:{plan_id}"):
            yield True

    def _repos(self):
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry()

    def _planner(self):
        if self._planner_provider is not None:
            return self._planner_provider()
        from agent.services.goal_planning_recovery_service import (
            get_goal_planning_recovery_service,
        )

        return get_goal_planning_recovery_service()

    def _approval_service(self):
        if self._approval_service_provider is not None:
            return self._approval_service_provider()
        from agent.services.approval_request_service import get_approval_request_service

        return get_approval_request_service()

    def _planning_service(self):
        if self._planning_service_provider is not None:
            return self._planning_service_provider()
        from agent.services.planning_service import get_planning_service

        return get_planning_service()

    def _update_task(self, task_id: str, status: str, **values: Any) -> Any:
        if self._task_status_updater is not None:
            return self._task_status_updater(task_id, status, **values)
        from agent.services.task_runtime_service import update_local_task_status

        return update_local_task_status(task_id, status, **values)

    def _conditional_update_task(
        self,
        task_id: str,
        status: str,
        *,
        expected_statuses: set[str],
        **values: Any,
    ) -> bool:
        """Use the production row-CAS while keeping injected repositories testable."""

        normalized_expected = {
            str(value or "").strip().lower() for value in expected_statuses if str(value or "").strip()
        }
        if self._task_status_updater is None:
            from agent.services.task_runtime_service import (
                compare_and_set_local_task_status,
            )

            return compare_and_set_local_task_status(
                task_id,
                status,
                expected_statuses=normalized_expected,
                **values,
            )

        repos = self._repos()
        before = repos.task_repo.get_by_id(task_id)
        if before is not None and str(getattr(before, "status", "") or "").strip().lower() not in normalized_expected:
            return False
        result = self._update_task(task_id, status, **values)
        if result is False:
            return False
        after = repos.task_repo.get_by_id(task_id)
        if after is None:
            # Some focused test adapters record child transitions without
            # maintaining a task table.
            return True
        return str(getattr(after, "status", "") or "").strip().lower() == str(status or "").strip().lower()

    def _global_recovery_policy(self) -> dict[str, Any]:
        if self._routing_policy_provider is not None:
            return dict(self._routing_policy_provider() or {})
        from agent.services.model_invocation_service import ModelInvocationService

        return ModelInvocationService.get_context_recovery_policy()

    def _model_routing(self, task: Any) -> dict[str, Any]:
        task_routing: dict[str, Any] = {}
        recovery_strategies_explicit = False
        try:
            from agent.services.model_routing_contract import (
                extract_model_routing_from_task,
                has_model_routing_declaration,
            )

            recovery_strategies_explicit = has_model_routing_declaration(task)
            resolved = extract_model_routing_from_task(task)
            if resolved is not None:
                recovery_strategies_explicit = "context_recovery_strategies" in set(
                    getattr(resolved, "model_fields_set", set()) or set()
                )
                serializer = getattr(resolved, "as_metadata", None)
                if callable(serializer):
                    task_routing = dict(serializer())
                else:
                    serializer = getattr(resolved, "model_dump", None)
                    if callable(serializer):
                        task_routing = dict(serializer(exclude_none=True))
                    else:
                        task_routing = _mapping(resolved)
        except (ImportError, ValueError, TypeError):
            pass

        global_policy = self._global_recovery_policy()
        if not recovery_strategies_explicit:
            task_routing["context_recovery_strategies"] = list(global_policy.get("context_recovery_strategies") or [])
            task_routing["require_approval_for_generated_plan"] = bool(
                global_policy.get("require_approval_for_generated_plan", True)
            )
        return task_routing

    # Pure rules live in ``task_recovery_planning_rules`` /
    # ``task_recovery_signal_context``; the historic private names stay as
    # aliases for callers and tests.
    _safe_signal_summary = staticmethod(summarize_exhaustion_signal)
    _task_recovery_depth = staticmethod(task_recovery_depth)
    _existing_plan = staticmethod(existing_plan)
    _active_plan_for_source = staticmethod(active_plan_for_source)
    _plan_digest = staticmethod(plan_digest)
    _plan_action_configured = staticmethod(plan_action_configured)
    _is_terminal = staticmethod(is_terminal)
    _team_binding_matches = staticmethod(team_binding_matches)
    _stored_team_binding_matches = staticmethod(stored_team_binding_matches)
    _reject_plan = staticmethod(reject_plan)
    _compacted_context = staticmethod(compact_recovery_context)
    _audit = staticmethod(record_recovery_audit)
    _release_epoch = staticmethod(release_epoch)

    def _policy_binding(self, task: Any) -> tuple[list[str], bool, str]:
        routing = self._model_routing(task)
        actions = [
            str(value).strip()
            for value in list(routing.get("context_recovery_strategies") or [])
            if str(value).strip() in _RECOVERY_ACTIONS
        ]
        approval_required = bool(routing.get("require_approval_for_generated_plan", True))
        policy_hash = _sha256_json(
            {
                "actions": actions,
                "require_approval_for_generated_plan": approval_required,
            }
        )
        return actions, approval_required, policy_hash

    def resolve_recovery_policy(
        self,
        task: Any,
    ) -> tuple[list[str], bool, str]:
        """Expose the bounded policy through the strategy-executor port."""
        return self._policy_binding(task)

    def summarize_exhaustion_signal(
        self,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Expose the verified exhaustion fact through a narrow port."""
        return self._safe_signal_summary(strategy_failures)

    def compact_context_for_recovery(
        self,
        task: Any,
        *,
        actions: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build a bounded planning/review context without persisting input."""
        return self._compacted_context(task, actions=actions)

    # -- Composition of the saga steps -------------------------------------
    # The service is the composition root. It adapts its constructor
    # providers (production defaults when omitted) into the narrow ports of
    # each step. Steps are composed per call from bound methods, so a
    # replaced compatibility hook on this instance (e.g. a test double of
    # ``_conditional_update_task``) is the collaborator the steps receive.

    def _recovery_locks(self) -> RecoveryLocks:
        return RecoveryLocks(
            lock_for=self._lock_for,
            source_mutation_lock=self._source_mutation_lock,
            distributed_source_lock=self._distributed_source_lock,
            distributed_recovery_lock=self._distributed_recovery_lock,
            distributed_task_locks=self._distributed_task_locks,
            plan_mutation_lock=self._plan_mutation_lock,
        )

    def _child_canceller(self) -> RecoveryChildCanceller:
        if self._task_status_updater is None:
            return DispatchGateChildCanceller()
        return StatusCasChildCanceller(self._conditional_update_task)

    def _approval_saga(self) -> RecoveryApprovalSaga:
        return RecoveryApprovalSaga(
            locks=self._recovery_locks(),
            conditional_update=self._conditional_update_task,
            approval_service=self._approval_service,
        )

    def _release_step(self) -> RecoveryRelease:
        return RecoveryRelease(
            locks=self._recovery_locks(),
            conditional_update=self._conditional_update_task,
            child_canceller=self._child_canceller(),
        )

    def _proposal_step(self) -> RecoveryProposal:
        return RecoveryProposal(
            locks=self._recovery_locks(),
            saga=self._approval_saga(),
            role=self._role_provider,
            repositories=self._repos,
            planner=self._planner,
            approval_service=self._approval_service,
            policy_binding=self._policy_binding,
            audit=self._audit,
        )

    def _approval_decision_step(self) -> RecoveryApprovalDecision:
        return RecoveryApprovalDecision(
            locks=self._recovery_locks(),
            conditional_update=self._conditional_update_task,
            saga=self._approval_saga(),
            release=self._release_step(),
            role=self._role_provider,
            repositories=self._repos,
            approval_service=self._approval_service,
            planner=self._planner,
            planning_service=self._planning_service,
            policy_binding=self._policy_binding,
            audit=self._audit,
        )

    # -- Compatibility delegators -------------------------------------------

    def _request_materialization_approval(
        self,
        *,
        repos: Any,
        plan: Any,
        nodes: list[Any],
        goal_id: str,
        source_task_id: str,
        recovery_key: str,
        policy_hash: str,
        team_id: str,
    ) -> tuple[Any, str]:
        return self._approval_saga().request_materialization_approval(
            repos=repos, plan=plan, nodes=nodes, goal_id=goal_id, source_task_id=source_task_id,
            recovery_key=recovery_key, policy_hash=policy_hash, team_id=team_id,
        )

    def _mark_source_waiting_for_approval(
        self,
        *,
        source_task: Any,
        plan_id: str,
        approval_request_id: str,
        recovery_key: str,
        node_count: int,
        team_id: str,
    ) -> bool:
        return self._approval_saga().mark_source_waiting_for_approval(
            source_task=source_task, plan_id=plan_id, approval_request_id=approval_request_id,
            recovery_key=recovery_key, node_count=node_count, team_id=team_id,
        )

    def _complete_approval_refresh_saga(
        self,
        *,
        repos: Any,
        plan_id: str,
        goal_id: str,
        source_task_id: str,
        recovery_key: str,
        team_id: str,
        node_count: int,
        stale_approval_id: str,
        refreshed_approval_id: str,
        refreshed_digest: str,
    ) -> dict[str, Any]:
        return self._approval_saga().complete_approval_refresh_saga(
            repos=repos, plan_id=plan_id, goal_id=goal_id, source_task_id=source_task_id,
            recovery_key=recovery_key, team_id=team_id, node_count=node_count,
            stale_approval_id=stale_approval_id, refreshed_approval_id=refreshed_approval_id,
            refreshed_digest=refreshed_digest,
        )

    def _refresh_stale_plan_approval(
        self,
        *,
        repos: Any,
        plan_id: str,
        goal_id: str,
        source_task_id: str,
        recovery_key: str,
        policy_hash: str,
        team_id: str,
        stale_approval_id: str,
    ) -> dict[str, Any]:
        return self._approval_saga().refresh_stale_plan_approval(
            repos=repos, plan_id=plan_id, goal_id=goal_id, source_task_id=source_task_id,
            recovery_key=recovery_key, policy_hash=policy_hash, team_id=team_id,
            stale_approval_id=stale_approval_id,
        )

    def _resume_existing_plan_saga(
        self,
        *,
        repos: Any,
        plan: Any,
        source_task: Any,
        goal: Any,
        goal_id: str,
        source_task_id: str,
        recovery_key: str,
        policy_hash: str,
        team_id: str,
    ) -> dict[str, Any]:
        return self._proposal_step().resume_existing_plan_saga(
            repos=repos, plan=plan, source_task=source_task, goal=goal, goal_id=goal_id,
            source_task_id=source_task_id, recovery_key=recovery_key, policy_hash=policy_hash,
            team_id=team_id,
        )

    def _save_release_state(
        self,
        *,
        repos: Any,
        plan_id: str,
        state: str,
        release_epoch: str | None = None,
        release_details: dict[str, Any] | None = None,
    ) -> bool:
        return self._release_step().save_release_state(
            repos=repos, plan_id=plan_id, state=state, release_epoch=release_epoch,
            release_details=release_details,
        )

    def _cancel_recovery_children(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None:
        return self._release_step().cancel_recovery_children(
            created_task_ids=created_task_ids, plan_id=plan_id, source_task_id=source_task_id,
        )

    def _release_materialized_recovery(
        self,
        *,
        repos: Any,
        plan: Any,
        nodes: list[Any],
        source_task_id: str,
        goal_id: str,
        approval_id: str,
        recovery_key: str,
        team_id: str,
        created_task_ids: list[str],
    ) -> dict[str, Any]:
        return self._release_step().release_materialized_recovery(
            repos=repos, plan=plan, nodes=nodes, source_task_id=source_task_id, goal_id=goal_id,
            approval_id=approval_id, recovery_key=recovery_key, team_id=team_id,
            created_task_ids=created_task_ids,
        )

    def propose_after_model_exhaustion(
        self,
        *,
        task: Any,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Persist one approval-gated draft for an eligible exhausted task."""
        return self._proposal_step().propose_after_model_exhaustion(
            task=task, strategy_failures=strategy_failures,
        )

    def handle_approval_decision(self, approval: Any) -> dict[str, Any]:
        """Apply a recovery approval decision; no other approval tool is handled."""
        return self._approval_decision_step().handle_approval_decision(approval)


_service = TaskRecoveryPlanningService()


def get_task_recovery_planning_service() -> TaskRecoveryPlanningService:
    return _service
