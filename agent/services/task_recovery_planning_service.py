"""Hub-owned task decomposition after bounded model fallback exhaustion.

Workers only report ``model_recovery_signal.v1`` facts.  This service is the
control-plane boundary that may turn those facts into a persisted draft plan,
request an exact Hub policy decision, and materialize the approved nodes once.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable

from agent.config import settings
from agent.services.approval_auto_grant_policy import (
    RECOVERY_MATERIALIZE_TOOL,
)
from agent.services.recovery_plan_contract import (
    build_recovery_dependency_binding,
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
    calculate_recovery_task_payload_digest,
)
from agent.services.task_recovery_planning_values import (
    mapping as _mapping,
)
from agent.services.task_recovery_planning_values import (
    sha256_json as _sha256_json,
)
from agent.services.task_recovery_planning_values import (
    transitioned_recovery_strategy as _transitioned_recovery_strategy,
)

log = logging.getLogger(__name__)

RECOVERY_STATE_SCHEMA = "ananta.task_recovery_state.v1"
RECOVERY_SIGNAL_SCHEMA = "model_recovery_signal.v1"
_RECOVERY_ACTIONS = {
    "compact_context",
    "segment_planning",
    "propose_task_plan",
    "require_approval",
    "stop",
}
_TERMINAL_GOAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "aborted",
    "timeout",
    "archived",
}
_TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}


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

    @staticmethod
    def _safe_signal_summary(
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        failure_types: set[str] = set()
        error_types: set[str] = set()
        profile_ids: set[str] = set()
        model_ids: set[str] = set()
        attempt_count = 0
        structured_signal_seen = False
        non_recoverable_terminal_seen = False

        from ananta_contracts.model_recovery import (
            NON_RECOVERABLE_TERMINAL_REASONS,
            is_recoverable_model_error_type,
            sanitize_terminal_model_recovery_signal,
        )

        for failure in list(strategy_failures or []):
            if not isinstance(failure, dict):
                continue
            failure_type = str(failure.get("failure_type") or "").strip().lower()
            if failure_type:
                failure_types.add(failure_type)
                if failure_type in NON_RECOVERABLE_TERMINAL_REASONS:
                    non_recoverable_terminal_seen = True
            model_id = str(failure.get("model") or "").strip()
            if model_id:
                model_ids.add(model_id[:160])
            metadata = failure.get("metadata") if isinstance(failure.get("metadata"), dict) else {}
            fallback_decisions = failure.get("fallback_decisions")
            if not isinstance(fallback_decisions, list):
                fallback_decisions = metadata.get("fallback_decisions")
            for decision in fallback_decisions if isinstance(fallback_decisions, list) else []:
                if not isinstance(decision, dict) or not bool(decision.get("terminal")):
                    continue
                trigger = str(decision.get("trigger") or "").strip().lower()
                if not is_recoverable_model_error_type(trigger):
                    non_recoverable_terminal_seen = True
            signal = failure.get("model_recovery_signal")
            if not isinstance(signal, dict):
                signal = metadata.get("model_recovery_signal")
            if not isinstance(signal, dict):
                continue
            raw_terminal_reason = str(signal.get("terminal_reason") or "").strip().lower()
            if not is_recoverable_model_error_type(raw_terminal_reason):
                non_recoverable_terminal_seen = True

            signal = sanitize_terminal_model_recovery_signal(signal)
            if signal is None:
                non_recoverable_terminal_seen = True
                continue
            structured_signal_seen = True
            attempt_count += max(0, int(signal.get("attempt_count") or 0))
            for value in list(signal.get("error_types") or []):
                normalized = str(value or "").strip().lower()
                if normalized:
                    error_types.add(normalized[:80])
            for value in list(signal.get("failed_profile_ids") or []):
                normalized = str(value or "").strip()
                if normalized:
                    profile_ids.add(normalized[:160])
            reason_code = str(signal.get("reason_code") or "").strip().lower()
            if reason_code:
                failure_types.add(reason_code[:80])
            terminal_reason = str(signal.get("terminal_reason") or "").strip().lower()
            if terminal_reason:
                failure_types.add(terminal_reason[:80])

        if non_recoverable_terminal_seen or not structured_signal_seen:
            return None
        return {
            "schema": RECOVERY_SIGNAL_SCHEMA,
            "reason_code": "model_or_strategy_exhausted",
            "terminal": True,
            "attempt_count": max(attempt_count, len(list(strategy_failures or []))),
            "failure_types": sorted(failure_types),
            "error_types": sorted(error_types),
            "failed_profile_ids": sorted(profile_ids),
            "failed_models": sorted(model_ids),
        }

    @staticmethod
    def _task_recovery_depth(task: Any) -> int:
        data = _mapping(task)
        reason = str(data.get("derivation_reason") or "").strip().lower()
        if reason == "goal_task_recovery":
            return 1
        details = _mapping(data.get("status_reason_details"))
        state = _mapping(details.get("model_recovery"))
        return max(0, int(state.get("recovery_depth") or 0))

    @staticmethod
    def _existing_plan(repos: Any, *, goal_id: str, recovery_key: str):
        for plan in list(repos.plan_repo.get_by_goal_id(goal_id) or []):
            if str(_mapping(getattr(plan, "rationale", None)).get("recovery_key") or "") == recovery_key:
                return plan
        return None

    @staticmethod
    def _active_plan_for_source(
        repos: Any,
        *,
        goal_id: str,
        source_task_id: str,
        exclude_plan_id: str | None = None,
    ):
        active_statuses = {
            "draft",
            "pending_approval",
            "approved",
            "materialized",
        }
        for plan in list(repos.plan_repo.get_by_goal_id(goal_id) or []):
            if exclude_plan_id and str(getattr(plan, "id", "") or "") == str(exclude_plan_id):
                continue
            rationale = _mapping(getattr(plan, "rationale", None))
            if (
                str(rationale.get("source_task_id") or "") == source_task_id
                and str(getattr(plan, "status", "") or "").strip().lower() in active_statuses
            ):
                return plan
        return None

    @staticmethod
    def _plan_digest(plan: Any, nodes: list[Any]) -> str:
        return calculate_recovery_plan_digest(plan, nodes)

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

    @staticmethod
    def _plan_action_configured(actions: list[str]) -> bool:
        return bool(
            {"segment_planning", "propose_task_plan"}.intersection(
                actions
            )
        )

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

    @staticmethod
    def _is_terminal(record: Any, terminal_statuses: set[str]) -> bool:
        return str(getattr(record, "status", "") or "").strip().lower() in terminal_statuses

    @staticmethod
    def _team_binding_matches(
        *,
        source_task: Any,
        goal: Any,
        expected_team_id: str,
    ) -> bool:
        normalized_expected = str(expected_team_id or "").strip()
        return (
            str(getattr(source_task, "team_id", "") or "").strip()
            == normalized_expected
            and str(getattr(goal, "team_id", "") or "").strip()
            == normalized_expected
        )

    @staticmethod
    def _stored_team_binding_matches(
        stored: dict[str, Any],
        expected_team_id: str,
    ) -> bool:
        """Require an explicit persisted binding, including unscoped goals."""
        return (
            "team_id" in stored
            and str(stored.get("team_id") or "").strip()
            == str(expected_team_id or "").strip()
        )

    @staticmethod
    def _reject_plan(
        repos: Any,
        plan: Any,
        *,
        reason_code: str,
    ) -> None:
        plan.status = "rejected"
        plan.rationale = {
            **_mapping(getattr(plan, "rationale", None)),
            "approval_state": "stopped",
            "recovery_stop_reason": str(reason_code)[:160],
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)

    @staticmethod
    def _compacted_context(task: Any, *, actions: list[str]) -> tuple[str, dict[str, Any]]:
        data = _mapping(task)
        title = str(data.get("title") or "").strip()
        description = str(data.get("description") or "").strip()
        execution_context = _mapping(data.get("worker_execution_context"))
        context_data = _mapping(execution_context.get("context"))
        context_text = str(context_data.get("context_text") or "")
        if "compact_context" not in actions:
            bounded = "\n".join(value for value in (title, description, context_text) if value)
            return bounded[:8000], {
                "status": "bounded_without_compactor",
                "input_chars": len(title) + len(description) + len(context_text),
                "output_chars": min(8000, len(bounded)),
            }

        from agent.services.planning_context_compactor_service import (
            get_planning_context_compactor_service,
        )
        from agent.services.propose_policy import ProposePolicy

        compacted = get_planning_context_compactor_service().compact(
            goal_text=title or description or "Recover delegated task",
            context_text="\n".join(value for value in (description, context_text) if value),
            mode="generic",
            mode_data={"recovery": True, "segment_planning": "segment_planning" in actions},
            planning_policy={},
            llm_config={},
            policy=ProposePolicy(
                context_compaction_enabled=False,
                context_compaction_required=False,
                context_compactor_max_output_chars=8000,
                context_compactor_retry_attempts=0,
                context_compactor_fail_open=True,
            ),
        )
        payload = dict(compacted.payload or {})
        payload.pop("compactor_meta", None)
        return json.dumps(payload, ensure_ascii=False)[:8000], {
            key: value
            for key, value in dict(compacted.meta or {}).items()
            if key
            in {
                "input_chars",
                "output_chars",
                "reduction_ratio",
                "truncated_fields",
                "status",
                "error_classification",
                "fallback_stage",
            }
        }

    @staticmethod
    def _audit(action: str, details: dict[str, Any]) -> None:
        try:
            from agent.common.audit import log_audit

            log_audit(action, details)
        except Exception:
            log.debug("task recovery audit failed", exc_info=True)

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
        goal = repos.goal_repo.get_by_id(goal_id)
        if goal is None:
            raise RuntimeError("recovery_goal_not_found")
        materialization_inputs_digest = (
            calculate_recovery_materialization_inputs_digest(
                goal
            )
        )
        plan.rationale = {
            **_mapping(getattr(plan, "rationale", None)),
            "materialization_inputs_digest": (
                materialization_inputs_digest
            ),
        }
        plan.updated_at = time.time()
        plan = repos.plan_repo.save(plan)
        plan_digest = self._plan_digest(plan, nodes)
        approval = self._approval_service().create_pending_request(
            task_id=source_task_id,
            goal_id=goal_id,
            tenant_id=str(getattr(goal, "tenant_id", "") or "").strip()
            or None,
            project_id=str(getattr(goal, "project_id", "") or "").strip()
            or None,
            organization_id=str(
                getattr(goal, "organization_id", "") or ""
            ).strip()
            or None,
            trace_id=str(getattr(plan, "trace_id", "") or "") or None,
            tool_name=RECOVERY_MATERIALIZE_TOOL,
            arguments={
                "goal_id": goal_id,
                "plan_id": str(getattr(plan, "id", "") or ""),
                "plan_digest": plan_digest,
                "policy_hash": policy_hash,
                "recovery_key": recovery_key,
                "source_task_id": source_task_id,
                "team_id": team_id,
            },
            target_fingerprint=plan_digest,
            risk_class="task_materialization",
            governance_mode="balanced",
            scope={
                "approval_class": "task_materialization",
                "source": "model_context_recovery",
                "reason_code": "model_or_strategy_exhausted",
                "goal_id": goal_id,
                "plan_id": str(getattr(plan, "id", "") or ""),
                "source_task_id": source_task_id,
                "recovery_key": recovery_key,
                "team_id": team_id,
                "tenant_id": str(
                    getattr(goal, "tenant_id", "") or ""
                ).strip(),
                "project_id": str(
                    getattr(goal, "project_id", "") or ""
                ).strip(),
                "organization_id": str(
                    getattr(goal, "organization_id", "") or ""
                ).strip(),
            },
        )
        plan.rationale = {
            **_mapping(getattr(plan, "rationale", None)),
            "approval_request_id": approval.id,
            "plan_digest": plan_digest,
            "approval_state": str(approval.status or "pending"),
            "team_id": team_id,
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)
        return approval, plan_digest

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
        state = {
            "schema": RECOVERY_STATE_SCHEMA,
            "status": "pending_approval",
            "plan_id": plan_id,
            "approval_request_id": approval_request_id,
            "recovery_key": recovery_key,
            "recovery_depth": 1,
            "node_count": max(0, int(node_count)),
            "team_id": team_id,
        }
        current_status = str(getattr(source_task, "status", "") or "").strip().lower()
        if not current_status or current_status in _TERMINAL_TASK_STATUSES:
            return False
        task_id = str(getattr(source_task, "id", "") or "")
        current_state = _mapping(
            _mapping(
                getattr(
                    source_task,
                    "status_reason_details",
                    None,
                )
            ).get("model_recovery")
        )
        current_approval_id = str(
            current_state.get("approval_request_id") or ""
        ).strip()
        authority = contextlib.nullcontext()
        if (
            current_approval_id
            and current_approval_id != approval_request_id
        ):
            from agent.common.recovery_source_approval_rebind_write_boundary import (
                authorize_recovery_source_approval_rebind_write,
            )

            authority = (
                authorize_recovery_source_approval_rebind_write(
                    task_id=task_id,
                    current_state=current_state,
                    proposed_state=state,
                )
            )
        with authority:
            return self._conditional_update_task(
                task_id,
                "waiting_for_review",
                expected_statuses={current_status},
                force=False,
                verification_status={
                    **_mapping(
                        getattr(
                            source_task,
                            "verification_status",
                            None,
                        )
                    ),
                    "model_recovery": state,
                },
                status_reason_code=(
                    "model_recovery_plan_pending_approval"
                ),
                status_reason_details={
                    **_mapping(
                        getattr(
                            source_task,
                            "status_reason_details",
                            None,
                        )
                    ),
                    "model_recovery": state,
                },
                event_type=(
                    "task_recovery_plan_pending_approval"
                ),
                event_actor="hub_recovery_planner",
                event_details={
                    "plan_id": plan_id,
                    "approval_request_id": (
                        approval_request_id
                    ),
                },
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
        """Finish source rebind, stale-grant consume, and saga completion."""

        with (
            self._distributed_source_lock(
                source_task_id
            ) as source_lock_acquired,
            self._source_mutation_lock(source_task_id),
        ):
            if not source_lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "recovery_action_in_progress",
                    "plan_id": plan_id,
                }
            source_task = repos.task_repo.get_by_id(source_task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if (
                source_task is None
                or goal is None
                or self._is_terminal(
                    source_task,
                    _TERMINAL_TASK_STATUSES,
                )
                or self._is_terminal(
                    goal,
                    _TERMINAL_GOAL_STATUSES,
                )
                or not self._team_binding_matches(
                    source_task=source_task,
                    goal=goal,
                    expected_team_id=team_id,
                )
            ):
                return {
                    "status": "stopped",
                    "reason_code": (
                        "recovery_team_binding_changed"
                        if source_task is not None
                        and goal is not None
                        and not self._team_binding_matches(
                            source_task=source_task,
                            goal=goal,
                            expected_team_id=team_id,
                        )
                        else "recovery_refresh_owner_terminal"
                    ),
                    "plan_id": plan_id,
                }

            source_recovery = _mapping(
                _mapping(
                    getattr(
                        source_task,
                        "status_reason_details",
                        None,
                    )
                ).get("model_recovery")
            )
            current_approval_id = str(
                source_recovery.get("approval_request_id") or ""
            )
            source_owned = bool(
                str(source_recovery.get("plan_id") or "") == plan_id
                and str(source_recovery.get("recovery_key") or "")
                == recovery_key
                and self._stored_team_binding_matches(
                    source_recovery,
                    team_id,
                )
            )
            source_empty = not any(
                str(source_recovery.get(key) or "").strip()
                for key in (
                    "plan_id",
                    "recovery_key",
                    "approval_request_id",
                )
            )
            if (
                current_approval_id != refreshed_approval_id
                and not (
                    source_empty
                    or (
                        source_owned
                        and current_approval_id
                        == stale_approval_id
                    )
                )
            ):
                return {
                    "status": "failed",
                    "reason_code": (
                        "recovery_refresh_source_binding_conflict"
                    ),
                    "plan_id": plan_id,
                }
            if current_approval_id != refreshed_approval_id:
                transitioned = self._mark_source_waiting_for_approval(
                    source_task=source_task,
                    plan_id=plan_id,
                    approval_request_id=refreshed_approval_id,
                    recovery_key=recovery_key,
                    node_count=node_count,
                    team_id=team_id,
                )
                if not transitioned:
                    return {
                        "status": "failed",
                        "reason_code": (
                            "recovery_source_transition_conflict"
                        ),
                        "plan_id": plan_id,
                    }

            confirmed_source = repos.task_repo.get_by_id(
                source_task_id
            )
            confirmed_recovery = _mapping(
                _mapping(
                    getattr(
                        confirmed_source,
                        "status_reason_details",
                        None,
                    )
                ).get("model_recovery")
            )
            if not (
                confirmed_source is not None
                and str(
                    confirmed_recovery.get("plan_id") or ""
                )
                == plan_id
                and str(
                    confirmed_recovery.get(
                        "approval_request_id"
                    )
                    or ""
                )
                == refreshed_approval_id
                and str(
                    confirmed_recovery.get("recovery_key") or ""
                )
                == recovery_key
                and self._stored_team_binding_matches(
                    confirmed_recovery,
                    team_id,
                )
            ):
                return {
                    "status": "failed",
                    "reason_code": (
                        "recovery_refresh_source_confirmation_failed"
                    ),
                    "plan_id": plan_id,
                }

        with self._plan_mutation_lock(plan_id) as acquired:
            if not acquired:
                return {
                    "status": "ignored",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": plan_id,
                }
            plan = repos.plan_repo.get_by_id(plan_id)
            if plan is None:
                return {
                    "status": "failed",
                    "reason_code": "approval_target_not_found",
                }
            rationale = _mapping(getattr(plan, "rationale", None))
            saga = _mapping(rationale.get("approval_refresh"))
            if not (
                str(saga.get("stale_approval_request_id") or "")
                == stale_approval_id
                and str(
                    saga.get("refreshed_approval_request_id")
                    or ""
                )
                == refreshed_approval_id
                and str(
                    rationale.get("approval_request_id") or ""
                )
                == refreshed_approval_id
            ):
                return {
                    "status": "failed",
                    "reason_code": (
                        "recovery_refresh_plan_binding_conflict"
                    ),
                    "plan_id": plan_id,
                }
            plan.rationale = {
                **rationale,
                "approval_state": "refresh_source_bound",
                "approval_refresh": {
                    **saga,
                    "state": "source_bound",
                    "updated_at": time.time(),
                },
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)

        try:
            consumed = self._approval_service().consume_request(
                stale_approval_id
            )
        except Exception:
            log.exception(
                "stale recovery approval consume failed for %s",
                stale_approval_id,
            )
            consumed = None
        if consumed is None:
            return {
                "status": "failed",
                "reason_code": "approval_consume_failed",
                "plan_id": plan_id,
            }

        with self._plan_mutation_lock(plan_id) as acquired:
            if not acquired:
                return {
                    "status": "ignored",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": plan_id,
                }
            plan = repos.plan_repo.get_by_id(plan_id)
            if plan is None:
                return {
                    "status": "failed",
                    "reason_code": "approval_target_not_found",
                }
            rationale = _mapping(getattr(plan, "rationale", None))
            saga = _mapping(rationale.get("approval_refresh"))
            if (
                str(saga.get("stale_approval_request_id") or "")
                != stale_approval_id
                or str(
                    saga.get("refreshed_approval_request_id")
                    or ""
                )
                != refreshed_approval_id
            ):
                return {
                    "status": "failed",
                    "reason_code": (
                        "recovery_refresh_plan_binding_conflict"
                    ),
                    "plan_id": plan_id,
                }
            plan.status = "pending_approval"
            plan.rationale = {
                **rationale,
                "approval_state": "pending",
                "approval_refresh": {
                    **saga,
                    "state": "completed",
                    "updated_at": time.time(),
                },
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)

        return {
            "status": "pending_approval",
            "reason_code": "recovery_plan_digest_refreshed",
            "plan_id": plan_id,
            "approval_request_id": refreshed_approval_id,
            "plan_digest": refreshed_digest,
            "approval_status": "consumed",
        }

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
        """Create or resume the durable stale-digest replacement saga."""

        with self._plan_mutation_lock(plan_id) as acquired:
            if not acquired:
                return {
                    "status": "ignored",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": plan_id,
                }
            plan = repos.plan_repo.get_by_id(plan_id)
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
            if plan is None or not nodes:
                return {
                    "status": "failed",
                    "reason_code": "approval_target_not_found",
                }
            rationale = _mapping(getattr(plan, "rationale", None))
            current_digest = self._plan_digest(plan, nodes)
            saga = _mapping(rationale.get("approval_refresh"))
            saga_matches = (
                str(saga.get("stale_approval_request_id") or "")
                == stale_approval_id
                and self._stored_team_binding_matches(
                    saga,
                    team_id,
                )
            )
            refreshed_approval_id = (
                str(
                    saga.get("refreshed_approval_request_id")
                    or ""
                )
                if saga_matches
                else ""
            )
            refreshed_digest = (
                str(saga.get("refreshed_plan_digest") or "")
                if saga_matches
                else ""
            )
            if (
                saga_matches
                and refreshed_digest
                and refreshed_digest != current_digest
            ):
                return {
                    "status": "failed",
                    "reason_code": (
                        "recovery_refresh_plan_changed_again"
                    ),
                    "plan_id": plan_id,
                }
            if saga_matches and not refreshed_approval_id:
                candidate_id = str(
                    rationale.get("approval_request_id") or ""
                )
                if candidate_id != stale_approval_id:
                    get_request = getattr(
                        self._approval_service(),
                        "get_request",
                        None,
                    )
                    candidate = (
                        get_request(candidate_id)
                        if callable(get_request)
                        else None
                    )
                    candidate_args = _mapping(
                        getattr(
                            candidate,
                            "canonical_arguments",
                            None,
                        )
                    )
                    if (
                        candidate is not None
                        and str(
                            getattr(
                                candidate,
                                "target_fingerprint",
                                "",
                            )
                            or ""
                        )
                        == current_digest
                        and self._stored_team_binding_matches(
                            candidate_args,
                            team_id,
                        )
                    ):
                        refreshed_approval_id = candidate_id
                        refreshed_digest = current_digest

            if not saga_matches:
                plan.status = "pending_approval"
                plan.rationale = {
                    **rationale,
                    "approval_state": (
                        "refreshing_stale_plan_digest"
                    ),
                    "approval_refresh": {
                        "schema": (
                            "ananta.recovery_approval_refresh.v1"
                        ),
                        "state": "creating",
                        "stale_approval_request_id": (
                            stale_approval_id
                        ),
                        "refreshed_approval_request_id": "",
                        "refreshed_plan_digest": current_digest,
                        "team_id": team_id,
                        "updated_at": time.time(),
                    },
                }
                plan.updated_at = time.time()
                plan = repos.plan_repo.save(plan)
                refreshed_digest = current_digest

            if not refreshed_approval_id:
                refreshed, refreshed_digest = (
                    self._request_materialization_approval(
                        repos=repos,
                        plan=plan,
                        nodes=nodes,
                        goal_id=goal_id,
                        source_task_id=source_task_id,
                        recovery_key=recovery_key,
                        policy_hash=policy_hash,
                        team_id=team_id,
                    )
                )
                refreshed_approval_id = str(
                    getattr(refreshed, "id", "") or ""
                )

            plan = repos.plan_repo.get_by_id(plan_id)
            if plan is None:
                return {
                    "status": "failed",
                    "reason_code": "approval_target_not_found",
                }
            rationale = _mapping(getattr(plan, "rationale", None))
            saga = _mapping(rationale.get("approval_refresh"))
            plan.status = "pending_approval"
            plan.rationale = {
                **rationale,
                "approval_request_id": refreshed_approval_id,
                "plan_digest": refreshed_digest,
                "approval_state": "refresh_approval_bound",
                "approval_refresh": {
                    **saga,
                    "schema": (
                        "ananta.recovery_approval_refresh.v1"
                    ),
                    "state": "approval_bound",
                    "stale_approval_request_id": (
                        stale_approval_id
                    ),
                    "refreshed_approval_request_id": (
                        refreshed_approval_id
                    ),
                    "refreshed_plan_digest": refreshed_digest,
                    "team_id": team_id,
                    "updated_at": time.time(),
                },
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
            node_count = len(nodes)

        return self._complete_approval_refresh_saga(
            repos=repos,
            plan_id=plan_id,
            goal_id=goal_id,
            source_task_id=source_task_id,
            recovery_key=recovery_key,
            team_id=team_id,
            node_count=node_count,
            stale_approval_id=stale_approval_id,
            refreshed_approval_id=refreshed_approval_id,
            refreshed_digest=refreshed_digest,
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
        """Deterministically finish an interrupted plan/approval/source saga."""

        rationale = _mapping(getattr(plan, "rationale", None))
        plan_id = str(getattr(plan, "id", "") or "")
        plan_status = str(getattr(plan, "status", "") or "").strip().lower()
        if plan_status not in {
            "draft",
            "pending_approval",
            "approved",
        }:
            return {
                "status": plan_status or "failed",
                "reason_code": "recovery_plan_already_exists",
                "plan_id": plan_id,
                "approval_request_id": rationale.get("approval_request_id"),
                "recovery_key": recovery_key,
            }
        if (
            str(rationale.get("source_task_id") or source_task_id) != source_task_id
            or str(rationale.get("policy_hash") or policy_hash) != policy_hash
            or not self._stored_team_binding_matches(
                rationale,
                team_id,
            )
            or not self._team_binding_matches(
                source_task=source_task,
                goal=goal,
                expected_team_id=team_id,
            )
        ):
            return {
                "status": "failed",
                "reason_code": "recovery_plan_binding_conflict",
                "plan_id": plan_id,
            }
        approval_request_id = str(rationale.get("approval_request_id") or "").strip()
        source_recovery = _mapping(_mapping(getattr(source_task, "status_reason_details", None)).get("model_recovery"))
        source_binding_complete = bool(
            approval_request_id
            and str(source_recovery.get("plan_id") or "") == plan_id
            and str(source_recovery.get("approval_request_id") or "") == approval_request_id
            and str(source_recovery.get("recovery_key") or "") == recovery_key
        )
        if source_binding_complete:
            get_request = getattr(
                self._approval_service(),
                "get_request",
                None,
            )
            approval_exists = not callable(get_request) or get_request(approval_request_id) is not None
            if approval_exists:
                return {
                    "status": plan_status,
                    "reason_code": "recovery_plan_already_exists",
                    "plan_id": plan_id,
                    "approval_request_id": approval_request_id,
                    "recovery_key": recovery_key,
                }
        nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
        if not nodes:
            self._reject_plan(
                repos,
                plan,
                reason_code="recovery_plan_nodes_missing",
            )
            return {
                "status": "failed",
                "reason_code": "recovery_plan_nodes_missing",
                "plan_id": plan_id,
            }
        expected_node_count = max(
            0,
            int(rationale.get("node_count") or 0),
        )
        if expected_node_count and len(nodes) != expected_node_count:
            self._reject_plan(
                repos,
                plan,
                reason_code="recovery_plan_nodes_incomplete",
            )
            return {
                "status": "failed",
                "reason_code": "recovery_plan_nodes_incomplete",
                "plan_id": plan_id,
            }
        if self._is_terminal(source_task, _TERMINAL_TASK_STATUSES) or self._is_terminal(goal, _TERMINAL_GOAL_STATUSES):
            reason_code = (
                "recovery_source_terminal"
                if self._is_terminal(
                    source_task,
                    _TERMINAL_TASK_STATUSES,
                )
                else "recovery_goal_terminal"
            )
            self._reject_plan(repos, plan, reason_code=reason_code)
            return {"status": "stopped", "reason_code": reason_code}

        with self._plan_mutation_lock(plan_id) as plan_lock_acquired:
            if not plan_lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": plan_id,
                }
            plan = repos.plan_repo.get_by_id(plan_id)
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
            if plan is None or not nodes:
                return {
                    "status": "failed",
                    "reason_code": "recovery_plan_persistence_failed",
                }
            plan.status = "pending_approval"
            plan.planning_mode = "task_recovery"
            plan.rationale = {
                **_mapping(getattr(plan, "rationale", None)),
                "recovery_schema": RECOVERY_STATE_SCHEMA,
                "recovery_key": recovery_key,
                "source_task_id": source_task_id,
                "recovery_depth": 1,
                "policy_hash": policy_hash,
                "team_id": team_id,
                "approval_state": "pending",
            }
            plan.updated_at = time.time()
            plan = repos.plan_repo.save(plan)
            approval, plan_digest = self._request_materialization_approval(
                repos=repos,
                plan=plan,
                nodes=nodes,
                goal_id=goal_id,
                source_task_id=source_task_id,
                recovery_key=recovery_key,
                policy_hash=policy_hash,
                team_id=team_id,
            )

        source_task = repos.task_repo.get_by_id(source_task_id)
        if source_task is None or not self._mark_source_waiting_for_approval(
            source_task=source_task,
            plan_id=plan_id,
            approval_request_id=str(approval.id),
            recovery_key=recovery_key,
            node_count=len(nodes),
            team_id=team_id,
        ):
            return {
                "status": "failed",
                "reason_code": "recovery_source_transition_conflict",
                "plan_id": plan_id,
                "approval_request_id": str(approval.id),
            }
        return {
            "status": "pending_approval",
            "reason_code": "recovery_plan_saga_resumed",
            "plan_id": plan_id,
            "approval_request_id": str(approval.id),
            "plan_digest": plan_digest,
            "recovery_key": recovery_key,
            "node_count": len(nodes),
        }

    def _save_release_state(
        self,
        *,
        repos: Any,
        plan_id: str,
        state: str,
        release_epoch: str | None = None,
        release_details: dict[str, Any] | None = None,
    ) -> bool:
        with self._plan_mutation_lock(plan_id) as acquired:
            if not acquired:
                return False
            plan = repos.plan_repo.get_by_id(plan_id)
            if plan is None:
                return False
            rationale = _mapping(getattr(plan, "rationale", None))
            current_state = str(
                rationale.get("materialization_release_state") or ""
            )
            current_epoch = str(
                rationale.get("materialization_release_epoch") or ""
            )
            normalized_epoch = str(release_epoch or "")
            if current_state == "cancelled" and state != "cancelled":
                return False
            if (
                current_epoch
                and normalized_epoch
                and current_epoch != normalized_epoch
            ):
                return False
            if current_state == "completed" and state == "committed":
                return True
            plan.rationale = {
                **rationale,
                **dict(release_details or {}),
                "materialization_release_state": state,
                **(
                    {
                        "materialization_release_epoch": (
                            normalized_epoch
                        )
                    }
                    if normalized_epoch
                    else {}
                ),
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
            return True

    @staticmethod
    def _release_epoch(
        *,
        plan_id: str,
        approval_id: str,
        recovery_key: str,
        team_id: str,
    ) -> str:
        return _sha256_json(
            {
                "schema": "ananta.recovery_release_epoch.v1",
                "plan_id": plan_id,
                "approval_id": approval_id,
                "recovery_key": recovery_key,
                "team_id": team_id,
            }
        )

    def _cancel_recovery_children(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None:
        active_statuses = {
            "todo",
            "created",
            "assigned",
            "proposing",
            "in_progress",
            "delegated",
            "waiting_for_review",
            "needs_review",
            "blocked",
            "blocked_by_dependency",
            "paused",
            "updated",
        }
        if self._task_status_updater is None:
            from agent.services.recovery_dispatch_gate_service import (
                get_recovery_dispatch_gate_service,
            )

            recovery_gate = get_recovery_dispatch_gate_service()
            for child_task_id in created_task_ids:
                recovery_gate.invalidate_task(
                    child_task_id,
                    reason_code="recovery_parent_state_changed",
                )
            return
        for child_task_id in created_task_ids:
            self._conditional_update_task(
                child_task_id,
                "cancelled",
                expected_statuses=active_statuses,
                force=False,
                status_reason_code="recovery_parent_state_changed",
                event_type="task_recovery_child_cancelled",
                event_actor="hub_approval_dispatcher",
                event_details={
                    "plan_id": plan_id,
                    "source_task_id": source_task_id,
                },
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
        """Idempotently release a DAG only after a confirmed source CAS."""

        plan_id = str(getattr(plan, "id", "") or "")
        release_epoch = self._release_epoch(
            plan_id=plan_id,
            approval_id=approval_id,
            recovery_key=recovery_key,
            team_id=team_id,
        )
        with self._distributed_task_locks(
            {source_task_id, *created_task_ids}
        ) as source_lock_acquired:
            if not source_lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "recovery_action_in_progress",
                    "plan_id": plan_id,
                }
            with self._source_mutation_lock(source_task_id):
                latest_source = repos.task_repo.get_by_id(source_task_id)
                latest_goal = repos.goal_repo.get_by_id(goal_id)
                source_status = str(getattr(latest_source, "status", "") or "").strip().lower()
                source_terminal = latest_source is None or self._is_terminal(
                    latest_source,
                    _TERMINAL_TASK_STATUSES,
                )
                goal_terminal = latest_goal is None or self._is_terminal(
                    latest_goal,
                    _TERMINAL_GOAL_STATUSES,
                )
                team_binding_changed = not (
                    latest_source is not None
                    and latest_goal is not None
                    and self._team_binding_matches(
                        source_task=latest_source,
                        goal=latest_goal,
                        expected_team_id=team_id,
                    )
                )
                source_state_changed = not source_terminal and source_status not in {
                    "waiting_for_review",
                    "needs_review",
                    "blocked_by_dependency",
                }
                if (
                    source_terminal
                    or source_state_changed
                    or goal_terminal
                    or team_binding_changed
                ):
                    self._cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self._save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": (
                            "recovery_goal_became_terminal"
                            if goal_terminal
                            else (
                                "recovery_source_terminal"
                                if source_terminal
                                else (
                                    "recovery_team_binding_changed"
                                    if team_binding_changed
                                    else "recovery_source_state_changed"
                                )
                            )
                        ),
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }

                authoritative_plan = repos.plan_repo.get_by_id(plan_id)
                authoritative_rationale = _mapping(
                    getattr(authoritative_plan, "rationale", None)
                )
                release_state = str(
                    authoritative_rationale.get(
                        "materialization_release_state"
                    )
                    or ""
                )
                authoritative_epoch = str(
                    authoritative_rationale.get(
                        "materialization_release_epoch"
                    )
                    or ""
                )
                if release_state == "cancelled":
                    return {
                        "status": "materialized",
                        "reason_code": "recovery_release_cancelled",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }
                if release_state == "completed":
                    source_recovery = _mapping(
                        _mapping(
                            getattr(
                                latest_source,
                                "status_reason_details",
                                None,
                            )
                        ).get("model_recovery")
                    )
                    if (
                        source_status == "blocked_by_dependency"
                        and str(source_recovery.get("plan_id") or "") == plan_id
                        and str(source_recovery.get("approval_request_id") or "") == approval_id
                        and (
                            not authoritative_epoch
                            or (
                                authoritative_epoch
                                == release_epoch
                                == str(
                                    source_recovery.get(
                                        "release_epoch"
                                    )
                                    or ""
                                )
                            )
                        )
                    ):
                        return {
                            "status": "materialized",
                            "reason_code": ("recovery_release_already_completed"),
                            "plan_id": plan_id,
                            "created_task_ids": created_task_ids,
                            "approval_status": "consumed",
                        }
                    self._cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self._save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": ("recovery_source_confirmation_failed"),
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }

                node_task_pairs: list[tuple[Any, str]] = []
                for index, node in enumerate(nodes):
                    task_id = str(getattr(node, "materialized_task_id", "") or "").strip()
                    if not task_id and index < len(created_task_ids):
                        task_id = created_task_ids[index]
                    if task_id:
                        node_task_pairs.append((node, task_id))
                node_key_to_task_id = {
                    str(getattr(node, "node_key", "") or ""): task_id for node, task_id in node_task_pairs
                }
                child_dependencies = {
                    task_id: [
                        node_key_to_task_id[dependency]
                        for dependency in list(getattr(node, "depends_on", None) or [])
                        if dependency in node_key_to_task_id
                    ]
                    for node, task_id in node_task_pairs
                }
                expected_task_ids = [task_id for _node, task_id in node_task_pairs]
                if len(expected_task_ids) != len(created_task_ids) or set(expected_task_ids) != set(created_task_ids):
                    self._cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self._save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "failed",
                        "reason_code": "materialized_task_binding_mismatch",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "children_cancelled": True,
                    }

                child_id_set = set(created_task_ids)
                preexisting_dependency_ids = list(
                    dict.fromkeys(
                        str(value or "").strip()
                        for value in list(
                            getattr(
                                latest_source,
                                "depends_on",
                                None,
                            )
                            or []
                        )
                        if (
                            str(value or "").strip()
                            and str(value or "").strip()
                            not in child_id_set
                        )
                    )
                )
                dependency_binding = (
                    build_recovery_dependency_binding(
                        source_task_id=source_task_id,
                        preexisting_dependency_ids=(
                            preexisting_dependency_ids
                        ),
                        child_task_ids=created_task_ids,
                    )
                )
                recovery_state = {
                    "schema": RECOVERY_STATE_SCHEMA,
                    "status": "materialized_waiting_for_children",
                    "plan_id": plan_id,
                    "approval_request_id": approval_id,
                    "created_task_ids": created_task_ids,
                    "recovery_key": recovery_key,
                    "recovery_depth": 1,
                    "release_epoch": release_epoch,
                    "team_id": team_id,
                    "dependency_binding": dependency_binding,
                }
                recovery_strategy_state = (
                    _transitioned_recovery_strategy(
                        latest_source,
                        status="materialized",
                        reason_code="approved_plan_materialized",
                    )
                )
                source_transitioned = self._conditional_update_task(
                    source_task_id,
                    "blocked_by_dependency",
                    expected_statuses={
                        "waiting_for_review",
                        "needs_review",
                        "blocked_by_dependency",
                    },
                    force=False,
                    status_reason_code=("recovery_plan_materialized_waiting_for_children"),
                    depends_on=list(
                        dependency_binding[
                            "authoritative_dependency_ids"
                        ]
                    ),
                    verification_status={
                        **_mapping(
                            getattr(
                                latest_source,
                                "verification_status",
                                None,
                            )
                        ),
                        "model_recovery": recovery_state,
                        "model_recovery_strategy": (
                            recovery_strategy_state
                        ),
                    },
                    status_reason_details={
                        **_mapping(
                            getattr(
                                latest_source,
                                "status_reason_details",
                                None,
                            )
                        ),
                        "model_recovery": recovery_state,
                        "model_recovery_strategy": (
                            recovery_strategy_state
                        ),
                    },
                    event_type="task_recovery_plan_materialized",
                    event_actor="hub_approval_dispatcher",
                    event_details={
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                    },
                )
                if not source_transitioned:
                    self._cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self._save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": "recovery_source_transition_conflict",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }

                release_details = {
                    "materialization_release_source_task_id": (
                        source_task_id
                    ),
                    "materialization_release_goal_id": goal_id,
                    "materialization_release_approval_id": approval_id,
                    "materialization_release_team_id": team_id,
                    "materialization_dependency_binding_digest": (
                        dependency_binding["digest"]
                    ),
                }
                if not self._save_release_state(
                    repos=repos,
                    plan_id=plan_id,
                    state="committed",
                    release_epoch=release_epoch,
                    release_details=release_details,
                ):
                    self._cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    return {
                        "status": "failed",
                        "reason_code": (
                            "recovery_release_commit_conflict"
                        ),
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "children_cancelled": True,
                    }

                for child_task_id in created_task_ids:
                    dependencies = child_dependencies.get(child_task_id)
                    release_status = "todo" if dependencies == [] else "blocked_by_dependency"
                    child = repos.task_repo.get_by_id(child_task_id)
                    child_release_state = {
                        "schema": "ananta.recovery_release_gate.v1",
                        "release_epoch": release_epoch,
                        "plan_id": plan_id,
                        "source_task_id": source_task_id,
                        "goal_id": goal_id,
                        "approval_request_id": approval_id,
                        "recovery_key": recovery_key,
                        "team_id": team_id,
                        "task_payload_digest": (
                            calculate_recovery_task_payload_digest(
                                child
                            )
                        ),
                    }
                    released = self._conditional_update_task(
                        child_task_id,
                        release_status,
                        expected_statuses={
                            "paused",
                            release_status,
                        },
                        force=False,
                        status_reason_code="recovery_approval_consumed",
                        status_reason_details={
                            **_mapping(
                                getattr(
                                    child,
                                    "status_reason_details",
                                    None,
                                )
                            ),
                            "model_recovery_release": (
                                child_release_state
                            ),
                        },
                        event_type="task_recovery_child_released",
                        event_actor="hub_approval_dispatcher",
                        event_details={
                            "plan_id": plan_id,
                            "source_task_id": source_task_id,
                        },
                    )
                    if not released:
                        child = repos.task_repo.get_by_id(child_task_id)
                        child_status = str(getattr(child, "status", "") or "").strip().lower()
                        # A released root may already be claimed before an
                        # interrupted release is reconciled.
                        released = bool(
                            child is not None
                            and str(getattr(child, "plan_id", "") or "") == plan_id
                            and child_status != "paused"
                            and str(
                                _mapping(
                                    _mapping(
                                        getattr(
                                            child,
                                            "status_reason_details",
                                            None,
                                        )
                                    ).get(
                                        "model_recovery_release"
                                    )
                                ).get("release_epoch")
                                or ""
                            )
                            == release_epoch
                        )
                    if not released:
                        self._cancel_recovery_children(
                            created_task_ids=created_task_ids,
                            plan_id=plan_id,
                            source_task_id=source_task_id,
                        )
                        self._save_release_state(
                            repos=repos,
                            plan_id=plan_id,
                            state="cancelled",
                            release_epoch=release_epoch,
                        )
                        return {
                            "status": "failed",
                            "reason_code": "recovery_child_release_conflict",
                            "plan_id": plan_id,
                            "created_task_ids": created_task_ids,
                            "children_cancelled": True,
                        }

                # Do not persist a successful release marker based on a stale
                # write attempt.  Re-read both authoritative owners.
                confirmed_source = repos.task_repo.get_by_id(source_task_id)
                confirmed_goal = repos.goal_repo.get_by_id(goal_id)
                confirmed_recovery = _mapping(
                    _mapping(
                        getattr(
                            confirmed_source,
                            "status_reason_details",
                            None,
                        )
                    ).get("model_recovery")
                )
                source_confirmed = bool(
                    confirmed_source is not None
                    and str(getattr(confirmed_source, "status", "") or "").strip().lower() == "blocked_by_dependency"
                    and str(confirmed_recovery.get("plan_id") or "") == plan_id
                    and str(confirmed_recovery.get("approval_request_id") or "") == approval_id
                    and str(
                        confirmed_recovery.get("release_epoch") or ""
                    )
                    == release_epoch
                    and self._team_binding_matches(
                        source_task=confirmed_source,
                        goal=confirmed_goal,
                        expected_team_id=team_id,
                    )
                )
                if (
                    not source_confirmed
                    or confirmed_goal is None
                    or self._is_terminal(
                        confirmed_goal,
                        _TERMINAL_GOAL_STATUSES,
                    )
                ):
                    self._cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self._save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": "recovery_source_confirmation_failed",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }
                if not self._save_release_state(
                    repos=repos,
                    plan_id=plan_id,
                    state="completed",
                    release_epoch=release_epoch,
                    release_details=release_details,
                ):
                    return {
                        "status": "ignored",
                        "reason_code": "recovery_action_in_progress",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                    }
                return {
                    "status": "materialized",
                    "reason_code": "approved_plan_materialized",
                    "plan_id": plan_id,
                    "created_task_ids": created_task_ids,
                    "approval_status": "consumed",
                }

    def propose_after_model_exhaustion(
        self,
        *,
        task: Any,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Persist one approval-gated draft for an eligible exhausted task."""
        if str(self._role_provider() or "").strip().lower() != "hub":
            return {"status": "ignored", "reason_code": "hub_role_required"}

        task_data = _mapping(task)
        task_id = str(task_data.get("id") or getattr(task, "id", "") or "").strip()
        goal_id = str(task_data.get("goal_id") or getattr(task, "goal_id", "") or "").strip()
        if not task_id or not goal_id:
            return {"status": "ignored", "reason_code": "task_goal_binding_required"}
        recovery_depth = self._task_recovery_depth(task)

        actions, approval_required, policy_hash = self._policy_binding(task)
        if not self._plan_action_configured(actions):
            return {"status": "ignored", "reason_code": "recovery_plan_not_configured"}
        if "require_approval" not in actions or not approval_required:
            return {"status": "stopped", "reason_code": "recovery_plan_approval_required"}

        signal = self._safe_signal_summary(strategy_failures)
        if signal is None:
            return {"status": "ignored", "reason_code": "model_exhaustion_signal_required"}

        repos = self._repos()
        source_task = repos.task_repo.get_by_id(task_id)
        goal = repos.goal_repo.get_by_id(goal_id)
        if source_task is None or goal is None:
            return {"status": "ignored", "reason_code": "authoritative_binding_not_found"}
        if str(getattr(source_task, "goal_id", "") or "") != goal_id:
            return {"status": "stopped", "reason_code": "task_goal_binding_mismatch"}
        team_id = str(getattr(goal, "team_id", "") or "").strip()
        if not self._team_binding_matches(
            source_task=source_task,
            goal=goal,
            expected_team_id=team_id,
        ):
            return {
                "status": "stopped",
                "reason_code": "recovery_team_binding_mismatch",
            }
        if str(getattr(goal, "status", "") or "").strip().lower() in _TERMINAL_GOAL_STATUSES:
            return {"status": "stopped", "reason_code": "recovery_goal_terminal"}
        if str(getattr(source_task, "status", "") or "").strip().lower() in _TERMINAL_TASK_STATUSES:
            return {"status": "stopped", "reason_code": "recovery_source_terminal"}

        failure_fingerprint = _sha256_json(
            {
                "task_id": task_id,
                "signal": signal,
            }
        )
        recovery_key = _sha256_json(
            {
                "task_id": task_id,
                "team_id": team_id,
                "policy_hash": policy_hash,
                "failure_fingerprint": failure_fingerprint,
            }
        )

        with (
            self._lock_for(recovery_key),
            self._distributed_recovery_lock(recovery_key) as lock_acquired,
        ):
            if not lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "recovery_plan_generation_in_progress",
                    "recovery_key": recovery_key,
                }
            existing = self._existing_plan(
                repos,
                goal_id=goal_id,
                recovery_key=recovery_key,
            )
            if existing is not None:
                with self._distributed_source_lock(task_id) as source_lock_acquired:
                    if not source_lock_acquired:
                        return {
                            "status": "ignored",
                            "reason_code": ("recovery_plan_generation_in_progress"),
                            "recovery_key": recovery_key,
                        }
                    with self._source_mutation_lock(task_id):
                        authoritative_source = repos.task_repo.get_by_id(task_id)
                        authoritative_goal = repos.goal_repo.get_by_id(goal_id)
                        if authoritative_source is None or authoritative_goal is None:
                            return {
                                "status": "stopped",
                                "reason_code": ("authoritative_binding_not_found"),
                            }
                        return self._resume_existing_plan_saga(
                            repos=repos,
                            plan=existing,
                            source_task=authoritative_source,
                            goal=authoritative_goal,
                            goal_id=goal_id,
                            source_task_id=task_id,
                            recovery_key=recovery_key,
                            policy_hash=policy_hash,
                            team_id=team_id,
                        )
            if recovery_depth >= 1:
                return {
                    "status": "stopped",
                    "reason_code": "task_recovery_recursion_guard",
                }

            compacted_context, compaction_meta = self._compacted_context(
                source_task,
                actions=actions,
            )
            title = str(getattr(source_task, "title", "") or "delegated task").strip()
            description = str(getattr(source_task, "description", "") or "").strip()
            recovery_goal = (
                f'Implement and validate a bounded recovery plan for task "{title[:240]}". '
                f"Original task: {description[:1600]}"
            )
            result = self._planner().plan_goal(
                goal=recovery_goal,
                context=compacted_context,
                team_id=team_id or None,
                parent_task_id=task_id,
                create_tasks=False,
                use_template=True,
                use_repo_context=False,
                goal_id=goal_id,
                goal_trace_id=str(getattr(source_task, "goal_trace_id", "") or getattr(goal, "trace_id", "") or ""),
                mode="generic",
                mode_data={
                    "task_recovery": True,
                    "source_task_id": task_id,
                    "segment_planning": "segment_planning" in actions,
                    "recovery_depth": 1,
                },
                initial_plan_rationale={
                    "recovery_schema": RECOVERY_STATE_SCHEMA,
                    "recovery_key": recovery_key,
                    "source_task_id": task_id,
                    "team_id": team_id,
                    "recovery_depth": 1,
                    "policy_hash": policy_hash,
                    "failure_fingerprint": failure_fingerprint,
                    "failure_signal": signal,
                    "recovery_actions": actions,
                    "compaction": compaction_meta,
                    "approval_state": "initializing",
                },
            )
            plan_id = str((result or {}).get("plan_id") or "").strip()
            if not plan_id or (result or {}).get("error"):
                reason_code = str(
                    (result or {}).get("error_classification")
                    or (result or {}).get("error")
                    or "recovery_plan_generation_failed"
                )[:160]
                self._audit(
                    "task_recovery_plan_failed",
                    {
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "reason_code": reason_code,
                        "policy_hash": policy_hash,
                        "failure_fingerprint": failure_fingerprint,
                    },
                )
                return {"status": "failed", "reason_code": reason_code}

            plan = repos.plan_repo.get_by_id(plan_id)
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
            if plan is None or not nodes or str(getattr(plan, "goal_id", "") or "") != goal_id:
                return {"status": "failed", "reason_code": "recovery_plan_persistence_failed"}

            # Planning may involve a slow LLM call. Re-read every authoritative
            # precondition before creating an approval or mutating the source.
            source_task = repos.task_repo.get_by_id(task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if source_task is None or goal is None:
                self._reject_plan(
                    repos,
                    plan,
                    reason_code="authoritative_binding_not_found",
                )
                return {
                    "status": "stopped",
                    "reason_code": "authoritative_binding_not_found",
                }
            if (
                str(getattr(source_task, "goal_id", "") or "") != goal_id
                or not self._team_binding_matches(
                    source_task=source_task,
                    goal=goal,
                    expected_team_id=team_id,
                )
                or self._is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                or self._is_terminal(source_task, _TERMINAL_TASK_STATUSES)
            ):
                reason_code = (
                    "recovery_goal_terminal"
                    if self._is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                    else "recovery_source_terminal"
                )
                self._reject_plan(repos, plan, reason_code=reason_code)
                return {"status": "stopped", "reason_code": reason_code}
            (
                current_actions,
                current_approval_required,
                current_policy_hash,
            ) = self._policy_binding(source_task)
            if (
                not self._plan_action_configured(current_actions)
                or "require_approval" not in current_actions
                or not current_approval_required
                or current_policy_hash != policy_hash
            ):
                self._reject_plan(
                    repos,
                    plan,
                    reason_code="recovery_policy_changed",
                )
                return {
                    "status": "stopped",
                    "reason_code": "recovery_policy_changed",
                }

            with (
                self._distributed_source_lock(task_id) as source_lock_acquired,
                self._source_mutation_lock(task_id),
            ):
                if not source_lock_acquired:
                    return {
                        "status": "ignored",
                        "reason_code": ("recovery_plan_generation_in_progress"),
                        "recovery_key": recovery_key,
                    }
                source_task = repos.task_repo.get_by_id(task_id)
                goal = repos.goal_repo.get_by_id(goal_id)
                if (
                    source_task is None
                    or goal is None
                    or self._is_terminal(
                        source_task,
                        _TERMINAL_TASK_STATUSES,
                    )
                    or self._is_terminal(
                        goal,
                        _TERMINAL_GOAL_STATUSES,
                    )
                    or not self._team_binding_matches(
                        source_task=source_task,
                        goal=goal,
                        expected_team_id=team_id,
                    )
                ):
                    reason_code = (
                        "recovery_goal_terminal"
                        if goal is None
                        or self._is_terminal(
                            goal,
                            _TERMINAL_GOAL_STATUSES,
                        )
                        else "recovery_source_terminal"
                    )
                    self._reject_plan(
                        repos,
                        plan,
                        reason_code=reason_code,
                    )
                    return {
                        "status": "stopped",
                        "reason_code": reason_code,
                    }
                active_plan = self._active_plan_for_source(
                    repos,
                    goal_id=goal_id,
                    source_task_id=task_id,
                    exclude_plan_id=plan_id,
                )
                authoritative_depth = self._task_recovery_depth(source_task)
                if active_plan is not None:
                    self._reject_plan(
                        repos,
                        plan,
                        reason_code=("recovery_source_plan_already_active"),
                    )
                    active_rationale = _mapping(getattr(active_plan, "rationale", None))
                    return {
                        "status": str(getattr(active_plan, "status", "") or "draft"),
                        "reason_code": ("recovery_plan_already_exists_for_source"),
                        "plan_id": str(getattr(active_plan, "id", "") or ""),
                        "approval_request_id": active_rationale.get("approval_request_id"),
                    }
                if authoritative_depth >= 1:
                    self._reject_plan(
                        repos,
                        plan,
                        reason_code="task_recovery_recursion_guard",
                    )
                    return {
                        "status": "stopped",
                        "reason_code": ("task_recovery_recursion_guard"),
                    }
                (
                    locked_actions,
                    locked_approval_required,
                    locked_policy_hash,
                ) = self._policy_binding(source_task)
                if (
                    not self._plan_action_configured(locked_actions)
                    or "require_approval" not in locked_actions
                    or not locked_approval_required
                    or locked_policy_hash != policy_hash
                ):
                    self._reject_plan(
                        repos,
                        plan,
                        reason_code="recovery_policy_changed",
                    )
                    return {
                        "status": "stopped",
                        "reason_code": "recovery_policy_changed",
                    }

                with self._plan_mutation_lock(plan_id) as plan_lock_acquired:
                    if not plan_lock_acquired:
                        return {
                            "status": "ignored",
                            "reason_code": "plan_mutation_in_progress",
                            "plan_id": plan_id,
                        }
                    plan = repos.plan_repo.get_by_id(plan_id)
                    nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
                    if plan is None or not nodes:
                        return {
                            "status": "failed",
                            "reason_code": ("recovery_plan_persistence_failed"),
                        }
                    plan.status = "pending_approval"
                    plan.planning_mode = "task_recovery"
                    plan.rationale = {
                        **_mapping(getattr(plan, "rationale", None)),
                        "recovery_schema": RECOVERY_STATE_SCHEMA,
                        "recovery_key": recovery_key,
                        "source_task_id": task_id,
                        "team_id": team_id,
                        "recovery_depth": 1,
                        "policy_hash": policy_hash,
                        "failure_fingerprint": failure_fingerprint,
                        "failure_signal": signal,
                        "recovery_actions": actions,
                        "compaction": compaction_meta,
                        "approval_state": "pending",
                    }
                    plan.updated_at = time.time()
                    plan = repos.plan_repo.save(plan)
                    approval, plan_digest = self._request_materialization_approval(
                        repos=repos,
                        plan=plan,
                        nodes=nodes,
                        goal_id=goal_id,
                        source_task_id=task_id,
                        recovery_key=recovery_key,
                        policy_hash=policy_hash,
                        team_id=team_id,
                    )
                source_transitioned = self._mark_source_waiting_for_approval(
                    source_task=source_task,
                    plan_id=plan_id,
                    approval_request_id=str(approval.id),
                    recovery_key=recovery_key,
                    node_count=len(nodes),
                    team_id=team_id,
                )
                if not source_transitioned:
                    return {
                        "status": "failed",
                        "reason_code": ("recovery_source_transition_conflict"),
                        "plan_id": plan_id,
                        "approval_request_id": str(approval.id),
                    }
                self._audit(
                    "task_recovery_plan_proposed",
                    {
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "plan_id": plan_id,
                        "approval_request_id": approval.id,
                        "policy_hash": policy_hash,
                        "failure_fingerprint": failure_fingerprint,
                        "node_count": len(nodes),
                    },
                )
                return {
                    "status": "pending_approval",
                    "reason_code": "recovery_plan_pending_approval",
                    "plan_id": plan_id,
                    "approval_request_id": approval.id,
                    "plan_digest": plan_digest,
                    "recovery_key": recovery_key,
                    "node_count": len(nodes),
                    "compaction": compaction_meta,
                }

    def handle_approval_decision(self, approval: Any) -> dict[str, Any]:
        """Apply a recovery approval decision; no other approval tool is handled."""
        if str(self._role_provider() or "").strip().lower() != "hub":
            return {"status": "ignored", "reason_code": "hub_role_required"}
        if str(getattr(approval, "tool_name", "") or "") != RECOVERY_MATERIALIZE_TOOL:
            return {"status": "ignored", "reason_code": "approval_tool_not_handled"}

        args = _mapping(getattr(approval, "canonical_arguments", None))
        plan_id = str(args.get("plan_id") or "").strip()
        goal_id = str(args.get("goal_id") or "").strip()
        source_task_id = str(args.get("source_task_id") or "").strip()
        recovery_key = str(args.get("recovery_key") or "").strip()
        expected_policy_hash = str(args.get("policy_hash") or "").strip()
        expected_team_id = str(args.get("team_id") or "").strip()
        if not all(
            (
                plan_id,
                goal_id,
                source_task_id,
                recovery_key,
                expected_policy_hash,
            )
        ) or "team_id" not in args:
            return {"status": "failed", "reason_code": "approval_binding_incomplete"}

        with (
            self._lock_for(plan_id),
            self._distributed_recovery_lock(recovery_key) as distributed_lock_acquired,
        ):
            if not distributed_lock_acquired:
                # Another Hub owns the same exact recovery mutation. Keep the
                # durable grant intact; reconciliation will observe its result.
                return {
                    "status": "ignored",
                    "reason_code": "recovery_action_in_progress",
                    "plan_id": plan_id,
                }
            approval_service = self._approval_service()
            get_request = getattr(approval_service, "get_request", None)
            if callable(get_request):
                authoritative = get_request(str(getattr(approval, "id", "") or ""))
                if authoritative is not None:
                    approval = authoritative
            repos = self._repos()
            plan = repos.plan_repo.get_by_id(plan_id)
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
            source_task = repos.task_repo.get_by_id(source_task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if plan is None or source_task is None or goal is None or not nodes:
                return {"status": "failed", "reason_code": "approval_target_not_found"}
            rationale = _mapping(getattr(plan, "rationale", None))
            source_details = _mapping(getattr(source_task, "status_reason_details", None))
            source_recovery = _mapping(source_details.get("model_recovery"))
            approval_id = str(getattr(approval, "id", "") or "")
            decision = str(getattr(approval, "status", "") or "").strip().lower()
            team_bindings_match = (
                self._stored_team_binding_matches(
                    rationale,
                    expected_team_id,
                )
                and self._team_binding_matches(
                    source_task=source_task,
                    goal=goal,
                    expected_team_id=expected_team_id,
                )
            )
            if not team_bindings_match:
                self._reject_plan(
                    repos,
                    plan,
                    reason_code="recovery_team_binding_changed",
                )
                if decision == "granted":
                    consumed = approval_service.consume_request(
                        approval_id
                    )
                    if consumed is None:
                        return {
                            "status": "failed",
                            "reason_code": "approval_consume_failed",
                            "plan_id": plan_id,
                        }
                return {
                    "status": "stopped",
                    "reason_code": "recovery_team_binding_changed",
                    "plan_id": plan_id,
                    "approval_status": (
                        "consumed"
                        if decision in {"granted", "consumed"}
                        else decision
                    ),
                }
            refresh_saga = _mapping(
                rationale.get("approval_refresh")
            )
            stale_refresh_id = str(
                refresh_saga.get("stale_approval_request_id") or ""
            )
            refreshed_id = str(
                refresh_saga.get(
                    "refreshed_approval_request_id"
                )
                or ""
            )
            refresh_state = str(
                refresh_saga.get("state") or ""
            )
            if approval_id == stale_refresh_id:
                return self._refresh_stale_plan_approval(
                    repos=repos,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=expected_policy_hash,
                    team_id=expected_team_id,
                    stale_approval_id=approval_id,
                )
            if (
                approval_id == refreshed_id
                and refresh_state != "completed"
            ):
                refresh_result = (
                    self._complete_approval_refresh_saga(
                        repos=repos,
                        plan_id=plan_id,
                        goal_id=goal_id,
                        source_task_id=source_task_id,
                        recovery_key=recovery_key,
                        team_id=expected_team_id,
                        node_count=len(nodes),
                        stale_approval_id=stale_refresh_id,
                        refreshed_approval_id=refreshed_id,
                        refreshed_digest=str(
                            refresh_saga.get(
                                "refreshed_plan_digest"
                            )
                            or ""
                        ),
                    )
                )
                if (
                    str(refresh_result.get("status") or "")
                    != "pending_approval"
                ):
                    return refresh_result
                plan = repos.plan_repo.get_by_id(plan_id)
                source_task = repos.task_repo.get_by_id(
                    source_task_id
                )
                if plan is None or source_task is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_target_not_found",
                    }
                rationale = _mapping(
                    getattr(plan, "rationale", None)
                )
                source_details = _mapping(
                    getattr(
                        source_task,
                        "status_reason_details",
                        None,
                    )
                )
                source_recovery = _mapping(
                    source_details.get("model_recovery")
                )
            plan_bindings_match = (
                str(getattr(plan, "goal_id", "") or "") == goal_id
                and str(getattr(source_task, "goal_id", "") or "") == goal_id
                and str(rationale.get("recovery_key") or "") == recovery_key
                and str(rationale.get("policy_hash") or "") == expected_policy_hash
                and str(rationale.get("source_task_id") or "") == source_task_id
                and str(rationale.get("approval_request_id") or "") == approval_id
                and self._stored_team_binding_matches(
                    rationale,
                    expected_team_id,
                )
            )
            source_binding_matches = (
                str(source_recovery.get("recovery_key") or "") == recovery_key
                and str(source_recovery.get("plan_id") or "") == plan_id
                and str(source_recovery.get("approval_request_id") or "") == approval_id
                and self._stored_team_binding_matches(
                    source_recovery,
                    expected_team_id,
                )
            )
            source_binding_empty = not any(
                str(source_recovery.get(key) or "").strip()
                for key in (
                    "recovery_key",
                    "plan_id",
                    "approval_request_id",
                )
            )
            if not plan_bindings_match or not (source_binding_matches or source_binding_empty):
                return {"status": "failed", "reason_code": "approval_binding_mismatch"}
            if source_binding_empty and decision in {"granted", "consumed"}:
                with (
                    self._distributed_source_lock(source_task_id) as source_lock_acquired,
                    self._source_mutation_lock(source_task_id),
                ):
                    source_task = repos.task_repo.get_by_id(source_task_id)
                    if (
                        not source_lock_acquired
                        or source_task is None
                        or not self._mark_source_waiting_for_approval(
                            source_task=source_task,
                            plan_id=plan_id,
                            approval_request_id=approval_id,
                            recovery_key=recovery_key,
                            node_count=len(nodes),
                            team_id=expected_team_id,
                        )
                    ):
                        return {
                            "status": "failed",
                            "reason_code": ("recovery_source_transition_conflict"),
                        }
                source_task = repos.task_repo.get_by_id(source_task_id)
                if source_task is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_target_not_found",
                    }
            if decision == "consumed":
                if str(getattr(plan, "status", "") or "") != "materialized":
                    return {
                        "status": "failed",
                        "reason_code": ("consumed_approval_plan_not_materialized"),
                        "plan_id": plan_id,
                    }
                created_task_ids = [
                    str(getattr(node, "materialized_task_id", "") or "")
                    for node in nodes
                    if str(getattr(node, "materialized_task_id", "") or "").strip()
                ]
                if not created_task_ids:
                    return {
                        "status": "failed",
                        "reason_code": "materialized_tasks_missing",
                        "plan_id": plan_id,
                    }
                return self._release_materialized_recovery(
                    repos=repos,
                    plan=plan,
                    nodes=nodes,
                    source_task_id=source_task_id,
                    goal_id=goal_id,
                    approval_id=approval_id,
                    recovery_key=recovery_key,
                    team_id=expected_team_id,
                    created_task_ids=created_task_ids,
                )
            if decision == "denied":
                with self._plan_mutation_lock(plan_id) as plan_lock_acquired:
                    if not plan_lock_acquired:
                        return {
                            "status": "ignored",
                            "reason_code": "plan_mutation_in_progress",
                            "plan_id": plan_id,
                        }
                    plan = repos.plan_repo.get_by_id(plan_id)
                    if plan is None:
                        return {
                            "status": "failed",
                            "reason_code": "approval_target_not_found",
                        }
                    plan.status = "rejected"
                    plan.rationale = {
                        **_mapping(getattr(plan, "rationale", None)),
                        "approval_state": "denied",
                        "approval_request_id": approval_id,
                    }
                    plan.updated_at = time.time()
                    repos.plan_repo.save(plan)
                source_task = repos.task_repo.get_by_id(source_task_id)
                current_source_status = str(getattr(source_task, "status", "") or "").strip().lower()
                if source_task is not None and current_source_status not in _TERMINAL_TASK_STATUSES:
                    denied_recovery_state = {
                        "schema": RECOVERY_STATE_SCHEMA,
                        "status": "denied",
                        "plan_id": plan_id,
                        "approval_request_id": approval_id,
                        "recovery_key": recovery_key,
                        "recovery_depth": 1,
                    }
                    denied_strategy_state = (
                        _transitioned_recovery_strategy(
                            source_task,
                            status="denied",
                            reason_code="recovery_plan_denied",
                        )
                    )
                    source_transitioned = self._conditional_update_task(
                        source_task_id,
                        "needs_review",
                        expected_statuses={current_source_status},
                        force=False,
                        status_reason_code="recovery_plan_denied",
                        verification_status={
                            **_mapping(
                                getattr(
                                    source_task,
                                    "verification_status",
                                    None,
                                )
                            ),
                            "model_recovery": denied_recovery_state,
                            "model_recovery_strategy": (
                                denied_strategy_state
                            ),
                        },
                        status_reason_details={
                            **_mapping(
                                getattr(
                                    source_task,
                                    "status_reason_details",
                                    None,
                                )
                            ),
                            "model_recovery": denied_recovery_state,
                            "model_recovery_strategy": (
                                denied_strategy_state
                            ),
                        },
                        event_type="task_recovery_plan_denied",
                        event_actor="hub_approval_dispatcher",
                        event_details={"plan_id": plan_id},
                    )
                    if not source_transitioned:
                        confirmed_source = repos.task_repo.get_by_id(source_task_id)
                        confirmed_recovery = _mapping(
                            _mapping(
                                getattr(
                                    confirmed_source,
                                    "status_reason_details",
                                    None,
                                )
                            ).get("model_recovery")
                        )
                        denial_confirmed = bool(
                            confirmed_source is not None
                            and (
                                self._is_terminal(
                                    confirmed_source,
                                    _TERMINAL_TASK_STATUSES,
                                )
                                or (
                                    str(confirmed_recovery.get("status") or "") == "denied"
                                    and str(confirmed_recovery.get("approval_request_id") or "") == approval_id
                                )
                            )
                        )
                        if not denial_confirmed:
                            return {
                                "status": "failed",
                                "reason_code": ("recovery_source_transition_conflict"),
                                "plan_id": plan_id,
                            }
                self._audit(
                    "task_recovery_plan_denied",
                    {
                        "task_id": source_task_id,
                        "goal_id": goal_id,
                        "plan_id": plan_id,
                        "approval_request_id": approval_id,
                    },
                )
                return {
                    "status": "denied",
                    "reason_code": "recovery_plan_denied",
                    "plan_id": plan_id,
                }
            goal_terminal = self._is_terminal(
                goal,
                _TERMINAL_GOAL_STATUSES,
            )
            source_status = str(getattr(source_task, "status", "") or "").strip().lower()
            source_terminal = source_status in _TERMINAL_TASK_STATUSES
            source_state_changed = source_status not in {"waiting_for_review", "needs_review"}
            if goal_terminal or source_terminal or source_state_changed:
                reason_code = (
                    "recovery_goal_terminal"
                    if goal_terminal
                    else ("recovery_source_terminal" if source_terminal else "recovery_source_state_changed")
                )
                if decision == "granted":
                    self._reject_plan(
                        repos,
                        plan,
                        reason_code=reason_code,
                    )
                    consumed = approval_service.consume_request(approval_id)
                    if consumed is not None:
                        return {
                            "status": "stopped",
                            "reason_code": reason_code,
                            "plan_id": plan_id,
                            "approval_status": "consumed",
                        }
                return {
                    "status": "failed",
                    "reason_code": reason_code,
                }

            if decision != "granted":
                return {"status": "ignored", "reason_code": f"approval_not_granted:{decision}"}

            (
                current_actions,
                current_approval_required,
                current_policy_hash,
            ) = self._policy_binding(source_task)
            if (
                not self._plan_action_configured(current_actions)
                or "require_approval" not in current_actions
                or not current_approval_required
                or current_policy_hash != expected_policy_hash
            ):
                self._reject_plan(
                    repos,
                    plan,
                    reason_code="recovery_policy_changed",
                )
                consumed = approval_service.consume_request(approval_id)
                if consumed is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_consume_failed",
                        "plan_id": plan_id,
                    }
                return {
                    "status": "stopped",
                    "reason_code": "recovery_policy_changed",
                    "plan_id": plan_id,
                    "approval_status": "consumed",
                }

            current_digest = self._plan_digest(plan, nodes)
            expected_digest = str(args.get("plan_digest") or "")
            if (
                current_digest != expected_digest
                or str(getattr(approval, "target_fingerprint", "") or "") != current_digest
                or str(rationale.get("plan_digest") or "") != current_digest
            ):
                policy_hash = str(rationale.get("policy_hash") or "").strip()
                if not policy_hash:
                    return {
                        "status": "failed",
                        "reason_code": "recovery_policy_binding_missing",
                    }
                return self._refresh_stale_plan_approval(
                    repos=repos,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=policy_hash,
                    team_id=expected_team_id,
                    stale_approval_id=approval_id,
                )

            source_task = repos.task_repo.get_by_id(source_task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if (
                source_task is None
                or goal is None
                or self._is_terminal(source_task, _TERMINAL_TASK_STATUSES)
                or self._is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                or str(getattr(source_task, "status", "") or "").strip().lower()
                not in {"waiting_for_review", "needs_review"}
            ):
                reason_code = (
                    "recovery_goal_terminal"
                    if goal is None or self._is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                    else (
                        "recovery_source_terminal"
                        if source_task is None
                        or self._is_terminal(
                            source_task,
                            _TERMINAL_TASK_STATUSES,
                        )
                        else "recovery_source_state_changed"
                    )
                )
                self._reject_plan(
                    repos,
                    plan,
                    reason_code=reason_code,
                )
                consumed = approval_service.consume_request(approval_id)
                if consumed is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_consume_failed",
                        "plan_id": plan_id,
                    }
                return {
                    "status": "stopped",
                    "reason_code": reason_code,
                    "plan_id": plan_id,
                    "approval_status": "consumed",
                }
            approved_materialization_inputs_digest = str(
                rationale.get("materialization_inputs_digest")
                or ""
            )
            current_materialization_inputs_digest = (
                calculate_recovery_materialization_inputs_digest(
                    goal
                )
            )
            if (
                not approved_materialization_inputs_digest
                or approved_materialization_inputs_digest
                != current_materialization_inputs_digest
            ):
                self._reject_plan(
                    repos,
                    plan,
                    reason_code=(
                        "recovery_materialization_inputs_changed"
                    ),
                )
                consumed = approval_service.consume_request(
                    approval_id
                )
                if consumed is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_consume_failed",
                        "plan_id": plan_id,
                    }
                return {
                    "status": "stopped",
                    "reason_code": (
                        "recovery_materialization_inputs_changed"
                    ),
                    "plan_id": plan_id,
                    "approval_status": "consumed",
                }

            result = self._planning_service().materialize_existing_plan(
                planner=self._planner(),
                plan_id=plan_id,
                approval_request_id=str(getattr(approval, "id", "") or ""),
                team_id=expected_team_id or None,
                parent_task_id=None,
                source_task_id=source_task_id,
                expected_plan_digest=expected_digest,
                # Dependency reconciliation automatically releases root nodes
                # which have no ``depends_on`` entries.  ``paused`` is the
                # canonical inert state that remains non-dispatchable until
                # the exact approval has been consumed below.
                initial_task_status="paused",
            )
            if str(result.get("reason_code") or "") == "recovery_plan_digest_stale":
                return self._refresh_stale_plan_approval(
                    repos=repos,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=expected_policy_hash,
                    team_id=expected_team_id,
                    stale_approval_id=approval_id,
                )
            if str(result.get("status") or "") != "materialized":
                return dict(result)

            plan = repos.plan_repo.get_by_id(plan_id) or plan
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id) or nodes
            plan.status = "materialized"
            created_task_ids = [
                str(value) for value in list(result.get("created_task_ids") or []) if str(value).strip()
            ]
            consumed = approval_service.consume_request(approval_id)
            if consumed is None:
                return {
                    "status": "failed",
                    "reason_code": "approval_consume_failed",
                    "plan_id": plan_id,
                    "created_task_ids": created_task_ids,
                }

            release_result = self._release_materialized_recovery(
                repos=repos,
                plan=plan,
                nodes=nodes,
                source_task_id=source_task_id,
                goal_id=goal_id,
                approval_id=approval_id,
                recovery_key=recovery_key,
                team_id=expected_team_id,
                created_task_ids=created_task_ids,
            )
            self._audit(
                "task_recovery_plan_materialized",
                {
                    "task_id": source_task_id,
                    "goal_id": goal_id,
                    "plan_id": plan_id,
                    "approval_request_id": str(getattr(approval, "id", "") or ""),
                    "created_task_ids": created_task_ids,
                },
            )
            return {
                **dict(result),
                **release_result,
            }


_service = TaskRecoveryPlanningService()


def get_task_recovery_planning_service() -> TaskRecoveryPlanningService:
    return _service
