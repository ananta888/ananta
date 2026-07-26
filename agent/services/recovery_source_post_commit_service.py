"""Durable replay boundary for Recovery source post-commit effects."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.services.recovery_source_callback_delivery import (
    RecoverySourceCallbackDeliveryPort,
    TaskRuntimeRecoverySourceCallbackDelivery,
)
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from agent.services.task_status_service import normalize_task_status


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class RecoverySourcePostCommitDecision:
    delivered: bool
    reason_code: str
    transition_id: str | None = None


class RecoverySourcePostCommitService:
    """Claim, execute, and acknowledge idempotent terminal side effects."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        mutation_lock_provider: Callable[[], Any] | None = None,
        callback_delivery_port: (
            RecoverySourceCallbackDeliveryPort | None
        ) = None,
        retry_after_seconds: float = 30.0,
        attempt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._mutation_lock_provider = mutation_lock_provider
        self._callback_delivery_port = (
            callback_delivery_port
            if callback_delivery_port is not None
            else TaskRuntimeRecoverySourceCallbackDelivery()
        )
        self._retry_after_seconds = max(
            1.0,
            float(retry_after_seconds),
        )
        self._attempt_id_factory = (
            attempt_id_factory
            if attempt_id_factory is not None
            else lambda: secrets.token_hex(16)
        )

    def _repos(self):
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        return get_repository_registry()

    def _locks(self):
        if self._mutation_lock_provider is not None:
            return self._mutation_lock_provider()
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port()

    def deliver_if_pending(
        self,
        task_id: str,
    ) -> RecoverySourcePostCommitDecision:
        normalized_id = str(task_id or "").strip()
        repos = self._repos()
        now = time.time()
        with self._locks().mutation_lock(normalized_id) as acquired:
            if not acquired:
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_lock_unavailable",
                )
            task = repos.task_repo.get_by_id(normalized_id)
            details = _mapping(
                getattr(task, "status_reason_details", None)
            )
            marker = _mapping(
                details.get("recovery_source_post_commit")
            )
            if (
                task is None
                or recovery_task_role(task) != "source"
                or normalize_task_status(
                    getattr(task, "status", None)
                )
                not in {"completed", "verification_failed"}
                or not marker
            ):
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_not_pending",
                )
            transition_id = str(
                marker.get("transition_id") or ""
            ).strip() or None
            state = str(marker.get("state") or "").strip().lower()
            if state == "completed":
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_completed",
                    transition_id,
                )
            processing_at = float(
                marker.get("processing_at") or 0.0
            )
            if (
                state == "processing"
                and now - processing_at < self._retry_after_seconds
            ):
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_inflight",
                    transition_id,
                )
            attempt_id = str(
                self._attempt_id_factory() or ""
            ).strip()
            if (
                not attempt_id
                or attempt_id
                == str(marker.get("attempt_id") or "")
            ):
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_attempt_id_invalid",
                    transition_id,
                )
            previous_marker = dict(marker)
            marker.update(
                {
                    "state": "processing",
                    "processing_at": now,
                    "attempt_id": attempt_id,
                    "attempt_count": int(
                        marker.get("attempt_count") or 0
                    )
                    + 1,
                }
            )
            details["recovery_source_post_commit"] = marker
            task.status_reason_details = details
            task.updated_at = now
            from agent.common.recovery_source_post_commit_write_boundary import (
                authorize_recovery_source_post_commit_write,
            )

            with authorize_recovery_source_post_commit_write(
                task_id=normalized_id,
                current=previous_marker,
                proposed=marker,
            ):
                persisted = (
                    repos.task_repo.save(task)
                    or repos.task_repo.get_by_id(normalized_id)
                )
            marker = _mapping(
                _mapping(
                    getattr(
                        persisted,
                        "status_reason_details",
                        None,
                    )
                ).get("recovery_source_post_commit")
            )
            if not self._owns_processing_claim(
                marker,
                transition_id=transition_id,
                attempt_id=attempt_id,
            ):
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_superseded",
                    transition_id,
                )

        try:
            callback_delivery = (
                self._callback_delivery_port.deliver(
                    normalized_id,
                    old_status=str(
                        marker.get("old_status") or ""
                    )
                    or None,
                    event_type="recovery_source_finalized",
                )
            )
            if not callback_delivery.delivered:
                raise RuntimeError(
                    callback_delivery.reason_code
                    or "recovery_source_callback_delivery_failed"
                )
        except Exception as exc:
            failure_recorded = self._record_failure(
                task_id=normalized_id,
                transition_id=transition_id,
                attempt_id=attempt_id,
                error=str(exc),
            )
            return RecoverySourcePostCommitDecision(
                False,
                (
                    "recovery_source_post_commit_failed"
                    if failure_recorded
                    else "recovery_source_post_commit_superseded"
                ),
                transition_id,
            )

        with self._locks().mutation_lock(normalized_id) as acquired:
            if not acquired:
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_ack_lock_unavailable",
                    transition_id,
                )
            task = repos.task_repo.get_by_id(normalized_id)
            details = _mapping(
                getattr(task, "status_reason_details", None)
            )
            current = _mapping(
                details.get("recovery_source_post_commit")
            )
            if not self._owns_processing_claim(
                current,
                transition_id=transition_id,
                attempt_id=attempt_id,
            ):
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_superseded",
                    transition_id,
                )
            previous_marker = dict(current)
            current.update(
                {
                    "state": "completed",
                    "completed_at": time.time(),
                    "last_error": None,
                }
            )
            details["recovery_source_post_commit"] = current
            task.status_reason_details = details
            task.updated_at = time.time()
            from agent.common.recovery_source_post_commit_write_boundary import (
                authorize_recovery_source_post_commit_write,
            )

            with authorize_recovery_source_post_commit_write(
                task_id=normalized_id,
                current=previous_marker,
                proposed=current,
            ):
                persisted = (
                    repos.task_repo.save(task)
                    or repos.task_repo.get_by_id(normalized_id)
                )
            persisted_marker = _mapping(
                _mapping(
                    getattr(
                        persisted,
                        "status_reason_details",
                        None,
                    )
                ).get("recovery_source_post_commit")
            )
            if not (
                str(persisted_marker.get("state") or "")
                == "completed"
                and str(
                    persisted_marker.get("transition_id") or ""
                )
                == str(transition_id or "")
                and str(
                    persisted_marker.get("attempt_id") or ""
                )
                == attempt_id
                and persisted_marker.get("last_error") is None
            ):
                return RecoverySourcePostCommitDecision(
                    False,
                    "recovery_source_post_commit_superseded",
                    transition_id,
                )
        return RecoverySourcePostCommitDecision(
            True,
            "recovery_source_post_commit_delivered",
            transition_id,
        )

    def _record_failure(
        self,
        *,
        task_id: str,
        transition_id: str | None,
        attempt_id: str,
        error: str,
    ) -> bool:
        repos = self._repos()
        with self._locks().mutation_lock(task_id) as acquired:
            if not acquired:
                return False
            task = repos.task_repo.get_by_id(task_id)
            if task is None:
                return False
            details = _mapping(
                getattr(task, "status_reason_details", None)
            )
            marker = _mapping(
                details.get("recovery_source_post_commit")
            )
            if not self._owns_processing_claim(
                marker,
                transition_id=transition_id,
                attempt_id=attempt_id,
            ):
                return False
            previous_marker = dict(marker)
            marker.update(
                {
                    "state": "pending",
                    "last_error": str(error or "")[:500],
                    "failed_at": time.time(),
                }
            )
            details["recovery_source_post_commit"] = marker
            task.status_reason_details = details
            task.updated_at = time.time()
            from agent.common.recovery_source_post_commit_write_boundary import (
                authorize_recovery_source_post_commit_write,
            )

            with authorize_recovery_source_post_commit_write(
                task_id=task_id,
                current=previous_marker,
                proposed=marker,
            ):
                persisted = (
                    repos.task_repo.save(task)
                    or repos.task_repo.get_by_id(task_id)
                )
            persisted_marker = _mapping(
                _mapping(
                    getattr(
                        persisted,
                        "status_reason_details",
                        None,
                    )
                ).get("recovery_source_post_commit")
            )
            return bool(
                str(persisted_marker.get("state") or "")
                == "pending"
                and str(
                    persisted_marker.get("transition_id") or ""
                )
                == str(transition_id or "")
                and str(
                    persisted_marker.get("attempt_id") or ""
                )
                == attempt_id
                and str(
                    persisted_marker.get("last_error") or ""
                )
                == str(error or "")[:500]
            )

    @staticmethod
    def _owns_processing_claim(
        marker: dict[str, Any],
        *,
        transition_id: str | None,
        attempt_id: str,
    ) -> bool:
        """Return whether this delivery still owns the persisted CAS claim."""

        return bool(
            str(marker.get("state") or "").strip().lower()
            == "processing"
            and str(marker.get("transition_id") or "")
            == str(transition_id or "")
            and str(marker.get("attempt_id") or "")
            == str(attempt_id or "")
        )


_service = RecoverySourcePostCommitService()


def get_recovery_source_post_commit_service() -> (
    RecoverySourcePostCommitService
):
    return _service
