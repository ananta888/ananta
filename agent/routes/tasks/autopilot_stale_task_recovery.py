"""Stale-task sweeps of the autopilot tick.

Before dispatching, every tick resets or fails tasks that stopped
progressing (stale ``proposing``/``assigned``/``in_progress``), retries
machine-recoverable ``waiting_for_review`` tooling failures and times out
stale reviews in fully autonomous runs. Tasks governed by the Hub recovery
saga and active recovery approvals are left untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.tasks.autopilot_task_dispatcher_helpers import (
    _current_task_status,
    _effective_agent_cfg_for_task,
)

# Reset tasks stuck in `proposing` with no output back to `todo` so the
# autopilot can retry them (workers can crash mid-dispatch).
PROPOSING_STALE_SECONDS = 30
ASSIGNED_STALE_SECONDS = 60
IN_PROGRESS_STALE_SECONDS = 120
RECOVER_WAITING_REVIEW_SECONDS = 30
STALE_ACTIVE_MAX_RETRIES = 3
TOOLING_RECOVERY_MAX = 2
FORCE_FAIL_WAITING_REVIEW_SECONDS = 90
WAITING_REVIEW_RETRY_MAX = 2

_RETRYABLE_REVIEW_REASON_CODES = {
    "proposal_budget_exhausted",
    "autopilot_strategy_exhausted",
    "task_propose_hard_guard",
}
_INACTIVE_APPROVAL_STATUSES = {"expired", "denied", "superseded", "consumed"}


def is_hub_managed_model_recovery_task(task: Any) -> bool:
    """Keep generic Autopilot repair loops out of the Hub Recovery saga."""

    if (
        str(getattr(task, "derivation_reason", "") or "").strip()
        == "goal_task_recovery"
    ):
        return True
    for attribute in ("status_reason_details", "verification_status"):
        payload = getattr(task, attribute, None)
        details = dict(payload) if isinstance(payload, dict) else {}
        if any(
            isinstance(details.get(key), dict) and details.get(key)
            for key in (
                "model_recovery",
                "model_recovery_strategy",
                "model_recovery_release",
                "recovery_dispatch_lease",
            )
        ):
            return True
    return False


def refresh_approval_lifecycle() -> Any | None:
    """Expire and reconcile approval requests; ``None`` when the service is unavailable."""
    try:
        from agent.services.approval_request_service import (
            get_approval_request_service,
        )

        approval_lifecycle = get_approval_request_service()
        approval_lifecycle.expire_old_requests()
        approval_lifecycle.reconcile_granted_domain_actions()
        return approval_lifecycle
    except Exception:
        logging.getLogger("agent.routes.tasks.autopilot_tick_engine").warning(
            "approval lifecycle refresh failed during autopilot tick",
            exc_info=True,
        )
        return None


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or "").lower()


def _younger_than(task: Any, now_ts: float, seconds: int) -> bool:
    updated = float(getattr(task, "updated_at", None) or 0)
    return bool(updated) and (now_ts - updated) < seconds


def _is_recoverable_tooling_output(last_output: str) -> bool:
    lowered = last_output.lower()
    return (
        "[tool_intent] unresolved:" in last_output
        or "command not found" in lowered
        or "not recognized as an internal or external command" in lowered
        or "no such file or directory" in lowered
    )


def _bounded_non_negative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


@dataclass
class StaleTaskRecovery:
    """Run the stale-task sweeps for one tick."""

    loop: Any
    append_trace_event: Callable[..., None]
    update_local_task_status: Callable[..., None]
    approval_lifecycle: Any | None
    now_ts: float

    def run(self, tasks: list[Any]) -> None:
        self.reset_stale_proposing(tasks)
        self.retry_stale_active(tasks)
        self.recover_tooling_waiting_review(tasks)
        self.expire_stale_waiting_review(tasks)

    def reset_stale_proposing(self, tasks: list[Any]) -> None:
        for task in tasks:
            if is_hub_managed_model_recovery_task(task) or _status(task) != "proposing":
                continue
            if _younger_than(task, self.now_ts, PROPOSING_STALE_SECONDS) or getattr(task, "last_output", None):
                continue
            self.update_local_task_status(
                task.id,
                "todo",
                event_type="stale_proposing_reset",
                event_actor="autopilot_tick",
                force=True,
            )
            self.append_trace_event(task.id, "stale_proposing_reset", reason="no_output_after_90s")

    def retry_stale_active(self, tasks: list[Any]) -> None:
        """Recover active tasks that stopped progressing without terminal output.

        This keeps autonomous runs moving when worker transport/runtime hangs.
        """
        for task in tasks:
            if is_hub_managed_model_recovery_task(task):
                continue
            status = _status(task)
            if status not in {"assigned", "in_progress"}:
                continue
            stale_after = ASSIGNED_STALE_SECONDS if status == "assigned" else IN_PROGRESS_STALE_SECONDS
            if _younger_than(task, self.now_ts, stale_after):
                continue
            verification = dict(getattr(task, "verification_status", None) or {})
            recovery = dict(verification.get("autopilot_recovery") or {})
            retries = int(recovery.get("stale_active_retries") or 0)
            if retries < STALE_ACTIVE_MAX_RETRIES:
                recovery.update(
                    {
                        "stale_active_retries": retries + 1,
                        "last_stale_active_status": status,
                        "last_stale_active_retry_at": self.now_ts,
                    }
                )
                verification["autopilot_recovery"] = recovery
                self.update_local_task_status(
                    task.id,
                    "todo",
                    verification_status=verification,
                    event_type="stale_active_task_retry",
                    event_actor="autopilot_tick",
                    force=True,
                )
                self.append_trace_event(
                    task.id,
                    "stale_active_task_retry",
                    stale_status=status,
                    retry_attempt=retries + 1,
                    stale_after_seconds=stale_after,
                )
                continue
            self.update_local_task_status(
                task.id,
                "failed",
                error=f"stale_active_task_exhausted:{status}",
                event_type="stale_active_task_auto_failed",
                event_actor="autopilot_tick",
                force=True,
            )
            self.append_trace_event(
                task.id,
                "stale_active_task_auto_failed",
                stale_status=status,
                retry_attempt=retries,
                stale_after_seconds=stale_after,
            )

    def recover_tooling_waiting_review(self, tasks: list[Any]) -> None:
        """Auto-recover waiting_for_review tasks caused by recoverable runtime/tooling issues.

        These are machine-retryable artifacts and should not deadlock the chain.
        In fully autonomous runs (allow_human_review=False) allow up to
        autonomous_repair_attempts retries before failing, to allow round-robin
        assignment to reach a capable worker.
        """
        for task in tasks:
            if is_hub_managed_model_recovery_task(task) or _status(task) != "waiting_for_review":
                continue
            if _younger_than(task, self.now_ts, RECOVER_WAITING_REVIEW_SECONDS):
                continue
            if not _is_recoverable_tooling_output(str(getattr(task, "last_output", None) or "")):
                continue
            self._retry_or_fail_tooling_review(task)

    def _retry_or_fail_tooling_review(self, task: Any) -> None:
        propose_policy = _effective_agent_cfg_for_task(loop=self.loop, task=task).get("propose_policy") or {}
        allow_human_review = bool(propose_policy.get("allow_human_review", True))
        verification = dict(getattr(task, "verification_status", None) or {})
        recovery = dict(verification.get("autopilot_recovery") or {})
        tooling_retries = _bounded_non_negative_int(recovery.get("tooling_retries") or 0, 0)
        max_tooling_retries = _bounded_non_negative_int(
            propose_policy.get("autonomous_repair_attempts", TOOLING_RECOVERY_MAX), TOOLING_RECOVERY_MAX
        )
        can_retry = allow_human_review or tooling_retries < max_tooling_retries
        if can_retry and not allow_human_review:
            recovery["tooling_retries"] = tooling_retries + 1
            recovery["last_tooling_retry_at"] = self.now_ts
            verification["autopilot_recovery"] = recovery
        event = (
            "recover_waiting_review_retryable_failure"
            if can_retry
            else "waiting_for_review_auto_failed_no_human_review"
        )
        self.update_local_task_status(
            task.id,
            "todo" if can_retry else "failed",
            verification_status=verification if not allow_human_review else None,
            error=None if can_retry else "autonomous_run_tooling_retries_exhausted",
            event_type=event,
            event_actor="autopilot_tick",
            force=True,
        )
        self.append_trace_event(
            task.id,
            event,
            reason=(
                "auto_retry_recoverable_waiting_review_failure"
                if can_retry
                else "autonomous_run_waiting_for_review_terminated"
            ),
            allow_human_review=allow_human_review,
            tooling_retries=tooling_retries,
            max_tooling_retries=max_tooling_retries,
        )

    def expire_stale_waiting_review(self, tasks: list[Any]) -> None:
        """Guardrail: stale waiting_for_review tasks must not block goal terminalization.

        For strategy/budget guardrails, prefer controlled retry (todo) before
        hard-failing the task, otherwise autonomous opencode runs can dead-end
        without ever producing executable steps/artifacts.
        """
        for task in tasks:
            if _status(task) != "waiting_for_review":
                continue
            if _younger_than(task, self.now_ts, FORCE_FAIL_WAITING_REVIEW_SECONDS):
                continue
            verification = dict(getattr(task, "verification_status", None) or {})
            model_recovery = dict(verification.get("model_recovery") or {})
            if str(model_recovery.get("status") or "").strip().lower() == "pending_approval":
                self._settle_recovery_approval(task, verification, model_recovery)
                continue
            if is_hub_managed_model_recovery_task(task):
                # Stopped/denied/materialized Recovery sources and Recovery
                # children are governed by the Hub saga, never by the generic
                # stale-review retry/fail policy.
                continue
            self._retry_or_fail_stale_review(task, verification)

    def _settle_recovery_approval(self, task: Any, verification: dict, model_recovery: dict) -> None:
        """Move a stale review to needs_review once its recovery approval is no longer active.

        Approval requests have their own persisted TTL/lifecycle. A generic
        90-second autonomous timeout must not override a human review gate
        and terminalize the source task underneath it.
        """
        approval_id = str(model_recovery.get("approval_request_id") or "").strip()
        approval = (
            self.approval_lifecycle.get_request(approval_id)
            if self.approval_lifecycle is not None and approval_id
            else None
        )
        approval_status = str(getattr(approval, "status", "") or "").strip().lower()
        expires_at = getattr(approval, "expires_at", None)
        if approval_status in {"pending", "granted"} and (expires_at is None or float(expires_at) >= self.now_ts):
            return
        if _current_task_status(task.id, app=self.loop._app) != "waiting_for_review":
            return
        terminal_recovery_status = (
            approval_status if approval_status in _INACTIVE_APPROVAL_STATUSES else "approval_missing"
        )
        model_recovery["status"] = terminal_recovery_status
        verification["model_recovery"] = model_recovery
        self.update_local_task_status(
            task.id,
            "needs_review",
            verification_status=verification,
            status_reason_code=f"recovery_approval_{terminal_recovery_status}",
            event_type="task_recovery_approval_inactive",
            event_actor="autopilot_tick",
            event_details={
                "approval_request_id": approval_id,
                "approval_status": terminal_recovery_status,
            },
            force=True,
        )
        self.append_trace_event(
            task.id,
            "task_recovery_approval_inactive",
            approval_request_id=approval_id,
            approval_status=terminal_recovery_status,
        )

    def _retry_or_fail_stale_review(self, task: Any, verification: dict) -> None:
        reason_code = str(dict(verification.get("autopilot_strategy") or {}).get("reason_code") or "").strip().lower()
        recover = dict(verification.get("autopilot_recovery") or {})
        review_retries = int(recover.get("waiting_review_retries") or 0)
        if reason_code in _RETRYABLE_REVIEW_REASON_CODES and review_retries < WAITING_REVIEW_RETRY_MAX:
            recover.update(
                {
                    "waiting_review_retries": review_retries + 1,
                    "last_waiting_review_retry_at": self.now_ts,
                    "last_waiting_review_reason_code": reason_code,
                }
            )
            verification["autopilot_recovery"] = recover
            self.update_local_task_status(
                task.id,
                "todo",
                verification_status=verification,
                manual_override_until=self.now_ts + 20,
                event_type="waiting_for_review_retry_scheduled",
                event_actor="autopilot_tick",
                force=True,
            )
            self.append_trace_event(
                task.id,
                "waiting_for_review_retry_scheduled",
                reason_code=reason_code,
                retry_attempt=review_retries + 1,
                retry_max=WAITING_REVIEW_RETRY_MAX,
            )
            return
        self.update_local_task_status(
            task.id,
            "failed",
            error="waiting_for_review_timeout_auto_failed",
            event_type="waiting_for_review_timeout_auto_failed",
            event_actor="autopilot_tick",
            force=True,
        )
        self.append_trace_event(
            task.id,
            "waiting_for_review_timeout_auto_failed",
            reason="auto_fail_stale_waiting_for_review",
            timeout_seconds=FORCE_FAIL_WAITING_REVIEW_SECONDS,
        )
