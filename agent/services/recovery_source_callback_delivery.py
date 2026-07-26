"""Strict callback delivery port for Recovery source finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RecoverySourceCallbackDelivery:
    """Observable result of the canonical post-commit callback path."""

    delivered: bool
    callback_required: bool
    reason_code: str
    status_code: int | None = None


class RecoverySourceCallbackDeliveryPort(Protocol):
    """Deliver one finalized source transition through the callback path."""

    def deliver(
        self,
        task_id: str,
        *,
        old_status: str | None,
        event_type: str,
    ) -> RecoverySourceCallbackDelivery:
        ...


class TaskRuntimeRecoverySourceCallbackDelivery:
    """Adapter to the canonical task-runtime post-commit implementation."""

    def deliver(
        self,
        task_id: str,
        *,
        old_status: str | None,
        event_type: str,
    ) -> RecoverySourceCallbackDelivery:
        from agent.services.task_runtime_service import (
            run_external_task_status_post_commit,
        )

        result = run_external_task_status_post_commit(
            task_id,
            old_status=old_status,
            event_type=event_type,
            force=True,
            synchronous_delivery=True,
            strict_callback_delivery=True,
        )
        if isinstance(result, RecoverySourceCallbackDelivery):
            return result
        return RecoverySourceCallbackDelivery(
            delivered=False,
            callback_required=True,
            reason_code=(
                "recovery_source_callback_invalid_delivery_result"
            ),
        )
