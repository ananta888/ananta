"""Hub-owned one-shot reconciliation for stale Cleanup reservations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import time
from typing import Any, Callable, Mapping
import uuid

from ananta_contracts.unsloth_task import canonical_unsloth_json
from agent.services.unsloth_storage_governance_service import (
    StorageCleanupAdmissionPort,
    UnslothStorageError,
    cleanup_plan_from_submission,
)
from agent.services.unsloth_mutation_command_service import (
    UnslothMutationError,
)
from agent.services.unsloth_task_port import HubTaskSubmissionPort


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AUDIT_EVENT = "unsloth_cleanup_reservation_reconciled"


@dataclass(frozen=True, slots=True)
class UnslothCleanupReservationPolicy:
    stale_after_seconds: float = 30.0
    max_age_seconds: float = 86_400.0
    lease_seconds: float = 30.0
    batch_limit: int = 32

    def __post_init__(self) -> None:
        if (
            not 1.0 <= self.stale_after_seconds <= 86_400.0
            or not self.stale_after_seconds
            <= self.max_age_seconds
            <= 2_592_000.0
            or not 5.0 <= self.lease_seconds <= 300.0
            or not 1 <= self.batch_limit <= 500
        ):
            raise ValueError(
                "unsloth_cleanup_reservation_policy_invalid"
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "UnslothCleanupReservationPolicy":
        raw = dict(value or {})
        nested = raw.get("cleanup_reservation_reconciliation")
        source = dict(nested) if isinstance(nested, Mapping) else raw
        stale = _bounded_float(
            source.get("cleanup_reservation_stale_seconds"),
            default=30.0,
            minimum=1.0,
            maximum=86_400.0,
        )
        return cls(
            stale_after_seconds=stale,
            max_age_seconds=max(
                stale,
                _bounded_float(
                    source.get("cleanup_reservation_max_age_seconds"),
                    default=86_400.0,
                    minimum=stale,
                    maximum=2_592_000.0,
                ),
            ),
            lease_seconds=_bounded_float(
                source.get("cleanup_reservation_lease_seconds"),
                default=30.0,
                minimum=5.0,
                maximum=300.0,
            ),
            batch_limit=_bounded_int(
                source.get("cleanup_reservation_batch_limit"),
                default=32,
                minimum=1,
                maximum=500,
            ),
        )


class UnslothCleanupReservationReconciler:
    """Repair stale Hub reservations without polling or executing Workers."""

    def __init__(
        self,
        *,
        tasks: HubTaskSubmissionPort,
        catalog: StorageCleanupAdmissionPort,
        policy: UnslothCleanupReservationPolicy | None = None,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
        is_hub: Callable[[], bool] | None = None,
    ) -> None:
        self._tasks = tasks
        self._catalog = catalog
        self._policy = policy or UnslothCleanupReservationPolicy()
        self._audit = audit or _noop_audit
        self._clock = clock
        self._is_hub = is_hub or (lambda: True)

    def run_once(
        self,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]:
        bounded = max(
            1,
            min(int(limit or self._policy.batch_limit), 500),
        )
        if not self._is_hub():
            return {
                "hub_only": False,
                "scanned": 0,
                "leased": 0,
                "activated": 0,
                "rejected": 0,
                "deferred": 0,
                "conflicts": 0,
                "invalid": 0,
                "errors": [],
            }
        now = float(self._clock())
        candidates = self._tasks.list_stale_reserved_cleanup(
            before=now - self._policy.stale_after_seconds,
            limit=min(500, bounded * 4),
        )
        summary: dict[str, Any] = {
            "hub_only": True,
            "scanned": len(candidates),
            "leased": 0,
            "activated": 0,
            "rejected": 0,
            "deferred": 0,
            "conflicts": 0,
            "invalid": 0,
            "errors": [],
        }
        for candidate in candidates:
            if summary["leased"] >= bounded:
                break
            task_id = str(candidate.get("task_id") or "")
            lease_owner = f"ucr-{uuid.uuid4().hex}"
            if not self._tasks.lease_reserved(
                task_id,
                lease_owner=lease_owner,
                now=now,
                lease_until=now + self._policy.lease_seconds,
            ):
                summary["conflicts"] += 1
                continue
            summary["leased"] += 1
            try:
                outcome = self._reconcile_leased(
                    task_id=task_id,
                    lease_owner=lease_owner,
                    now=now,
                )
            except Exception as exc:
                summary["deferred"] += 1
                summary["errors"].append(
                    {
                        "task_id": task_id,
                        "reason_code": (
                            "unsloth_cleanup_reservation_reconcile_failed"
                        ),
                        "error_type": type(exc).__name__,
                    }
                )
                self._emit_audit(
                    task_id=task_id,
                    tenant_id="",
                    plan_sha256="",
                    reason_sha256="",
                    age_seconds=0.0,
                    outcome="deferred",
                    reason_code=(
                        "unsloth_cleanup_reservation_reconcile_failed"
                    ),
                )
                continue
            summary[outcome] += 1
        return summary

    def _reconcile_leased(
        self,
        *,
        task_id: str,
        lease_owner: str,
        now: float,
    ) -> str:
        submission = self._tasks.get_submission(task_id)
        if submission is None:
            return "conflicts"
        payload = submission.get("payload")
        tenant_id = str(submission.get("tenant_id") or "")
        created_at = float(submission.get("created_at") or 0.0)
        age_seconds = max(0.0, now - created_at)
        if (
            submission.get("status") != "reserved"
            or submission.get("task_type") != "ml.storage.cleanup"
            or submission.get("result_handler")
            != "unsloth_storage_cleanup_v1"
            or not tenant_id
            or not isinstance(payload, Mapping)
            or _SHA256.fullmatch(
                str(payload.get("reason_sha256") or "")
            )
            is None
            or _SHA256.fullmatch(
                str(submission.get("payload_sha256") or "")
            )
            is None
        ):
            return self._invalid_submission(
                task_id=task_id,
                tenant_id=tenant_id,
                payload=payload,
                submission=submission,
                age_seconds=age_seconds,
            )
        try:
            encoded_payload = canonical_unsloth_json(payload)
        except (TypeError, ValueError):
            return self._invalid_submission(
                task_id=task_id,
                tenant_id=tenant_id,
                payload=payload,
                submission=submission,
                age_seconds=age_seconds,
            )
        if not _constant_time_equal(
            hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest(),
            str(submission["payload_sha256"]),
        ):
            return self._invalid_submission(
                task_id=task_id,
                tenant_id=tenant_id,
                payload=payload,
                submission=submission,
                age_seconds=age_seconds,
            )
        try:
            plan = cleanup_plan_from_submission(
                tenant_id=tenant_id,
                task_id=task_id,
                payload=payload,
            )
        except (
            TypeError,
            ValueError,
            UnslothMutationError,
            UnslothStorageError,
        ):
            return self._invalid_submission(
                task_id=task_id,
                tenant_id=tenant_id,
                payload=payload,
                submission=submission,
                age_seconds=age_seconds,
            )
        reason_sha256 = str(payload["reason_sha256"])
        payload_sha256 = str(submission["payload_sha256"])
        if age_seconds > self._policy.max_age_seconds:
            return self._release_and_reject(
                task_id=task_id,
                lease_owner=lease_owner,
                plan=plan,
                reason_sha256=reason_sha256,
                age_seconds=age_seconds,
                reason_code="unsloth_cleanup_reservation_expired",
                payload_sha256=payload_sha256,
            )
        try:
            catalog_revision = self._catalog.mark_cleanup_queued(
                plan=plan,
                task_id=task_id,
            )
        except UnslothStorageError as exc:
            return self._release_and_reject(
                task_id=task_id,
                lease_owner=lease_owner,
                plan=plan,
                reason_sha256=reason_sha256,
                age_seconds=age_seconds,
                reason_code=exc.reason_code,
                payload_sha256=payload_sha256,
            )
        if not self._tasks.activate_reserved(
            task_id,
            lease_owner=lease_owner,
        ):
            current = self._tasks.get_submission(task_id)
            if current is not None and current.get("status") in {
                "todo",
                "created",
                "assigned",
                "in_progress",
                "delegated",
                "completed",
            }:
                return "activated"
            return "conflicts"
        self._emit_audit(
            task_id=task_id,
            tenant_id=tenant_id,
            plan_sha256=plan.plan_sha256,
            reason_sha256=reason_sha256,
            age_seconds=age_seconds,
            outcome="activated",
            reason_code="unsloth_cleanup_reservation_activated",
            catalog_revision_after=catalog_revision,
            payload_sha256=payload_sha256,
        )
        return "activated"

    def _release_and_reject(
        self,
        *,
        task_id: str,
        lease_owner: str,
        plan,
        reason_sha256: str,
        age_seconds: float,
        reason_code: str,
        payload_sha256: str,
    ) -> str:
        catalog_revision = self._catalog.release_cleanup_queued(
            plan=plan,
            task_id=task_id,
        )
        if not self._tasks.reject_reserved(
            task_id,
            reason_code=reason_code,
            lease_owner=lease_owner,
        ):
            return "conflicts"
        self._emit_audit(
            task_id=task_id,
            tenant_id=plan.tenant_id,
            plan_sha256=plan.plan_sha256,
            reason_sha256=reason_sha256,
            age_seconds=age_seconds,
            outcome="rejected",
            reason_code=reason_code,
            catalog_revision_after=catalog_revision,
            payload_sha256=payload_sha256,
        )
        return "rejected"

    def _invalid_submission(
        self,
        *,
        task_id: str,
        tenant_id: str,
        payload: object,
        submission: Mapping[str, object],
        age_seconds: float,
    ) -> str:
        raw_payload = payload if isinstance(payload, Mapping) else {}
        self._emit_audit(
            task_id=task_id,
            tenant_id=tenant_id,
            plan_sha256=str(
                raw_payload.get("plan_sha256") or ""
            ),
            reason_sha256=str(
                raw_payload.get("reason_sha256") or ""
            ),
            age_seconds=age_seconds,
            outcome="invalid",
            reason_code=(
                "unsloth_cleanup_reservation_contract_invalid"
            ),
            payload_sha256=str(
                submission.get("payload_sha256") or ""
            ),
        )
        return "invalid"

    def _emit_audit(
        self,
        *,
        task_id: str,
        tenant_id: str,
        plan_sha256: str,
        reason_sha256: str,
        age_seconds: float,
        outcome: str,
        reason_code: str,
        catalog_revision_after: int | None = None,
        payload_sha256: str = "",
    ) -> None:
        self._audit(
            _AUDIT_EVENT,
            {
                "task_id": task_id,
                "tenant_id_sha256": _sha256(tenant_id),
                "plan_sha256": plan_sha256,
                "payload_sha256": payload_sha256,
                "reason_sha256": reason_sha256,
                "age_seconds": round(max(0.0, age_seconds), 3),
                "outcome": outcome,
                "reason_code": reason_code,
                "catalog_revision_after": catalog_revision_after,
                "worker_polled": False,
            },
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _bounded_float(
    value: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _noop_audit(
    _event_type: str,
    _details: dict[str, Any],
) -> None:
    return
