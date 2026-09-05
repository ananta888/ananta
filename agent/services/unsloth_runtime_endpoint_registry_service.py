"""Immutable Hub registry for provider-neutral runtime endpoint revisions."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent.services.unsloth_storage_contracts import StorageReferencePort

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_API_CAPABILITIES = frozenset(
    {
        "openai_chat",
        "openai_responses",
        "anthropic_messages",
        "streaming",
        "tools",
        "structured_output",
    }
)


class RuntimeEndpointRegistryError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RuntimeEndpointRevision:
    tenant_id: str
    endpoint_id: str
    revision: int
    parent_revision: int | None
    restored_from_revision: int | None
    operation: str
    task_id: str
    request_digest: str
    manifest: Mapping[str, Any]
    created_at: float
    replayed: bool = False

    def public_summary(self) -> dict[str, Any]:
        provider = dict(self.manifest.get("provider_descriptor") or {})
        artifact = dict(self.manifest.get("artifact") or {})
        return {
            "endpoint_id": self.endpoint_id,
            "endpoint_revision": self.revision,
            "parent_revision": self.parent_revision,
            "restored_from_revision": self.restored_from_revision,
            "operation": self.operation,
            "state": "active",
            "task_id": self.task_id,
            "provider_id": provider.get("provider_id"),
            "provider_type": provider.get("provider_type"),
            "model_id": provider.get("model_id"),
            "artifact_id": artifact.get("artifact_id"),
            "artifact_sha256": artifact.get("artifact_sha256"),
            "api_capabilities": dict(
                self.manifest.get("api_capabilities") or {}
            ),
            "replayed": self.replayed,
        }


@runtime_checkable
class RuntimeEndpointRegistryPort(Protocol):
    def apply_handoff(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        expected_revision: int,
        task_id: str,
        idempotency_key: str,
        manifest: Mapping[str, Any],
    ) -> RuntimeEndpointRevision: ...

    def rollback(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        expected_revision: int,
        reason_sha256: str,
        actor_id: str,
    ) -> RuntimeEndpointRevision: ...

    def resolve_for_invocation(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        required_capability: str,
        expected_revision: int | None = None,
    ) -> Mapping[str, Any]: ...


class SqliteRuntimeEndpointRegistry(RuntimeEndpointRegistryPort):
    """Append-only endpoint revisions plus an atomic mutable head pointer."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS unsloth_runtime_endpoint_revisions (
            tenant_id TEXT NOT NULL,
            endpoint_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            parent_revision INTEGER,
            restored_from_revision INTEGER,
            operation TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE,
            idempotency_key_digest TEXT NOT NULL,
            request_digest TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, endpoint_id, revision)
        );
        CREATE TABLE IF NOT EXISTS unsloth_runtime_endpoint_heads (
            tenant_id TEXT NOT NULL,
            endpoint_id TEXT NOT NULL,
            current_revision INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, endpoint_id)
        );
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock=time.time,
        storage_references: StorageReferencePort | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._storage_references = storage_references
        self._initialization_lock = threading.Lock()
        self._initialize()

    def apply_handoff(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        expected_revision: int,
        task_id: str,
        idempotency_key: str,
        manifest: Mapping[str, Any],
    ) -> RuntimeEndpointRevision:
        tenant, endpoint = _scope(tenant_id, endpoint_id)
        if not isinstance(expected_revision, int) or isinstance(
            expected_revision, bool
        ) or expected_revision < 0:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_revision_invalid",
                "expected_endpoint_revision must be a non-negative integer.",
            )
        normalized_task_id = _opaque(task_id, "runtime_handoff_task_id_invalid")
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            raise RuntimeEndpointRegistryError(
                "runtime_handoff_idempotency_missing",
                "Runtime handoff requires an idempotency key.",
            )
        payload = _validated_manifest(
            manifest,
            tenant_id=tenant,
            endpoint_id=endpoint,
            expected_revision=expected_revision,
        )
        encoded = _canonical_json(payload)
        request_digest = _sha256(encoded)
        key_digest = _sha256(
            f"runtime-endpoint-idempotency-v1\0{tenant}\0{endpoint}\0{normalized_key}"
        )
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT tenant_id, endpoint_id, revision, parent_revision,
                       restored_from_revision, operation, task_id,
                       request_digest, manifest_json, created_at
                FROM unsloth_runtime_endpoint_revisions
                WHERE task_id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if existing is not None:
                record = _record(existing, replayed=True)
                if (
                    record.tenant_id != tenant
                    or record.endpoint_id != endpoint
                    or not hmac.compare_digest(
                        record.request_digest,
                        request_digest,
                    )
                ):
                    raise RuntimeEndpointRegistryError(
                        "runtime_handoff_idempotency_conflict",
                        "The Hub task ID is already bound to another handoff.",
                    )
                return record
            head = connection.execute(
                """
                SELECT current_revision
                FROM unsloth_runtime_endpoint_heads
                WHERE tenant_id = ? AND endpoint_id = ?
                """,
                (tenant, endpoint),
            ).fetchone()
            current_revision = int(head[0]) if head is not None else 0
            if current_revision != expected_revision:
                raise RuntimeEndpointRegistryError(
                    "runtime_endpoint_revision_conflict",
                    "The endpoint revision changed after the Dry-Run.",
                )
            revision = current_revision + 1
            self._bind_storage_reference(
                tenant_id=tenant,
                endpoint_id=endpoint,
                revision=revision,
                manifest=payload,
            )
            connection.execute(
                """
                INSERT INTO unsloth_runtime_endpoint_revisions
                    (tenant_id, endpoint_id, revision, parent_revision,
                     restored_from_revision, operation, task_id,
                     idempotency_key_digest, request_digest, manifest_json,
                     created_at)
                VALUES (?, ?, ?, ?, NULL, 'handoff', ?, ?, ?, ?, ?)
                """,
                (
                    tenant,
                    endpoint,
                    revision,
                    current_revision or None,
                    normalized_task_id,
                    key_digest,
                    request_digest,
                    encoded,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO unsloth_runtime_endpoint_heads
                    (tenant_id, endpoint_id, current_revision, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, endpoint_id) DO UPDATE SET
                    current_revision = excluded.current_revision,
                    updated_at = excluded.updated_at
                """,
                (tenant, endpoint, revision, now),
            )
        return RuntimeEndpointRevision(
            tenant_id=tenant,
            endpoint_id=endpoint,
            revision=revision,
            parent_revision=current_revision or None,
            restored_from_revision=None,
            operation="handoff",
            task_id=normalized_task_id,
            request_digest=request_digest,
            manifest=payload,
            created_at=now,
        )

    def rollback(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        expected_revision: int,
        reason_sha256: str,
        actor_id: str,
    ) -> RuntimeEndpointRevision:
        tenant, endpoint = _scope(tenant_id, endpoint_id)
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_revision_invalid",
                "Rollback requires a positive expected endpoint revision.",
            )
        if _SHA256.fullmatch(str(reason_sha256 or "")) is None:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_rollback_reason_invalid",
                "Rollback requires a SHA-256-bound reason.",
            )
        actor_digest = _sha256(str(actor_id or "").strip())
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            head = connection.execute(
                """
                SELECT current_revision
                FROM unsloth_runtime_endpoint_heads
                WHERE tenant_id = ? AND endpoint_id = ?
                """,
                (tenant, endpoint),
            ).fetchone()
            if head is None:
                raise RuntimeEndpointRegistryError(
                    "runtime_endpoint_not_found",
                    "The runtime endpoint does not exist.",
                )
            current_revision = int(head[0])
            if current_revision != expected_revision:
                raise RuntimeEndpointRegistryError(
                    "runtime_endpoint_revision_conflict",
                    "The endpoint revision changed before rollback.",
                )
            current = connection.execute(
                """
                SELECT parent_revision
                FROM unsloth_runtime_endpoint_revisions
                WHERE tenant_id = ? AND endpoint_id = ? AND revision = ?
                """,
                (tenant, endpoint, current_revision),
            ).fetchone()
            target_revision = int(current[0] or 0) if current is not None else 0
            if target_revision < 1:
                raise RuntimeEndpointRegistryError(
                    "runtime_endpoint_rollback_target_missing",
                    "No previous immutable endpoint revision exists.",
                )
            target = connection.execute(
                """
                SELECT manifest_json
                FROM unsloth_runtime_endpoint_revisions
                WHERE tenant_id = ? AND endpoint_id = ? AND revision = ?
                """,
                (tenant, endpoint, target_revision),
            ).fetchone()
            if target is None:
                raise RuntimeEndpointRegistryError(
                    "runtime_endpoint_history_corrupt",
                    "The previous immutable endpoint revision is unavailable.",
                )
            manifest = json.loads(str(target[0]))
            revision = current_revision + 1
            self._bind_storage_reference(
                tenant_id=tenant,
                endpoint_id=endpoint,
                revision=revision,
                manifest=manifest,
            )
            request_digest = _sha256(
                _canonical_json(
                    {
                        "operation": "rollback",
                        "tenant_id": tenant,
                        "endpoint_id": endpoint,
                        "from_revision": current_revision,
                        "restored_from_revision": target_revision,
                        "reason_sha256": reason_sha256,
                        "actor_digest": actor_digest,
                    }
                )
            )
            task_id = f"runtime-rollback-{request_digest[:32]}"
            connection.execute(
                """
                INSERT INTO unsloth_runtime_endpoint_revisions
                    (tenant_id, endpoint_id, revision, parent_revision,
                     restored_from_revision, operation, task_id,
                     idempotency_key_digest, request_digest, manifest_json,
                     created_at)
                VALUES (?, ?, ?, ?, ?, 'rollback', ?, ?, ?, ?, ?)
                """,
                (
                    tenant,
                    endpoint,
                    revision,
                    current_revision,
                    target_revision,
                    task_id,
                    request_digest,
                    request_digest,
                    _canonical_json(manifest),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE unsloth_runtime_endpoint_heads
                SET current_revision = ?, updated_at = ?
                WHERE tenant_id = ? AND endpoint_id = ?
                """,
                (revision, now, tenant, endpoint),
            )
        return RuntimeEndpointRevision(
            tenant_id=tenant,
            endpoint_id=endpoint,
            revision=revision,
            parent_revision=current_revision,
            restored_from_revision=target_revision,
            operation="rollback",
            task_id=task_id,
            request_digest=request_digest,
            manifest=manifest,
            created_at=now,
        )

    def get_current(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
    ) -> RuntimeEndpointRevision | None:
        tenant, endpoint = _scope(tenant_id, endpoint_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT revision.tenant_id, revision.endpoint_id,
                       revision.revision, revision.parent_revision,
                       revision.restored_from_revision, revision.operation,
                       revision.task_id, revision.request_digest,
                       revision.manifest_json, revision.created_at
                FROM unsloth_runtime_endpoint_heads AS head
                JOIN unsloth_runtime_endpoint_revisions AS revision
                  ON revision.tenant_id = head.tenant_id
                 AND revision.endpoint_id = head.endpoint_id
                 AND revision.revision = head.current_revision
                WHERE head.tenant_id = ? AND head.endpoint_id = ?
                """,
                (tenant, endpoint),
            ).fetchone()
        return _record(row) if row is not None else None

    def history(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
    ) -> tuple[RuntimeEndpointRevision, ...]:
        tenant, endpoint = _scope(tenant_id, endpoint_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tenant_id, endpoint_id, revision, parent_revision,
                       restored_from_revision, operation, task_id,
                       request_digest, manifest_json, created_at
                FROM unsloth_runtime_endpoint_revisions
                WHERE tenant_id = ? AND endpoint_id = ?
                ORDER BY revision ASC
                """,
                (tenant, endpoint),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def resolve_for_invocation(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        required_capability: str,
        expected_revision: int | None = None,
    ) -> Mapping[str, Any]:
        capability = str(required_capability or "").strip()
        if capability not in RUNTIME_API_CAPABILITIES:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_capability_invalid",
                "The requested runtime API capability is unknown.",
            )
        current = self.get_current(
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
        )
        if current is None:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_not_found",
                "The runtime endpoint does not exist.",
            )
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_revision_conflict",
                "The active endpoint revision does not match the invocation fence.",
            )
        capabilities = dict(current.manifest.get("api_capabilities") or {})
        if capabilities.get(capability) is not True:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_capability_unavailable",
                "The active endpoint revision does not advertise the required API.",
            )
        return {
            **current.public_summary(),
            "provider_descriptor": dict(
                current.manifest.get("provider_descriptor") or {}
            ),
            "endpoint_descriptor": dict(
                current.manifest.get("endpoint_descriptor") or {}
            ),
            "limits": dict(current.manifest.get("limits") or {}),
            "required_capability": capability,
            "fallback": None,
        }

    def _bind_storage_reference(
        self,
        *,
        tenant_id: str,
        endpoint_id: str,
        revision: int,
        manifest: Mapping[str, Any],
    ) -> None:
        if self._storage_references is None:
            return
        artifact = manifest.get("artifact")
        if not isinstance(artifact, Mapping):
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_storage_binding_missing",
                "Endpoint storage provenance is incomplete.",
            )
        try:
            self._storage_references.bind_reference(
                tenant_id=tenant_id,
                reference_kind="endpoint",
                reference_id=f"{endpoint_id}:{revision}",
                artifact_id=str(artifact.get("artifact_id") or ""),
                artifact_sha256=str(artifact.get("artifact_sha256") or ""),
            )
        except Exception as exc:
            raise RuntimeEndpointRegistryError(
                "runtime_endpoint_storage_binding_failed",
                "Endpoint storage reference could not be bound.",
            ) from exc

    def _initialize(self) -> None:
        with self._initialization_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._connect() as connection:
                connection.executescript(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path), timeout=10)


def _scope(tenant_id: str, endpoint_id: str) -> tuple[str, str]:
    tenant = str(tenant_id or "").strip()
    endpoint = _opaque(endpoint_id, "runtime_endpoint_id_invalid")
    if not tenant or len(tenant) > 256:
        raise RuntimeEndpointRegistryError(
            "runtime_endpoint_tenant_invalid",
            "A bounded tenant scope is required.",
        )
    return tenant, endpoint


def _opaque(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_ID.fullmatch(normalized) is None:
        raise RuntimeEndpointRegistryError(
            reason_code,
            "An opaque identifier is required.",
        )
    return normalized


def _validated_manifest(
    value: Mapping[str, Any],
    *,
    tenant_id: str,
    endpoint_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeEndpointRegistryError(
            "runtime_handoff_manifest_invalid",
            "Runtime handoff manifest must be an object.",
        )
    payload = json.loads(_canonical_json(dict(value)))
    if (
        payload.get("schema_version") != 2
        or payload.get("tenant_id") != tenant_id
        or payload.get("endpoint_id") != endpoint_id
        or payload.get("expected_endpoint_revision") != expected_revision
        or payload.get("fallback") is not None
    ):
        raise RuntimeEndpointRegistryError(
            "runtime_handoff_manifest_binding_mismatch",
            "Runtime handoff manifest is not bound to the Hub request.",
        )
    artifact = payload.get("artifact")
    if (
        not isinstance(artifact, dict)
        or _OPAQUE_ID.fullmatch(str(artifact.get("artifact_id") or "")) is None
        or _SHA256.fullmatch(str(artifact.get("artifact_sha256") or "")) is None
    ):
        raise RuntimeEndpointRegistryError(
            "runtime_handoff_artifact_invalid",
            "Runtime handoff artifact identity is invalid.",
        )
    capabilities = payload.get("api_capabilities")
    if (
        not isinstance(capabilities, dict)
        or set(capabilities) != RUNTIME_API_CAPABILITIES
        or not all(isinstance(enabled, bool) for enabled in capabilities.values())
        or not any(capabilities.values())
    ):
        raise RuntimeEndpointRegistryError(
            "runtime_endpoint_capabilities_invalid",
            "Runtime endpoint capabilities must be explicit and non-empty.",
        )
    _reject_direct_targets(payload)
    return payload


def _reject_direct_targets(value: Any, *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            normalized_key = str(raw_key).strip().casefold().replace("-", "_")
            if (
                normalized_key.endswith("_url")
                or normalized_key
                in {"url", "base_url", "host", "hostname", "filesystem_path"}
            ):
                raise RuntimeEndpointRegistryError(
                    "runtime_endpoint_direct_target_forbidden",
                    "Runtime descriptors must not contain direct targets.",
                )
            _reject_direct_targets(child, key=normalized_key)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _reject_direct_targets(child, key=key)
        return
    if isinstance(value, str) and value.strip().casefold().startswith(
        ("http://", "https://", "file://", "/")
    ):
        raise RuntimeEndpointRegistryError(
            "runtime_endpoint_direct_target_forbidden",
            "Runtime descriptors must contain only registry identities.",
        )


def _record(
    row: tuple[Any, ...],
    *,
    replayed: bool = False,
) -> RuntimeEndpointRevision:
    manifest = json.loads(str(row[8]))
    if not isinstance(manifest, dict):
        raise RuntimeEndpointRegistryError(
            "runtime_endpoint_history_corrupt",
            "Stored endpoint manifest is invalid.",
        )
    return RuntimeEndpointRevision(
        tenant_id=str(row[0]),
        endpoint_id=str(row[1]),
        revision=int(row[2]),
        parent_revision=int(row[3]) if row[3] is not None else None,
        restored_from_revision=int(row[4]) if row[4] is not None else None,
        operation=str(row[5]),
        task_id=str(row[6]),
        request_digest=str(row[7]),
        manifest=manifest,
        created_at=float(row[9]),
        replayed=replayed,
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeEndpointRegistryError(
            "runtime_handoff_manifest_invalid",
            "Runtime handoff manifest must be bounded JSON.",
        ) from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "RUNTIME_API_CAPABILITIES",
    "RuntimeEndpointRegistryError",
    "RuntimeEndpointRegistryPort",
    "RuntimeEndpointRevision",
    "SqliteRuntimeEndpointRegistry",
]
