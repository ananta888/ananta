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

import agent.services.task_recovery_approval_decision as _task_recovery_approval_decision
import agent.services.task_recovery_approval_saga as _task_recovery_approval_saga
import agent.services.task_recovery_proposal as _task_recovery_proposal
import agent.services.task_recovery_release as _task_recovery_release
from agent.config import settings
from agent.services.approval_auto_grant_policy import (  # noqa: F401 - re-exported for approval/run-control callers
    RECOVERY_MATERIALIZE_TOOL,
)
from agent.services.recovery_plan_contract import calculate_recovery_plan_digest
from agent.services.task_recovery_planning_rules import (  # noqa: F401 - re-exported compatibility names
    _RECOVERY_ACTIONS,
    _TERMINAL_GOAL_STATUSES,
    _TERMINAL_TASK_STATUSES,
    RECOVERY_SIGNAL_SCHEMA,
    RECOVERY_STATE_SCHEMA,
    _record_recovery_cut,
    _recovery_context_chars,
)
from agent.services.task_recovery_planning_values import (
    mapping as _mapping,
)
from agent.services.task_recovery_planning_values import (
    sha256_json as _sha256_json,
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
            limit = _recovery_context_chars()
            _record_recovery_cut("recovery.context", bounded, limit)
            return bounded[:limit], {
                "status": "bounded_without_compactor",
                "input_chars": len(title) + len(description) + len(context_text),
                "output_chars": min(limit, len(bounded)),
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
                context_compactor_max_output_chars=_recovery_context_chars(),
                context_compactor_retry_attempts=0,
                context_compactor_fail_open=True,
            ),
        )
        payload = dict(compacted.payload or {})
        payload.pop("compactor_meta", None)
        compact_json = json.dumps(payload, ensure_ascii=False)
        _record_recovery_cut("recovery.context", compact_json, _recovery_context_chars())
        return compact_json[:_recovery_context_chars()], {
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
        return _task_recovery_approval_saga.request_materialization_approval(
            self, repos=repos, plan=plan, nodes=nodes, goal_id=goal_id, source_task_id=source_task_id,
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
        return _task_recovery_approval_saga.mark_source_waiting_for_approval(
            self, source_task=source_task, plan_id=plan_id, approval_request_id=approval_request_id,
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
        return _task_recovery_approval_saga.complete_approval_refresh_saga(
            self, repos=repos, plan_id=plan_id, goal_id=goal_id, source_task_id=source_task_id,
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
        return _task_recovery_approval_saga.refresh_stale_plan_approval(
            self, repos=repos, plan_id=plan_id, goal_id=goal_id, source_task_id=source_task_id,
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
        return _task_recovery_proposal.resume_existing_plan_saga(
            self, repos=repos, plan=plan, source_task=source_task, goal=goal, goal_id=goal_id,
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
        return _task_recovery_release.save_release_state(
            self, repos=repos, plan_id=plan_id, state=state, release_epoch=release_epoch,
            release_details=release_details,
        )

    @staticmethod
    def _release_epoch(
        *,
        plan_id: str,
        approval_id: str,
        recovery_key: str,
        team_id: str,
    ) -> str:
        return _task_recovery_release.release_epoch(
            plan_id=plan_id, approval_id=approval_id, recovery_key=recovery_key, team_id=team_id,
        )

    def _cancel_recovery_children(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None:
        return _task_recovery_release.cancel_recovery_children(
            self, created_task_ids=created_task_ids, plan_id=plan_id, source_task_id=source_task_id,
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
        return _task_recovery_release.release_materialized_recovery(
            self, repos=repos, plan=plan, nodes=nodes, source_task_id=source_task_id, goal_id=goal_id,
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
        return _task_recovery_proposal.propose_after_model_exhaustion(
            self, task=task, strategy_failures=strategy_failures,
        )

    def handle_approval_decision(self, approval: Any) -> dict[str, Any]:
        """Apply a recovery approval decision; no other approval tool is handled."""
        return _task_recovery_approval_decision.handle_approval_decision(self, approval)


_service = TaskRecoveryPlanningService()


def get_task_recovery_planning_service() -> TaskRecoveryPlanningService:
    return _service
