"""Bounded hub-side rollback sequence for SFU broadcast activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol


@dataclass(frozen=True)
class RollbackCommand:
    operation_id: str
    fencing_token: int
    actor: str
    reason: str
    expected_policy_version: int
    deadline: datetime
    authority: str = "hub"


@dataclass(frozen=True)
class RollbackStepResult:
    success: bool
    reason_code: str


@dataclass(frozen=True)
class RollbackResult:
    status: str
    reason_codes: tuple[str, ...]
    completed_steps: tuple[str, ...]


class SfuBroadcastRollbackPort(Protocol):
    """Infrastructure port; operations are idempotent by operation_id."""

    def enable_security_fence(self, command: RollbackCommand) -> RollbackStepResult: ...

    def stop_new_admission(self, command: RollbackCommand) -> RollbackStepResult: ...

    def disable_optional_features(
        self, command: RollbackCommand
    ) -> RollbackStepResult: ...

    def project_parent_fallback(
        self, command: RollbackCommand
    ) -> RollbackStepResult: ...

    def request_graceful_drain(
        self, command: RollbackCommand
    ) -> RollbackStepResult: ...

    def verify_quiesced(self, command: RollbackCommand) -> RollbackStepResult: ...


class HubSfuBroadcastRollbackService:
    """Executes one finite safety sequence; it never creates a worker loop."""

    _STEPS = (
        "enable_security_fence",
        "stop_new_admission",
        "disable_optional_features",
        "project_parent_fallback",
        "request_graceful_drain",
        "verify_quiesced",
    )
    _CRITICAL_STEPS = frozenset(_STEPS[:4])

    def __init__(
        self,
        port: SfuBroadcastRollbackPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._port = port
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, command: RollbackCommand) -> RollbackResult:
        validation_reason = _validate_command(command, self._clock())
        if validation_reason is not None:
            return RollbackResult("failed", (validation_reason,), ())

        completed: list[str] = []
        reasons: set[str] = set()
        critical_failure = False
        for step_name in self._STEPS:
            if self._clock().astimezone(UTC) >= command.deadline.astimezone(UTC):
                reasons.add("rollback_deadline_exceeded")
                critical_failure = step_name in self._CRITICAL_STEPS
                break
            try:
                result = getattr(self._port, step_name)(command)
            except Exception:
                reasons.add(f"rollback_{step_name}_exception")
                critical_failure = step_name in self._CRITICAL_STEPS
                if critical_failure:
                    break
                continue
            if result.success:
                completed.append(step_name)
                continue
            reasons.add(result.reason_code or f"rollback_{step_name}_failed")
            critical_failure = step_name in self._CRITICAL_STEPS
            if critical_failure:
                break

        if not reasons and len(completed) == len(self._STEPS):
            return RollbackResult("completed", (), tuple(completed))
        return RollbackResult(
            "failed" if critical_failure else "partial",
            tuple(sorted(reasons)),
            tuple(completed),
        )


def _validate_command(command: RollbackCommand, now: datetime) -> str | None:
    if command.authority != "hub":
        return "rollback_authority_invalid"
    if not isinstance(command.operation_id, str) or not command.operation_id.strip():
        return "rollback_operation_id_missing"
    if isinstance(command.fencing_token, bool) or command.fencing_token <= 0:
        return "rollback_fencing_token_invalid"
    if not isinstance(command.actor, str) or not command.actor.strip():
        return "rollback_actor_missing"
    if not isinstance(command.reason, str) or not command.reason.strip():
        return "rollback_reason_missing"
    if (
        isinstance(command.expected_policy_version, bool)
        or command.expected_policy_version < 0
    ):
        return "rollback_policy_version_invalid"
    if now.tzinfo is None or command.deadline.tzinfo is None:
        return "rollback_deadline_timezone_missing"
    if command.deadline.astimezone(UTC) <= now.astimezone(UTC):
        return "rollback_deadline_expired"
    return None


__all__ = [
    "HubSfuBroadcastRollbackService",
    "RollbackCommand",
    "RollbackResult",
    "RollbackStepResult",
    "SfuBroadcastRollbackPort",
]
