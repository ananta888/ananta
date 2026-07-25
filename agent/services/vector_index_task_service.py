"""Hub-owned queue and lifecycle for delegated vector-index mutations."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.vector_store_rollout_service import (
    RESOLVED_CONFIG_SCHEMA,
    VectorStoreRolloutService,
    get_vector_store_rollout_service,
)

VECTOR_INDEX_TASK_SCHEMA = "ananta.vector_index_task.v1"
VECTOR_INDEX_RESULT_SCHEMA = "ananta.vector_index_task_result.v1"
VECTOR_INDEX_OPERATIONS = frozenset(
    {"index", "refresh", "rebuild", "delete", "migrate"}
)
_ACTIVE_STATUSES = frozenset(
    {"created", "todo", "blocked", "blocked_by_dependency", "assigned", "in_progress", "running"}
)
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_SCOPE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PAYLOAD_FIELDS = frozenset(
    {
        "points",
        "point_ids",
        "input_ref",
        "compatibility",
        "migration",
        "batch_size",
        "delete_all_scope",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "idempotency_key",
        "operation",
        "status",
        "reason_code",
        "diagnostics",
        "result",
        "error",
    }
)
_SECRET_MARKERS = ("api_key", "password", "secret", "token", "authorization")
_SAFE_SECRET_SUFFIXES = ("_ref", "_file", "_env")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _contains_plaintext_secret(value: Any, *, key: str = "") -> bool:
    normalized = str(key or "").strip().lower()
    if normalized and any(marker in normalized for marker in _SECRET_MARKERS):
        if not normalized.endswith(_SAFE_SECRET_SUFFIXES):
            return True
    if isinstance(value, Mapping):
        return any(
            _contains_plaintext_secret(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_plaintext_secret(item) for item in value)
    return False


def _scope_value(value: str, *, field: str) -> str:
    candidate = str(value or "").strip()
    if _SCOPE_VALUE.fullmatch(candidate) is None:
        raise ValueError(f"vector_index_{field}_invalid")
    return candidate


@dataclass(frozen=True)
class VectorIndexTrustedScope:
    workspace_id: str
    repository_id: str
    profile_name: str = "default"
    domain: str = "codecompass"

    def __post_init__(self) -> None:
        workspace = _scope_value(self.workspace_id, field="workspace_id")
        repository = _scope_value(self.repository_id, field="repository_id")
        profile = _scope_value(self.profile_name, field="profile_name")
        domain = str(self.domain or "").strip().lower()
        if domain not in {"codecompass", "wiki"}:
            raise ValueError("vector_index_domain_invalid")
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "repository_id", repository)
        object.__setattr__(self, "profile_name", profile)
        object.__setattr__(self, "domain", domain)

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "repository_id": self.repository_id,
            "profile_name": self.profile_name,
            "domain": self.domain,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()


@dataclass(frozen=True)
class VectorIndexOperationPayload:
    points: tuple[dict[str, Any], ...] = ()
    point_ids: tuple[str, ...] = ()
    input_ref: dict[str, Any] | None = None
    compatibility: dict[str, Any] | None = None
    migration: dict[str, Any] | None = None
    batch_size: int = 128
    delete_all_scope: bool = False

    @classmethod
    def from_mapping(
        cls,
        operation: str,
        raw: Mapping[str, Any] | None,
    ) -> "VectorIndexOperationPayload":
        payload = dict(raw or {})
        if set(payload) - _PAYLOAD_FIELDS:
            raise ValueError("vector_index_payload_fields_forbidden")
        if _contains_plaintext_secret(payload):
            raise ValueError("vector_index_plaintext_secret_forbidden")
        points_raw = payload.get("points") or []
        if not isinstance(points_raw, list) or any(
            not isinstance(item, Mapping) for item in points_raw
        ):
            raise ValueError("vector_index_points_invalid")
        if len(points_raw) > 1000:
            raise ValueError("vector_index_inline_points_limit_exceeded")
        point_ids_raw = payload.get("point_ids") or []
        if not isinstance(point_ids_raw, list):
            raise ValueError("vector_index_point_ids_invalid")
        point_ids = tuple(
            _scope_value(str(item), field="point_id") for item in point_ids_raw
        )
        input_ref = payload.get("input_ref")
        compatibility = payload.get("compatibility")
        migration = payload.get("migration")
        for field, value in (
            ("input_ref", input_ref),
            ("compatibility", compatibility),
            ("migration", migration),
        ):
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"vector_index_{field}_invalid")
        batch_size = int(payload.get("batch_size") or 128)
        if batch_size < 1 or batch_size > 4096:
            raise ValueError("vector_index_batch_size_invalid")
        normalized_operation = str(operation or "").strip().lower()
        has_input = bool(points_raw) or isinstance(input_ref, Mapping)
        if normalized_operation in {"index", "refresh", "rebuild"} and not has_input:
            raise ValueError("vector_index_input_required")
        if normalized_operation == "delete" and not (
            point_ids or bool(payload.get("delete_all_scope"))
        ):
            raise ValueError("vector_index_delete_selector_required")
        if normalized_operation == "migrate" and not isinstance(migration, Mapping):
            raise ValueError("vector_index_migration_contract_required")
        return cls(
            points=tuple(_clone(dict(item)) for item in points_raw),
            point_ids=point_ids,
            input_ref=_clone(dict(input_ref)) if isinstance(input_ref, Mapping) else None,
            compatibility=(
                _clone(dict(compatibility))
                if isinstance(compatibility, Mapping)
                else None
            ),
            migration=_clone(dict(migration)) if isinstance(migration, Mapping) else None,
            batch_size=batch_size,
            delete_all_scope=bool(payload.get("delete_all_scope", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"batch_size": self.batch_size}
        if self.points:
            result["points"] = [_clone(item) for item in self.points]
        if self.point_ids:
            result["point_ids"] = list(self.point_ids)
        if self.input_ref is not None:
            result["input_ref"] = _clone(self.input_ref)
        if self.compatibility is not None:
            result["compatibility"] = _clone(self.compatibility)
        if self.migration is not None:
            result["migration"] = _clone(self.migration)
        if self.delete_all_scope:
            result["delete_all_scope"] = True
        return result


class VectorIndexTaskQueuePort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class VectorIndexTaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any: ...

    def get_all(self) -> list[Any]: ...


class VectorIndexTaskService:
    """Serialize mutations per trusted scope and delegate through the Hub queue."""

    def __init__(
        self,
        *,
        task_queue: VectorIndexTaskQueuePort | None = None,
        task_repository: VectorIndexTaskRepositoryPort | None = None,
        rollout_service: VectorStoreRolloutService | None = None,
        status_updater: Callable[..., Any] | None = None,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._task_queue = task_queue
        self._task_repository = task_repository
        self._rollout = rollout_service or get_vector_store_rollout_service()
        self._status_updater = status_updater
        self._audit = audit or self._default_audit
        self._clock = clock
        self._lock = threading.RLock()

    @staticmethod
    def _default_audit(event: str, payload: dict[str, Any]) -> None:
        from agent.common.audit import log_audit

        log_audit(event, payload)

    def _queue(self) -> VectorIndexTaskQueuePort:
        if self._task_queue is not None:
            return self._task_queue
        from agent.services.task_queue_service import get_task_queue_service

        return get_task_queue_service()

    def _repository(self) -> VectorIndexTaskRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def _update_status(self, task_id: str, status: str, **kwargs: Any) -> Any:
        if self._status_updater is not None:
            return self._status_updater(task_id, status, **kwargs)
        from agent.services.task_runtime_service import update_local_task_status

        return update_local_task_status(task_id, status, **kwargs)

    @staticmethod
    def _raw(task: Any) -> dict[str, Any]:
        if task is None:
            return {}
        if hasattr(task, "model_dump"):
            return dict(task.model_dump())
        return dict(task)

    @staticmethod
    def _envelope(raw: Mapping[str, Any]) -> dict[str, Any]:
        context = raw.get("worker_execution_context")
        if not isinstance(context, Mapping):
            return {}
        envelope = context.get("vector_index_task")
        return dict(envelope) if isinstance(envelope, Mapping) else {}

    def get_task(self, job_id: str) -> dict[str, Any] | None:
        raw = self._raw(self._repository().get_by_id(str(job_id)))
        envelope = self._envelope(raw)
        if envelope.get("schema") != VECTOR_INDEX_TASK_SCHEMA:
            return None
        task_status = str(raw.get("status") or "todo").strip().lower()
        status = {
            "created": "queued",
            "todo": "queued",
            "blocked": "queued",
            "blocked_by_dependency": "queued",
            "assigned": "running",
            "in_progress": "running",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(task_status, "queued")
        payload = dict(envelope.get("payload") or {})
        verification = dict(raw.get("verification_status") or {})
        result = verification.get("vector_index_task_result")
        view = {
            "job_id": envelope.get("job_id"),
            "operation": envelope.get("operation"),
            "scope": _clone(envelope.get("scope") or {}),
            "scope_fingerprint": envelope.get("scope_fingerprint"),
            "idempotency_key": envelope.get("idempotency_key"),
            "request_fingerprint": envelope.get("request_fingerprint"),
            "resolved_config_hash": (
                dict(envelope.get("resolved_config") or {}).get("config_hash")
            ),
            "provider": dict(envelope.get("resolved_config") or {}).get("provider"),
            "status": status,
            "created_by": envelope.get("created_by"),
            "created_at": envelope.get("created_at"),
            "payload_summary": {
                "point_count": len(list(payload.get("points") or [])),
                "point_id_count": len(list(payload.get("point_ids") or [])),
                "has_input_ref": isinstance(payload.get("input_ref"), Mapping),
                "batch_size": payload.get("batch_size"),
                "dry_run": bool(
                    dict(payload.get("migration") or {}).get("dry_run", False)
                ),
            },
        }
        if isinstance(result, Mapping):
            view["result"] = _clone(dict(result))
        return {key: value for key, value in view.items() if value is not None}

    def submit(
        self,
        *,
        operation: str,
        trusted_scope: VectorIndexTrustedScope,
        idempotency_key: str,
        payload: Mapping[str, Any] | None,
        actor: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        normalized_operation = str(operation or "").strip().lower()
        if normalized_operation not in VECTOR_INDEX_OPERATIONS:
            raise ValueError("vector_index_operation_invalid")
        if normalized_operation == "search":
            raise ValueError("vector_index_search_must_not_enqueue")
        key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY_KEY.fullmatch(key) is None:
            raise ValueError("vector_index_idempotency_key_invalid")
        normalized_payload = VectorIndexOperationPayload.from_mapping(
            normalized_operation,
            payload,
        )
        normalized_priority = str(priority or "medium").strip().lower()
        if normalized_priority not in {"low", "medium", "high", "critical"}:
            raise ValueError("vector_index_priority_invalid")
        request_intent = {
            "operation": normalized_operation,
            "scope": trusted_scope.to_dict(),
            "idempotency_key": key,
            "payload": normalized_payload.to_dict(),
        }
        request_fingerprint = hashlib.sha256(
            _canonical_json(request_intent)
        ).hexdigest()
        job_seed = {
            "scope_fingerprint": trusted_scope.fingerprint(),
            "idempotency_key": key,
        }
        job_id = (
            "vector-index-"
            + hashlib.sha256(_canonical_json(job_seed)).hexdigest()[:32]
        )
        with self._lock:
            existing = self.get_task(job_id)
            if existing is not None:
                if existing.get("request_fingerprint") != request_fingerprint:
                    raise RuntimeError("vector_index_idempotency_mismatch")
                return existing
            self._assert_scope_available(trusted_scope)
            resolved = self._rollout.resolve(
                domain=trusted_scope.domain,
                workspace_id=trusted_scope.workspace_id,
                profile_name=trusted_scope.profile_name,
            )
            now = float(self._clock())
            envelope = {
                "schema": VECTOR_INDEX_TASK_SCHEMA,
                "job_id": job_id,
                "operation": normalized_operation,
                "scope": trusted_scope.to_dict(),
                "scope_fingerprint": trusted_scope.fingerprint(),
                "idempotency_key": key,
                "request_fingerprint": request_fingerprint,
                "resolved_config": resolved.to_worker_payload(),
                "payload": normalized_payload.to_dict(),
                "created_by": str(actor or "unknown"),
                "created_at": now,
            }
            self._queue().ingest_task(
                task_id=job_id,
                status="todo",
                title=(
                    f"Vector index {normalized_operation}: "
                    f"{trusted_scope.domain}/{trusted_scope.repository_id}"
                )[:200],
                description=(
                    "Hub-owned, worker-delegated vector index mutation. "
                    "Search is intentionally excluded."
                ),
                priority=normalized_priority,
                created_by=str(actor or "vector-index-api"),
                source="vector_index",
                tags=[
                    "vector_index",
                    "hub_delegated",
                    "persistent_job",
                    normalized_operation,
                ],
                event_type="task_ingested",
                event_channel="hub_task_queue",
                event_details={
                    "operation": normalized_operation,
                    "scope_fingerprint": trusted_scope.fingerprint(),
                    "request_fingerprint": request_fingerprint,
                    "domain_event_type": "vector_index_task_queued",
                },
                extra_fields={
                    "task_kind": "vector_index_operation",
                    "retrieval_intent": f"vector_{normalized_operation}",
                    "required_context_scope": trusted_scope.domain,
                    "required_capabilities": ["retrieval", "index_write"],
                    "worker_execution_context": {
                        "vector_index_task": envelope
                    },
                    "verification_spec": {
                        "schema": VECTOR_INDEX_RESULT_SCHEMA,
                        "idempotency_key": key,
                    },
                },
            )
            created = self.get_task(job_id)
            if created is None:
                raise RuntimeError("vector_index_task_persistence_failed")
            self._audit_task("queued", envelope, actor)
            return created

    def cancel(self, *, job_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            raw = self._raw(self._repository().get_by_id(str(job_id)))
            envelope = self._envelope(raw)
            if not envelope:
                raise ValueError("vector_index_task_not_found")
            status = str(raw.get("status") or "").strip().lower()
            if status in _TERMINAL_STATUSES:
                return self.get_task(job_id) or {}
            self._update_status(
                str(job_id),
                "cancelled",
                status_reason_code="vector_index_cancelled_by_hub",
                event_type="vector_index_task_cancelled",
                event_actor=str(actor or "unknown"),
                event_details={
                    "scope_fingerprint": envelope.get("scope_fingerprint"),
                    "idempotency_key_hash": hashlib.sha256(
                        str(envelope.get("idempotency_key") or "").encode("utf-8")
                    ).hexdigest(),
                },
            )
            self._audit_task("cancelled", envelope, actor)
            return self.get_task(job_id) or {}

    def retry(self, *, job_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            raw = self._raw(self._repository().get_by_id(str(job_id)))
            envelope = self._envelope(raw)
            if not envelope:
                raise ValueError("vector_index_task_not_found")
            status = str(raw.get("status") or "").strip().lower()
            if status not in {"failed", "cancelled"}:
                raise RuntimeError("vector_index_retry_state_invalid")
            scope = VectorIndexTrustedScope(**dict(envelope.get("scope") or {}))
            self._assert_scope_available(scope, exclude_job_id=str(job_id))
            self._update_status(
                str(job_id),
                "todo",
                status_reason_code=None,
                error=None,
                event_type="vector_index_task_retried",
                event_actor=str(actor or "unknown"),
                event_details={
                    "scope_fingerprint": envelope.get("scope_fingerprint"),
                    "same_idempotency_key": True,
                },
            )
            self._audit_task("retried", envelope, actor)
            return self.get_task(job_id) or {}

    def validate_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = self._raw(self._repository().get_by_id(str(job_id)))
        envelope = self._envelope(raw)
        if not envelope:
            raise ValueError("vector_index_task_not_found")
        payload = dict(result or {})
        if set(payload) != _RESULT_FIELDS:
            raise ValueError("vector_index_result_fields_invalid")
        if payload.get("schema") != VECTOR_INDEX_RESULT_SCHEMA:
            raise ValueError("vector_index_result_schema_invalid")
        if str(payload.get("job_id") or "") != str(job_id):
            raise ValueError("vector_index_result_job_mismatch")
        if payload.get("idempotency_key") != envelope.get("idempotency_key"):
            raise ValueError("vector_index_result_idempotency_mismatch")
        if payload.get("operation") != envelope.get("operation"):
            raise ValueError("vector_index_result_operation_mismatch")
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            raise ValueError("vector_index_result_status_invalid")
        for field in ("diagnostics", "result"):
            value = payload.get(field)
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"vector_index_result_{field}_invalid")
        if payload.get("reason_code") is not None and not isinstance(
            payload.get("reason_code"),
            str,
        ):
            raise ValueError("vector_index_result_reason_invalid")
        if payload.get("error") is not None and not isinstance(
            payload.get("error"),
            str,
        ):
            raise ValueError("vector_index_result_error_invalid")
        if _contains_plaintext_secret(payload):
            raise ValueError("vector_index_result_plaintext_secret_forbidden")
        return _clone({**payload, "status": status})

    def accept_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = self.validate_worker_result(job_id=job_id, result=result)
        raw = self._raw(self._repository().get_by_id(str(job_id)))
        envelope = self._envelope(raw)
        verification = dict(raw.get("verification_status") or {})
        verification["vector_index_task_result"] = payload
        self._update_status(
            str(job_id),
            str(payload["status"]),
            status_reason_code=str(payload.get("reason_code") or "") or None,
            verification_status=verification,
            event_type=f"vector_index_task_{payload['status']}",
            event_actor="vector-index-worker-gateway",
            event_details={
                "scope_fingerprint": envelope.get("scope_fingerprint"),
                "result_schema": VECTOR_INDEX_RESULT_SCHEMA,
            },
        )
        self._audit_task(str(payload["status"]), envelope, "worker-gateway")
        return self.get_task(job_id) or {}

    def _assert_scope_available(
        self,
        scope: VectorIndexTrustedScope,
        *,
        exclude_job_id: str | None = None,
    ) -> None:
        fingerprint = scope.fingerprint()
        for task in self._repository().get_all():
            raw = self._raw(task)
            if str(raw.get("id") or "") == str(exclude_job_id or ""):
                continue
            envelope = self._envelope(raw)
            if envelope.get("schema") != VECTOR_INDEX_TASK_SCHEMA:
                continue
            if envelope.get("scope_fingerprint") != fingerprint:
                continue
            status = str(raw.get("status") or "todo").strip().lower()
            if status in _ACTIVE_STATUSES:
                raise RuntimeError("vector_index_task_conflict")

    def _audit_task(
        self,
        action: str,
        envelope: Mapping[str, Any],
        actor: str,
    ) -> None:
        resolved = dict(envelope.get("resolved_config") or {})
        self._audit(
            f"vector_index_task_{action}",
            {
                "actor": str(actor or "unknown"),
                "job_id": str(envelope.get("job_id") or ""),
                "operation": str(envelope.get("operation") or ""),
                "scope_fingerprint": str(
                    envelope.get("scope_fingerprint") or ""
                ),
                "idempotency_key_hash": hashlib.sha256(
                    str(envelope.get("idempotency_key") or "").encode("utf-8")
                ).hexdigest(),
                "provider": str(resolved.get("provider") or ""),
                "resolved_config_hash": str(resolved.get("config_hash") or ""),
            },
        )


vector_index_task_service = VectorIndexTaskService()


def get_vector_index_task_service() -> VectorIndexTaskService:
    return vector_index_task_service


__all__ = [
    "VECTOR_INDEX_OPERATIONS",
    "VECTOR_INDEX_RESULT_SCHEMA",
    "VECTOR_INDEX_TASK_SCHEMA",
    "VectorIndexOperationPayload",
    "VectorIndexTaskService",
    "VectorIndexTrustedScope",
    "get_vector_index_task_service",
]
