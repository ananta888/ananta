"""Narrow Hub-owned ports used by Unsloth integration adapters."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Mapping, Protocol

from ananta_contracts.unsloth_task import canonical_unsloth_json


class HubTaskSubmissionPort(Protocol):
    """Submits execution to the Hub task queue without executing in-process."""

    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str: ...

    def reserve(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str: ...

    def get_submission(
        self,
        task_id: str,
    ) -> Mapping[str, object] | None: ...

    def list_stale_reserved_cleanup(
        self,
        *,
        before: float,
        limit: int,
    ) -> tuple[Mapping[str, object], ...]: ...

    def lease_reserved(
        self,
        task_id: str,
        *,
        lease_owner: str,
        now: float,
        lease_until: float,
    ) -> bool: ...

    def activate_reserved(
        self,
        task_id: str,
        *,
        lease_owner: str | None = None,
    ) -> bool: ...

    def reject_reserved(
        self,
        task_id: str,
        *,
        reason_code: str,
        lease_owner: str | None = None,
    ) -> bool: ...


class UnslothAuditPort(Protocol):
    """Records security- and governance-relevant Hub decisions."""

    def record(
        self,
        *,
        event_type: str,
        tenant_id: str,
        subject_id: str,
        details: Mapping[str, object],
    ) -> None: ...


def derive_unsloth_task_id(
    *,
    tenant_id: str,
    task_type: str,
    idempotency_key: str,
) -> str:
    """Derive the stable Hub queue identity used by an Unsloth submission."""
    key = str(idempotency_key or "").strip()
    return "unsloth-" + hashlib.sha256(
        f"{tenant_id}\0{task_type}\0{key}".encode("utf-8")
    ).hexdigest()[:32]


class HubUnslothTaskSubmissionAdapter:
    """Persist one opaque execution command in the Hub-owned central queue."""

    _TASK_TYPES = frozenset(
        {
            "ml.model.import",
            "ml.dataset.recipe.materialize",
            "ml.storage.cleanup",
            "unsloth.mcp.stop_training",
        }
    )
    _TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")

    def __init__(
        self,
        *,
        task_queue: Any | None = None,
        task_repository: Any | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._task_repository = task_repository

    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        _initial_status: str = "created",
    ) -> str:
        if task_type not in self._TASK_TYPES:
            raise ValueError("unsloth_task_type_invalid")
        if self._TENANT.fullmatch(str(tenant_id or "")) is None:
            raise ValueError("unsloth_task_tenant_invalid")
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 256:
            raise ValueError("unsloth_task_idempotency_key_invalid")
        if _initial_status not in {"created", "reserved"}:
            raise ValueError("unsloth_task_admission_status_invalid")
        encoded = canonical_unsloth_json(payload)
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise ValueError("unsloth_task_payload_too_large")
        payload_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        task_id = derive_unsloth_task_id(
            tenant_id=tenant_id,
            task_type=task_type,
            idempotency_key=key,
        )
        repository = self._repository()
        existing = repository.get_by_id(task_id)
        if existing is not None:
            context = dict(getattr(existing, "worker_execution_context", None) or {})
            stored = dict(context.get("unsloth_task") or {})
            if (
                stored.get("task_type") != task_type
                or stored.get("tenant_id") != tenant_id
                or stored.get("payload_sha256") != payload_sha256
            ):
                raise ValueError("unsloth_task_idempotency_conflict")
            return task_id
        required_capability = {
            "ml.model.import": "unsloth_model_import",
            "ml.dataset.recipe.materialize": "unsloth_dataset_materialization",
            "ml.storage.cleanup": "unsloth_storage_cleanup",
            "unsloth.mcp.stop_training": "unsloth_mcp_control",
        }[task_type]
        result_handler = {
            "ml.model.import": "unsloth_model_import_v1",
            "ml.dataset.recipe.materialize": "unsloth_data_recipe_v1",
            "ml.storage.cleanup": "unsloth_storage_cleanup_v1",
            "unsloth.mcp.stop_training": "unsloth_mcp_control_v1",
        }[task_type]
        self._queue().ingest_task(
            task_id=task_id,
            status=_initial_status,
            title=f"Hub-controlled {task_type}",
            description="Execute one bounded Unsloth platform task in an admitted worker.",
            priority="high",
            created_by="system:unsloth-control-plane",
            source="unsloth_platform",
            tags=["unsloth", "hub-orchestration", task_type],
            event_type=(
                "unsloth_task_reserved"
                if _initial_status == "reserved"
                else "unsloth_task_admitted"
            ),
            event_details={
                "task_type": task_type,
                "tenant_id_sha256": hashlib.sha256(tenant_id.encode("utf-8")).hexdigest(),
                "payload_sha256": payload_sha256,
                "admission_state": (
                    "reserved"
                    if _initial_status == "reserved"
                    else "activated"
                ),
            },
            extra_fields={
                "task_kind": task_type,
                "required_capabilities": [required_capability],
                "worker_execution_context": {
                    "schema": "ananta.unsloth-worker-task-context.v1",
                    "unsloth_task": {
                        "task_type": task_type,
                        "tenant_id": tenant_id,
                        "payload": dict(payload),
                        "payload_sha256": payload_sha256,
                        "result_handler": result_handler,
                        "followup_task_creation_allowed": False,
                    },
                },
            },
        )
        return task_id

    def reserve(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        return self.submit(
            task_type=task_type,
            tenant_id=tenant_id,
            payload=payload,
            idempotency_key=idempotency_key,
            _initial_status="reserved",
        )

    def get_submission(
        self,
        task_id: str,
    ) -> Mapping[str, object] | None:
        task = self._repository().get_by_id(str(task_id or ""))
        if task is None:
            return None
        context = dict(
            getattr(task, "worker_execution_context", None) or {}
        )
        envelope = context.get("unsloth_task")
        if (
            not isinstance(envelope, Mapping)
            or not isinstance(envelope.get("payload"), Mapping)
        ):
            return None
        return {
            "task_id": str(getattr(task, "id", "") or ""),
            "status": str(getattr(task, "status", "") or ""),
            "created_at": float(
                getattr(task, "created_at", 0.0) or 0.0
            ),
            "updated_at": float(
                getattr(task, "updated_at", 0.0) or 0.0
            ),
            "task_type": str(envelope.get("task_type") or ""),
            "tenant_id": str(envelope.get("tenant_id") or ""),
            "payload": dict(envelope["payload"]),
            "payload_sha256": str(
                envelope.get("payload_sha256") or ""
            ),
            "result_handler": str(
                envelope.get("result_handler") or ""
            ),
            "status_reason_details": dict(
                getattr(task, "status_reason_details", None)
                or {}
            ),
        }

    def list_stale_reserved_cleanup(
        self,
        *,
        before: float,
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        rows = self._repository().list_stale_reserved_unsloth_cleanup(
            before=float(before),
            limit=max(1, min(int(limit), 500)),
        )
        submissions = []
        for row in rows:
            submission = self.get_submission(
                str(getattr(row, "id", "") or "")
            )
            if submission is not None:
                submissions.append(submission)
        return tuple(submissions)

    def lease_reserved(
        self,
        task_id: str,
        *,
        lease_owner: str,
        now: float,
        lease_until: float,
    ) -> bool:
        return bool(
            self._queue().lease_reserved_task(
                task_id=str(task_id),
                lease_owner=lease_owner,
                now=now,
                lease_until=lease_until,
            )
        )

    def activate_reserved(
        self,
        task_id: str,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        current = self.get_submission(task_id)
        if current is None:
            return False
        if current["status"] in {
            "todo",
            "created",
            "assigned",
            "in_progress",
            "delegated",
            "completed",
        }:
            return True
        if current["status"] != "reserved":
            return False
        return bool(
            self._queue().activate_reserved_task(
                task_id=str(task_id),
                lease_owner=lease_owner,
            )
        )

    def reject_reserved(
        self,
        task_id: str,
        *,
        reason_code: str,
        lease_owner: str | None = None,
    ) -> bool:
        current = self.get_submission(task_id)
        if current is None:
            return False
        if current["status"] == "cancelled":
            return True
        if current["status"] != "reserved":
            return False
        return bool(
            self._queue().reject_reserved_task(
                task_id=str(task_id),
                reason_code=reason_code,
                lease_owner=lease_owner,
            )
        )

    def _queue(self) -> Any:
        if self._task_queue is None:
            from agent.services.task_queue_service import get_task_queue_service

            self._task_queue = get_task_queue_service()
        return self._task_queue

    def _repository(self) -> Any:
        if self._task_repository is None:
            from agent.repository import task_repo

            self._task_repository = task_repo
        return self._task_repository


class CallableUnslothAuditAdapter:
    def __init__(self, sink: Callable[[str, Mapping[str, object]], None]) -> None:
        self._sink = sink

    def record(
        self,
        *,
        event_type: str,
        tenant_id: str,
        subject_id: str,
        details: Mapping[str, object],
    ) -> None:
        self._sink(
            event_type,
            {
                "tenant_id": tenant_id,
                "subject_id": subject_id,
                **dict(details),
            },
        )
