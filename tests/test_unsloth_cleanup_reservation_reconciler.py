from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ananta_contracts.unsloth_task import canonical_unsloth_json

from agent.services.unsloth_cleanup_reservation_reconciler import (
    UnslothCleanupReservationPolicy,
    UnslothCleanupReservationReconciler,
)
from agent.services.unsloth_storage_governance_service import (
    UnslothStorageError,
)


TENANT = "tenant-a"
SCOPE = "a" * 64
PLAN_SHA256 = "b" * 64
REASON_SHA256 = "c" * 64


def _payload(task_id: str) -> dict[str, Any]:
    return {
        "contract_version": "ananta.unsloth-storage-cleanup-task.v1",
        "task_id": task_id,
        "tenant_scope_digest": SCOPE,
        "catalog_revision": 3,
        "plan_sha256": PLAN_SHA256,
        "reason_sha256": REASON_SHA256,
        "artifacts": [
            {
                "artifact_id": "export-a",
                "kind": "export",
                "relative_ref": (
                    f"tenants/{SCOPE}/jobs/job-a/attempts/attempt-a/"
                    "exports/export-a.zip"
                ),
                "job_id": "job-a",
                "attempt_id": "attempt-a",
                "sha256": "d" * 64,
                "size_bytes": 17,
            }
        ],
    }


def _submission(
    task_id: str,
    *,
    created_at: float = 900.0,
) -> dict[str, object]:
    payload = _payload(task_id)
    encoded = canonical_unsloth_json(payload)
    return {
        "task_id": task_id,
        "status": "reserved",
        "created_at": created_at,
        "updated_at": created_at,
        "task_type": "ml.storage.cleanup",
        "tenant_id": TENANT,
        "payload": payload,
        "payload_sha256": hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest(),
        "result_handler": "unsloth_storage_cleanup_v1",
        "status_reason_details": {},
    }


class _Tasks:
    def __init__(
        self,
        submissions: list[dict[str, object]],
        *,
        deny_leases: bool = False,
    ) -> None:
        self.items = {
            str(item["task_id"]): item for item in submissions
        }
        self.deny_leases = deny_leases
        self.list_limits: list[int] = []

    def list_stale_reserved_cleanup(
        self,
        *,
        before: float,
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        self.list_limits.append(limit)
        return tuple(
            dict(item)
            for item in self.items.values()
            if item["status"] == "reserved"
            and float(item["created_at"]) <= before
        )[:limit]

    def lease_reserved(
        self,
        task_id: str,
        *,
        lease_owner: str,
        now: float,
        lease_until: float,
    ) -> bool:
        if self.deny_leases:
            return False
        item = self.items[task_id]
        details = dict(item.get("status_reason_details") or {})
        if float(details.get("lease_until") or 0.0) > now:
            return False
        details["lease_owner"] = lease_owner
        details["lease_until"] = lease_until
        item["status_reason_details"] = details
        return True

    def get_submission(
        self,
        task_id: str,
    ) -> Mapping[str, object] | None:
        item = self.items.get(task_id)
        return dict(item) if item is not None else None

    def activate_reserved(
        self,
        task_id: str,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        item = self.items[task_id]
        details = dict(item.get("status_reason_details") or {})
        if details.get("lease_owner") != lease_owner:
            return False
        item["status"] = "created"
        return True

    def reject_reserved(
        self,
        task_id: str,
        *,
        reason_code: str,
        lease_owner: str | None = None,
    ) -> bool:
        item = self.items[task_id]
        details = dict(item.get("status_reason_details") or {})
        if details.get("lease_owner") != lease_owner:
            return False
        item["status"] = "cancelled"
        item["reason_code"] = reason_code
        return True


class _Catalog:
    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.marked: list[str] = []
        self.released: list[str] = []

    def mark_cleanup_queued(self, *, plan, task_id: str) -> int:
        self.marked.append(task_id)
        if self.failure:
            raise UnslothStorageError(
                self.failure,
                "injected catalog conflict",
            )
        return 4

    def release_cleanup_queued(self, *, plan, task_id: str) -> int:
        self.released.append(task_id)
        return 5


def _service(tasks, catalog, audit=None, *, max_age=200.0):
    return UnslothCleanupReservationReconciler(
        tasks=tasks,
        catalog=catalog,
        policy=UnslothCleanupReservationPolicy(
            stale_after_seconds=10.0,
            max_age_seconds=max_age,
            lease_seconds=30.0,
            batch_limit=2,
        ),
        audit=audit,
        clock=lambda: 1_000.0,
    )


def test_stale_valid_reservation_is_catalog_bound_then_activated():
    tasks = _Tasks([_submission("unsloth-cleanup-a")])
    catalog = _Catalog()
    audit = []

    result = _service(
        tasks,
        catalog,
        lambda event, details: audit.append((event, details)),
    ).run_once()

    assert result["activated"] == 1
    assert catalog.marked == ["unsloth-cleanup-a"]
    assert tasks.items["unsloth-cleanup-a"]["status"] == "created"
    assert audit[0][1]["worker_polled"] is False
    assert "relative_ref" not in str(audit)


def test_expired_reservation_is_released_before_terminal_rejection():
    tasks = _Tasks(
        [_submission("unsloth-cleanup-expired", created_at=100.0)]
    )
    catalog = _Catalog()

    result = _service(
        tasks,
        catalog,
        max_age=200.0,
    ).run_once()

    assert result["rejected"] == 1
    assert catalog.released == ["unsloth-cleanup-expired"]
    assert tasks.items["unsloth-cleanup-expired"]["status"] == "cancelled"


def test_catalog_conflict_releases_and_rejects_without_dispatch():
    tasks = _Tasks([_submission("unsloth-cleanup-conflict")])
    catalog = _Catalog(failure="storage_catalog_revision_conflict")

    result = _service(tasks, catalog).run_once()

    assert result["rejected"] == 1
    assert catalog.released == ["unsloth-cleanup-conflict"]
    assert tasks.items["unsloth-cleanup-conflict"]["status"] == "cancelled"


def test_parallel_lease_loss_is_a_bounded_noop():
    tasks = _Tasks(
        [_submission("unsloth-cleanup-leased")],
        deny_leases=True,
    )
    catalog = _Catalog()

    result = _service(tasks, catalog).run_once()

    assert result["conflicts"] == 1
    assert catalog.marked == []
    assert tasks.items["unsloth-cleanup-leased"]["status"] == "reserved"


def test_tampered_payload_hash_remains_fail_closed_and_is_audited():
    submission = _submission("unsloth-cleanup-tampered")
    submission["payload_sha256"] = "0" * 64
    tasks = _Tasks([submission])
    catalog = _Catalog()
    audit = []

    result = _service(
        tasks,
        catalog,
        lambda event, details: audit.append((event, details)),
    ).run_once()

    assert result["invalid"] == 1
    assert catalog.marked == []
    assert tasks.items["unsloth-cleanup-tampered"]["status"] == "reserved"
    assert audit[0][1]["reason_code"] == (
        "unsloth_cleanup_reservation_contract_invalid"
    )


def test_run_once_never_processes_more_than_requested_limit():
    tasks = _Tasks(
        [
            _submission(f"unsloth-cleanup-{index}")
            for index in range(6)
        ]
    )
    catalog = _Catalog()

    result = _service(tasks, catalog).run_once(limit=2)

    assert result["leased"] == 2
    assert result["activated"] == 2
    assert tasks.list_limits == [8]
