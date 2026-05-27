from __future__ import annotations

import time
from typing import Any

from agent.common.audit import log_audit
from agent.services.execution_audit_service import get_execution_audit_service


class WorkflowTransitionError(ValueError):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None):
        self.code = str(code or "workflow_transition_error")
        self.details = dict(details or {})
        super().__init__(self.code)


class CriticalWorkflowStateService:
    _DEFINITIONS: dict[str, dict[str, Any]] = {
        "evolution_proposal": {
            "initial_state": "review_required",
            "terminal_states": {"rejected", "applied", "failed"},
            "active_states": {"review_required", "approved", "apply_requested", "apply_in_progress", "apply_prepared", "blocked"},
            "transitions": {
                "review_required": {"approved", "rejected", "failed", "blocked"},
                "approved": {"apply_requested", "failed", "blocked"},
                "apply_requested": {"apply_in_progress", "blocked", "failed"},
                "apply_in_progress": {"apply_prepared", "applied", "blocked", "failed"},
                "apply_prepared": {"apply_in_progress", "applied", "blocked", "failed"},
                "blocked": {"apply_requested", "failed"},
                "rejected": set(),
                "applied": set(),
                "failed": set(),
                "timeout": {"blocked", "failed"},
            },
            "fallback_state": "blocked",
            "timeout_seconds": 300,
            "max_recovery_attempts": 1,
        },
        "repair_execution": {
            "initial_state": "detected",
            "terminal_states": {"succeeded", "failed", "escalated"},
            "active_states": {"detected", "diagnosing", "proposing", "approval_required", "executing", "verifying"},
            "transitions": {
                "detected": {"diagnosing"},
                "diagnosing": {"proposing", "failed", "escalated"},
                "proposing": {"approval_required", "executing", "failed", "escalated"},
                "approval_required": {"executing", "failed"},
                "executing": {"verifying", "failed"},
                "verifying": {"succeeded", "failed", "escalated"},
                "succeeded": set(),
                "failed": set(),
                "escalated": set(),
                "timeout": {"failed", "escalated"},
            },
            "fallback_state": "failed",
            "timeout_seconds": 300,
            "max_recovery_attempts": 1,
        },
    }

    def initialize(
        self,
        workflow_type: str,
        *,
        state: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        definition = self._definition(workflow_type)
        timestamp = float(now if now is not None else time.time())
        resolved_state = str(state or definition["initial_state"]).strip().lower()
        return {
            "schema": "critical_workflow_state.v1",
            "workflow_type": workflow_type,
            "state": resolved_state,
            "started_at": timestamp,
            "last_transition_at": timestamp,
            "transition_count": 0,
            "recovery_attempts": 0,
            "timeout_seconds": int(definition["timeout_seconds"]),
            "max_recovery_attempts": int(definition["max_recovery_attempts"]),
            "history": [],
        }

    def transition(
        self,
        record: dict[str, Any] | None,
        *,
        workflow_type: str,
        to_state: str,
        reason: str,
        actor: str = "system",
        trace_id: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        guards: dict[str, bool] | None = None,
        details: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        definition = self._definition(workflow_type)
        current = self._materialize_record(record, workflow_type=workflow_type, now=now)
        source = str(current.get("state") or "").strip().lower()
        target = str(to_state or "").strip().lower()
        timestamp = float(now if now is not None else time.time())

        self._enforce_guards(source=source, target=target, guards=guards)
        allowed = set(definition["transitions"].get(source) or set())
        if target == "timeout" and source in set(definition.get("active_states") or set()):
            allowed.add("timeout")
        if target != source and target not in allowed:
            raise WorkflowTransitionError(
                "workflow_invalid_transition",
                details={"workflow_type": workflow_type, "from_state": source, "to_state": target},
            )

        event = {
            "event_type": "workflow_transition",
            "from_state": source,
            "to_state": target,
            "reason": str(reason or "unspecified").strip() or "unspecified",
            "actor": str(actor or "system"),
            "timestamp": timestamp,
            "details": dict(details or {}),
        }
        history = list(current.get("history") or [])
        history.append(event)
        current["history"] = history[-50:]
        current["state"] = target
        current["last_transition_at"] = timestamp
        current["transition_count"] = int(current.get("transition_count") or 0) + 1

        get_execution_audit_service().emit_workflow_transition(
            trace_id=trace_id,
            task_id=task_id,
            goal_id=goal_id,
            from_state=source,
            to_state=target,
            trigger=event["reason"],
            policy_context=workflow_type,
            actor_role="hub",
            details={"workflow_type": workflow_type, "actor": event["actor"], **dict(details or {})},
        )
        log_audit(
            "critical_workflow_transition",
            {
                "workflow_type": workflow_type,
                "from_state": source,
                "to_state": target,
                "reason": event["reason"],
                "actor": event["actor"],
                "task_id": task_id,
                "goal_id": goal_id,
                "trace_id": trace_id,
            },
        )
        return current

    def apply_fallback(
        self,
        record: dict[str, Any] | None,
        *,
        workflow_type: str,
        reason: str,
        cause: str,
        actor: str = "system",
        trace_id: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        details: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        definition = self._definition(workflow_type)
        fallback_state = str(definition.get("fallback_state") or "").strip().lower()
        if not fallback_state:
            raise WorkflowTransitionError("workflow_fallback_not_configured", details={"workflow_type": workflow_type})
        return self.transition(
            record,
            workflow_type=workflow_type,
            to_state=fallback_state,
            reason=str(reason or "fallback").strip() or "fallback",
            actor=actor,
            trace_id=trace_id,
            task_id=task_id,
            goal_id=goal_id,
            details={"fallback_cause": str(cause or "unknown"), **dict(details or {})},
            now=now,
        )

    def inspect_timeout(
        self,
        record: dict[str, Any] | None,
        *,
        workflow_type: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        definition = self._definition(workflow_type)
        current = self._materialize_record(record, workflow_type=workflow_type, now=now)
        state = str(current.get("state") or "").strip().lower()
        timestamp = float(now if now is not None else time.time())
        elapsed = max(0.0, timestamp - float(current.get("last_transition_at") or current.get("started_at") or timestamp))
        timeout_seconds = int(current.get("timeout_seconds") or definition["timeout_seconds"])
        stuck = state in set(definition["active_states"]) and elapsed > float(timeout_seconds)
        return {
            "stuck": bool(stuck),
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": round(elapsed, 3),
            "state": state,
            "recovery_attempts": int(current.get("recovery_attempts") or 0),
            "max_recovery_attempts": int(current.get("max_recovery_attempts") or definition["max_recovery_attempts"]),
        }

    def handle_timeout(
        self,
        record: dict[str, Any] | None,
        *,
        workflow_type: str,
        reason: str,
        actor: str = "system",
        trace_id: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = self._materialize_record(record, workflow_type=workflow_type, now=now)
        inspection = self.inspect_timeout(current, workflow_type=workflow_type, now=now)
        if not inspection["stuck"]:
            return current
        timed = self.transition(
            current,
            workflow_type=workflow_type,
            to_state="timeout",
            reason=str(reason or "workflow_timeout").strip() or "workflow_timeout",
            actor=actor,
            trace_id=trace_id,
            task_id=task_id,
            goal_id=goal_id,
            details={"elapsed_seconds": inspection["elapsed_seconds"], "timeout_seconds": inspection["timeout_seconds"]},
            now=now,
        )
        attempts = int(timed.get("recovery_attempts") or 0)
        max_attempts = int(timed.get("max_recovery_attempts") or 1)
        if attempts >= max_attempts:
            return self.transition(
                timed,
                workflow_type=workflow_type,
                to_state="failed",
                reason="timeout_recovery_exhausted",
                actor=actor,
                trace_id=trace_id,
                task_id=task_id,
                goal_id=goal_id,
                details={"recovery_attempts": attempts, "max_recovery_attempts": max_attempts},
                now=now,
            )
        timed["recovery_attempts"] = attempts + 1
        return self.apply_fallback(
            timed,
            workflow_type=workflow_type,
            reason="timeout_recovery_fallback",
            cause="workflow_timeout",
            actor=actor,
            trace_id=trace_id,
            task_id=task_id,
            goal_id=goal_id,
            details={"recovery_attempts": timed["recovery_attempts"], "max_recovery_attempts": max_attempts},
            now=now,
        )

    def replay(self, record: dict[str, Any] | None, *, workflow_type: str) -> dict[str, Any]:
        definition = self._definition(workflow_type)
        current = self._materialize_record(record, workflow_type=workflow_type)
        history = list(current.get("history") or [])
        initial_state = str(definition["initial_state"])
        path = [initial_state]
        errors: list[str] = []
        state = initial_state
        for event in history:
            if str(event.get("event_type") or "") != "workflow_transition":
                continue
            source = str(event.get("from_state") or "").strip().lower()
            target = str(event.get("to_state") or "").strip().lower()
            if source != state:
                errors.append(f"state_mismatch:{source}:{state}")
                state = source
            allowed = set(definition["transitions"].get(source) or set())
            if target != source and target not in allowed:
                errors.append(f"invalid_transition:{source}->{target}")
            state = target
            path.append(state)
        return {
            "workflow_type": workflow_type,
            "valid": len(errors) == 0,
            "state_path": path,
            "transition_count": len([event for event in history if str(event.get("event_type") or "") == "workflow_transition"]),
            "terminal_reached": state in set(definition["terminal_states"]),
            "errors": errors,
            "current_state": str(current.get("state") or state),
        }

    def _materialize_record(
        self,
        record: dict[str, Any] | None,
        *,
        workflow_type: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        if isinstance(record, dict):
            payload = dict(record)
            payload["workflow_type"] = workflow_type
            payload.setdefault("history", [])
            payload.setdefault("transition_count", 0)
            payload.setdefault("recovery_attempts", 0)
            return payload
        return self.initialize(workflow_type, now=now)

    def materialize_record(
        self,
        record: dict[str, Any] | None,
        *,
        workflow_type: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        return self._materialize_record(record, workflow_type=workflow_type, now=now)

    def _definition(self, workflow_type: str) -> dict[str, Any]:
        key = str(workflow_type or "").strip().lower()
        definition = self._DEFINITIONS.get(key)
        if definition is None:
            raise WorkflowTransitionError("workflow_type_not_supported", details={"workflow_type": key})
        return definition

    @staticmethod
    def _enforce_guards(*, source: str, target: str, guards: dict[str, bool] | None) -> None:
        for guard_name, guard_passed in dict(guards or {}).items():
            if bool(guard_passed):
                continue
            raise WorkflowTransitionError(
                "workflow_guard_blocked",
                details={"guard": str(guard_name), "from_state": source, "to_state": target},
            )


_service = CriticalWorkflowStateService()


def get_critical_workflow_state_service() -> CriticalWorkflowStateService:
    return _service
