"""Hub-owned execution of ordered post-model recovery strategies.

Workers report bounded exhaustion facts.  This service interprets the
Hub-authored recovery policy and delegates plan persistence to the existing
recovery-planning saga.  It never runs provider attempts and never gives a
worker task-queue ownership.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent.config import settings


class RecoveryPlanningPort(Protocol):
    """Narrow port required by the strategy executor."""

    def resolve_recovery_policy(
        self,
        task: Any,
    ) -> tuple[list[str], bool, str]: ...

    def summarize_exhaustion_signal(
        self,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None: ...

    def compact_context_for_recovery(
        self,
        task: Any,
        *,
        actions: list[str],
    ) -> tuple[str, dict[str, Any]]: ...

    def propose_after_model_exhaustion(
        self,
        *,
        task: Any,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RecoveryStrategyOutcome:
    status: str
    reason_code: str
    actions: tuple[str, ...]
    policy_hash: str
    terminal_model_chain_handled: bool = True
    compaction: dict[str, Any] | None = None
    compacted_context_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "reason_code": self.reason_code,
            "recovery_actions": list(self.actions),
            "policy_hash": self.policy_hash,
            "terminal_model_chain_handled": (
                self.terminal_model_chain_handled
            ),
        }
        if self.compaction is not None:
            payload["compaction"] = dict(self.compaction)
        if self.compacted_context_hash:
            payload["compacted_context_hash"] = (
                self.compacted_context_hash
            )
        return payload


class ModelRecoveryStrategyExecutor:
    """Execute one bounded Hub policy after a verified terminal model chain."""

    _PLAN_ACTIONS = frozenset(
        {"segment_planning", "propose_task_plan"}
    )

    def __init__(
        self,
        *,
        role_provider: Callable[[], str] | None = None,
        planning_port_provider: (
            Callable[[], RecoveryPlanningPort] | None
        ) = None,
    ) -> None:
        self._role_provider = role_provider or (
            lambda: str(settings.role or "")
        )
        self._planning_port_provider = (
            planning_port_provider
            or self._default_planning_port
        )

    @staticmethod
    def _default_planning_port() -> RecoveryPlanningPort:
        from agent.services.task_recovery_planning_service import (
            get_task_recovery_planning_service,
        )

        return get_task_recovery_planning_service()

    def execute_after_model_exhaustion(
        self,
        *,
        task: Any,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        if str(self._role_provider() or "").strip().lower() != "hub":
            return {
                "status": "ignored",
                "reason_code": "hub_role_required",
                "terminal_model_chain_handled": False,
            }

        planning = self._planning_port_provider()
        signal = planning.summarize_exhaustion_signal(
            strategy_failures
        )
        if signal is None:
            return {
                "status": "ignored",
                "reason_code": "model_exhaustion_signal_required",
                "terminal_model_chain_handled": False,
            }

        actions, approval_required, policy_hash = (
            planning.resolve_recovery_policy(task)
        )
        normalized_actions = tuple(
            str(action).strip()
            for action in actions
            if str(action).strip()
        )
        if not normalized_actions:
            return RecoveryStrategyOutcome(
                status="stopped",
                reason_code="model_recovery_disabled",
                actions=normalized_actions,
                policy_hash=policy_hash,
            ).as_dict()

        plan_requested = bool(
            self._PLAN_ACTIONS.intersection(normalized_actions)
        )
        if plan_requested:
            if (
                "require_approval" not in normalized_actions
                or not approval_required
            ):
                return RecoveryStrategyOutcome(
                    status="stopped",
                    reason_code="recovery_plan_approval_required",
                    actions=normalized_actions,
                    policy_hash=policy_hash,
                ).as_dict()
            delegated = dict(
                planning.propose_after_model_exhaustion(
                    task=task,
                    strategy_failures=strategy_failures,
                )
                or {}
            )
            delegated.setdefault("status", "failed")
            delegated.setdefault(
                "reason_code",
                "recovery_plan_generation_failed",
            )
            delegated["recovery_actions"] = list(
                normalized_actions
            )
            delegated["policy_hash"] = policy_hash
            delegated["terminal_model_chain_handled"] = True
            return delegated

        compaction: dict[str, Any] | None = None
        compacted_context_hash: str | None = None
        if "compact_context" in normalized_actions:
            try:
                compacted_context, raw_meta = (
                    planning.compact_context_for_recovery(
                        task,
                        actions=list(normalized_actions),
                    )
                )
                compaction = {
                    str(key): value
                    for key, value in dict(raw_meta or {}).items()
                    if str(key)
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
                compacted_context_hash = hashlib.sha256(
                    str(compacted_context or "").encode("utf-8")
                ).hexdigest()
            except Exception:
                return RecoveryStrategyOutcome(
                    status="failed",
                    reason_code="context_compaction_failed",
                    actions=normalized_actions,
                    policy_hash=policy_hash,
                ).as_dict()

        reason_code = (
            "model_recovery_stop_selected"
            if "stop" in normalized_actions
            else "model_recovery_strategies_exhausted"
        )
        return RecoveryStrategyOutcome(
            status="stopped",
            reason_code=reason_code,
            actions=normalized_actions,
            policy_hash=policy_hash,
            compaction=compaction,
            compacted_context_hash=compacted_context_hash,
        ).as_dict()


_SERVICE = ModelRecoveryStrategyExecutor()


def get_model_recovery_strategy_executor(
) -> ModelRecoveryStrategyExecutor:
    return _SERVICE
