"""Hub-owned command boundary for closed Unsloth mutations."""

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

from agent.services.ml_intern_adapter_export_service import (
    AdapterExportError,
    MlInternAdapterExportService,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.unsloth_capability_projection import project_unsloth_capabilities

_OPERATIONS = frozenset({"cleanup", "export", "runtime_handoff", "mcp"})
_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONFIRMATION = re.compile(r"^unsloth-confirm-v1\.([0-9]{10})\.([0-9a-f]{64})$")
_MAX_REASON_LENGTH = 512
_CONFIRMATION_TTL_SECONDS = 15 * 60
_LEDGER_RETENTION_SECONDS = 30 * 24 * 60 * 60
_PENDING_LEASE_SECONDS = 60


class UnslothMutationError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _Command:
    operation: str
    resource_id: str
    reason: str
    dry_run: bool
    confirmed: bool
    confirmation_id: str | None
    operation_payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _LedgerClaim:
    scope_digest: str
    key_digest: str
    payload_digest: str
    replayed: bool
    result: Mapping[str, Any] | None = None


@runtime_checkable
class UnslothMutationExecutor(Protocol):
    """A Hub-composed operation; implementations must not call workers directly."""

    def preview(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class UnslothOperationPayloadExecutor(Protocol):
    """Optional additive executor contract for operation-specific fields."""

    def preview_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        operation_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute_operation(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
        operation_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...


class AdapterExportMutationExecutor:
    """Adapts the existing tenant-scoped Hub export service to the command port."""

    def __init__(self, service: MlInternAdapterExportService) -> None:
        self._service = service

    def preview(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
    ) -> Mapping[str, Any]:
        del reason
        try:
            return self._service.preview_export(
                resource_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
            )
        except AdapterExportError as exc:
            raise _export_error(exc) from exc

    def execute(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del reason, idempotency_key
        try:
            return self._service.export(
                resource_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
            )
        except AdapterExportError as exc:
            raise _export_error(exc) from exc


class SqliteUnslothMutationLedger:
    """Persistent scoped idempotency ledger for Hub mutation commands."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS unsloth_mutation_idempotency (
            scope_digest TEXT NOT NULL,
            key_digest TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            state TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL,
            lease_expires_at REAL NOT NULL,
            PRIMARY KEY (scope_digest, key_digest)
        )
    """

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._initialization_lock = threading.Lock()
        self._initialize()

    def begin(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        operation: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> _LedgerClaim:
        scope_digest = _sha256(
            f"unsloth-mutation-scope-v1\0{principal.tenant_id}\0"
            f"{principal.subject}\0{operation}"
        )
        key_digest = _sha256(
            f"unsloth-mutation-key-v1\0{scope_digest}\0{idempotency_key}"
        )
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM unsloth_mutation_idempotency WHERE created_at < ?",
                (now - _LEDGER_RETENTION_SECONDS,),
            )
            row = connection.execute(
                """
                SELECT payload_digest, state, result_json, lease_expires_at
                FROM unsloth_mutation_idempotency
                WHERE scope_digest = ? AND key_digest = ?
                """,
                (scope_digest, key_digest),
            ).fetchone()
            if row is not None:
                stored_payload, state, result_json, lease_expires_at = row
                if not hmac.compare_digest(str(stored_payload), payload_digest):
                    raise UnslothMutationError(
                        "unsloth_idempotency_payload_conflict",
                        "Idempotency-Key was already used with different normalized inputs.",
                        status_code=409,
                    )
                if state == "completed":
                    result = json.loads(str(result_json or "{}"))
                    if not isinstance(result, Mapping):
                        raise UnslothMutationError(
                            "unsloth_idempotency_record_invalid",
                            "Stored idempotency result is invalid.",
                            status_code=503,
                            retryable=True,
                        )
                    return _LedgerClaim(
                        scope_digest,
                        key_digest,
                        payload_digest,
                        True,
                        dict(result),
                    )
                if float(lease_expires_at) > now:
                    raise UnslothMutationError(
                        "unsloth_mutation_in_progress",
                        "An identical Hub mutation is already in progress.",
                        status_code=409,
                        retryable=True,
                    )
                connection.execute(
                    """
                    DELETE FROM unsloth_mutation_idempotency
                    WHERE scope_digest = ? AND key_digest = ?
                    """,
                    (scope_digest, key_digest),
                )
            connection.execute(
                """
                INSERT INTO unsloth_mutation_idempotency
                    (scope_digest, key_digest, payload_digest, state, result_json,
                     created_at, lease_expires_at)
                VALUES (?, ?, ?, 'pending', NULL, ?, ?)
                """,
                (
                    scope_digest,
                    key_digest,
                    payload_digest,
                    now,
                    now + _PENDING_LEASE_SECONDS,
                ),
            )
        return _LedgerClaim(scope_digest, key_digest, payload_digest, False)

    def complete(self, claim: _LedgerClaim, result: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            dict(result),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE unsloth_mutation_idempotency
                SET state = 'completed', result_json = ?, lease_expires_at = 0
                WHERE scope_digest = ? AND key_digest = ?
                  AND payload_digest = ? AND state = 'pending'
                """,
                (
                    encoded,
                    claim.scope_digest,
                    claim.key_digest,
                    claim.payload_digest,
                ),
            )
            if cursor.rowcount != 1:
                raise UnslothMutationError(
                    "unsloth_idempotency_completion_failed",
                    "Hub could not persist the mutation result.",
                    status_code=503,
                    retryable=True,
                )

    def abandon(self, claim: _LedgerClaim) -> None:
        if claim.replayed:
            return
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM unsloth_mutation_idempotency
                WHERE scope_digest = ? AND key_digest = ?
                  AND payload_digest = ? AND state = 'pending'
                """,
                (
                    claim.scope_digest,
                    claim.key_digest,
                    claim.payload_digest,
                ),
            )

    def _initialize(self) -> None:
        with self._initialization_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._connect() as connection:
                connection.execute(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path), timeout=10)


class UnslothMutationCommandService:
    """Normalizes, confirms, deduplicates and dispatches Hub-owned mutations."""

    def __init__(
        self,
        *,
        executors: Mapping[str, UnslothMutationExecutor],
        ledger: SqliteUnslothMutationLedger,
        confirmation_secret: bytes,
        clock=time.time,
    ) -> None:
        normalized = {
            str(operation): executor
            for operation, executor in executors.items()
            if operation in _OPERATIONS
            and (
                isinstance(executor, UnslothMutationExecutor)
                or isinstance(executor, UnslothOperationPayloadExecutor)
            )
        }
        if len(confirmation_secret) < 32:
            raise UnslothMutationError(
                "unsloth_confirmation_secret_unavailable",
                "A strong Hub confirmation secret is required.",
                status_code=503,
            )
        self._executors = normalized
        self._ledger = ledger
        self._secret = bytes(confirmation_secret)
        self._clock = clock

    @property
    def executable_operations(self) -> frozenset[str]:
        return frozenset(self._executors)

    def execute(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        route_operation: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command = _normalize_command(route_operation, payload)
        executor = self._executors.get(command.operation)
        if executor is None:
            raise UnslothMutationError(
                f"unsloth_{command.operation}_composition_unavailable",
                "The requested operation has no safe Hub execution composition.",
                status_code=409,
            )
        fingerprint = _command_fingerprint(principal, command)
        if not command.dry_run:
            self._verify_confirmation(command.confirmation_id, fingerprint)
        payload_digest = _sha256(
            json.dumps(
                {
                    "operation": command.operation,
                    "resource_id": command.resource_id,
                    "reason": command.reason,
                    "dry_run": command.dry_run,
                    "confirmed": command.confirmed,
                    "confirmation_id": command.confirmation_id,
                    "operation_payload": command.operation_payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        claim = self._ledger.begin(
            principal,
            operation=command.operation,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
        )
        if claim.replayed:
            replay = dict(claim.result or {})
            replay["replayed"] = True
            return replay

        try:
            if command.dry_run:
                if command.operation_payload:
                    if not isinstance(executor, UnslothOperationPayloadExecutor):
                        raise UnslothMutationError(
                            f"unsloth_{command.operation}_contract_unavailable",
                            "The operation-specific Hub contract is unavailable.",
                            status_code=409,
                        )
                    preview = executor.preview_operation(
                        principal=principal,
                        resource_id=command.resource_id,
                        reason=command.reason,
                        operation_payload=command.operation_payload,
                    )
                else:
                    preview = executor.preview(
                        principal=principal,
                        resource_id=command.resource_id,
                        reason=command.reason,
                    )
                summary = _safe_public_summary(preview)
                result = {
                    "accepted": True,
                    "operation": command.operation,
                    "resource_id": command.resource_id,
                    "dry_run": True,
                    "confirmed": False,
                    "reason_code": "unsloth_mutation_dry_run_ready",
                    "confirmation_id": self._issue_confirmation(fingerprint),
                    "summary": summary,
                    "replayed": False,
                }
            else:
                if command.operation_payload:
                    if not isinstance(executor, UnslothOperationPayloadExecutor):
                        raise UnslothMutationError(
                            f"unsloth_{command.operation}_contract_unavailable",
                            "The operation-specific Hub contract is unavailable.",
                            status_code=409,
                        )
                    executed = executor.execute_operation(
                        principal=principal,
                        resource_id=command.resource_id,
                        reason=command.reason,
                        idempotency_key=idempotency_key,
                        operation_payload=command.operation_payload,
                    )
                else:
                    executed = executor.execute(
                        principal=principal,
                        resource_id=command.resource_id,
                        reason=command.reason,
                        idempotency_key=idempotency_key,
                    )
                summary = _safe_public_summary(executed)
                result = {
                    "accepted": True,
                    "operation": command.operation,
                    "resource_id": command.resource_id,
                    "dry_run": False,
                    "confirmed": True,
                    "reason_code": "unsloth_mutation_completed",
                    "summary": summary,
                    "replayed": False,
                }
            self._ledger.complete(claim, result)
            return result
        except UnslothMutationError:
            self._ledger.abandon(claim)
            raise
        except Exception as exc:
            self._ledger.abandon(claim)
            raise UnslothMutationError(
                "unsloth_mutation_execution_failed",
                "The Hub mutation executor failed closed.",
                status_code=503,
                retryable=True,
            ) from exc

    def _issue_confirmation(self, fingerprint: str) -> str:
        issued_at = int(self._clock())
        signature = hmac.new(
            self._secret,
            f"unsloth-confirm-v1\0{issued_at}\0{fingerprint}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"unsloth-confirm-v1.{issued_at}.{signature}"

    def _verify_confirmation(
        self,
        confirmation_id: str | None,
        fingerprint: str,
    ) -> None:
        match = _CONFIRMATION.fullmatch(str(confirmation_id or ""))
        if match is None:
            raise UnslothMutationError(
                "unsloth_confirmation_invalid",
                "A valid Dry-Run confirmation is required.",
                status_code=409,
            )
        issued_at = int(match.group(1))
        now = int(self._clock())
        if issued_at > now + 30 or issued_at < now - _CONFIRMATION_TTL_SECONDS:
            raise UnslothMutationError(
                "unsloth_confirmation_expired",
                "The Dry-Run confirmation has expired.",
                status_code=409,
            )
        expected = hmac.new(
            self._secret,
            f"unsloth-confirm-v1\0{issued_at}\0{fingerprint}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(match.group(2), expected):
            raise UnslothMutationError(
                "unsloth_confirmation_invalid",
                "Dry-Run confirmation does not match the normalized inputs.",
                status_code=409,
            )


def _normalize_command(
    route_operation: str,
    payload: Mapping[str, Any],
) -> _Command:
    operation = str(route_operation or "").strip()
    if operation not in _OPERATIONS:
        raise UnslothMutationError(
            "unsloth_operation_invalid",
            "Operation must be cleanup, export, runtime_handoff, or mcp.",
            status_code=404,
        )
    allowed = {
        "operation",
        "resource_id",
        "reason",
        "dry_run",
        "confirmed",
        "confirmation_id",
    }
    if operation == "runtime_handoff":
        allowed.update(
            {
                "promoted_artifact_id",
                "promoted_artifact_sha256",
                "provider_descriptor",
                "endpoint_descriptor",
                "expected_endpoint_revision",
                "source_ids",
                "run_ids",
            }
        )
    if operation == "cleanup":
        allowed.update(
            {
                "artifact_ids",
                "expected_catalog_revision",
                "retention_before",
            }
        )
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise UnslothMutationError(
            "unsloth_mutation_unknown_fields",
            f"Unknown mutation fields: {', '.join(unknown[:10])}.",
            status_code=400,
        )
    body_operation = payload.get("operation")
    if not isinstance(body_operation, str) or body_operation.strip() != operation:
        raise UnslothMutationError(
            "unsloth_operation_mismatch",
            "Body operation must match the route operation.",
            status_code=409,
        )
    resource_id = str(payload.get("resource_id") or "").strip()
    if _RESOURCE_ID.fullmatch(resource_id) is None:
        raise UnslothMutationError(
            "unsloth_resource_id_invalid",
            "resource_id must be an opaque identifier.",
        )
    raw_reason = payload.get("reason")
    if not isinstance(raw_reason, str):
        raise UnslothMutationError(
            "unsloth_reason_invalid",
            "A meaningful reason is required.",
        )
    reason = " ".join(raw_reason.split())
    meaningful_tokens = re.findall(r"[A-Za-z0-9ÄÖÜäöüß]{3,}", reason)
    if (
        len(reason) < 10
        or len(reason) > _MAX_REASON_LENGTH
        or len(meaningful_tokens) < 2
    ):
        raise UnslothMutationError(
            "unsloth_reason_invalid",
            "reason must contain 10..512 characters and at least two meaningful words.",
        )
    dry_run = payload.get("dry_run")
    confirmed = payload.get("confirmed")
    if not isinstance(dry_run, bool) or not isinstance(confirmed, bool):
        raise UnslothMutationError(
            "unsloth_confirmation_flags_invalid",
            "dry_run and confirmed must be JSON booleans.",
            status_code=400,
        )
    if (dry_run, confirmed) not in {(True, False), (False, True)}:
        raise UnslothMutationError(
            "unsloth_confirmation_flags_invalid",
            "Use dry_run=true/confirmed=false or dry_run=false/confirmed=true.",
            status_code=409,
        )
    raw_confirmation = payload.get("confirmation_id")
    confirmation_id = (
        str(raw_confirmation).strip() if raw_confirmation is not None else None
    )
    if dry_run and confirmation_id:
        raise UnslothMutationError(
            "unsloth_confirmation_unexpected",
            "Dry-Run requests must not provide confirmation_id.",
            status_code=400,
        )
    if not dry_run and not confirmation_id:
        raise UnslothMutationError(
            "unsloth_confirmation_required",
            "Confirmed mutations require the Dry-Run confirmation_id.",
            status_code=409,
        )
    operation_payload: Mapping[str, Any] = {}
    if operation == "runtime_handoff":
        operation_payload = _normalize_runtime_handoff_payload(payload)
    elif operation == "cleanup":
        operation_payload = _normalize_cleanup_payload(payload)
    return _Command(
        operation,
        resource_id,
        reason,
        dry_run,
        confirmed,
        confirmation_id,
        operation_payload,
    )


def _command_fingerprint(
    principal: MlInternTrainingPrincipal,
    command: _Command,
) -> str:
    return _sha256(
        json.dumps(
            {
                "tenant_id": principal.tenant_id,
                "subject": principal.subject,
                "operation": command.operation,
                "resource_id": command.resource_id,
                "reason": command.reason,
                "operation_payload": command.operation_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _normalize_runtime_handoff_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    artifact_id = str(payload.get("promoted_artifact_id") or "").strip()
    if _RESOURCE_ID.fullmatch(artifact_id) is None:
        raise UnslothMutationError(
            "runtime_handoff_artifact_id_invalid",
            "promoted_artifact_id must be an opaque identifier.",
        )
    artifact_sha256 = str(
        payload.get("promoted_artifact_sha256") or ""
    ).strip()
    if re.fullmatch(r"[0-9a-f]{64}", artifact_sha256) is None:
        raise UnslothMutationError(
            "runtime_handoff_artifact_hash_invalid",
            "promoted_artifact_sha256 must be a lowercase SHA-256 digest.",
        )
    revision = payload.get("expected_endpoint_revision")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or not 0 <= revision <= 2_147_483_647
    ):
        raise UnslothMutationError(
            "runtime_handoff_revision_invalid",
            "expected_endpoint_revision must be a non-negative integer.",
        )
    provider = _bounded_json_descriptor(
        payload.get("provider_descriptor"),
        reason_code="runtime_handoff_provider_descriptor_invalid",
    )
    endpoint = _bounded_json_descriptor(
        payload.get("endpoint_descriptor"),
        reason_code="runtime_handoff_endpoint_descriptor_invalid",
    )
    return {
        "promoted_artifact_id": artifact_id,
        "promoted_artifact_sha256": artifact_sha256,
        "provider_descriptor": provider,
        "endpoint_descriptor": endpoint,
        "expected_endpoint_revision": revision,
        "source_ids": _evidence_ids(payload.get("source_ids"), prefix="SRC_"),
        "run_ids": _evidence_ids(payload.get("run_ids"), prefix="RUN_"),
    }


def _normalize_cleanup_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    artifact_ids = payload.get("artifact_ids")
    if (
        not isinstance(artifact_ids, list)
        or not 1 <= len(artifact_ids) <= 128
        or any(not isinstance(value, str) for value in artifact_ids)
    ):
        raise UnslothMutationError(
            "storage_cleanup_selection_invalid",
            "artifact_ids must contain 1..128 opaque identifiers.",
        )
    normalized = sorted({value.strip() for value in artifact_ids})
    if len(normalized) != len(artifact_ids) or any(
        _RESOURCE_ID.fullmatch(value) is None for value in normalized
    ):
        raise UnslothMutationError(
            "storage_cleanup_selection_invalid",
            "artifact_ids must be unique opaque identifiers.",
        )
    revision = payload.get("expected_catalog_revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 0 <= revision <= 2_147_483_647
    ):
        raise UnslothMutationError(
            "storage_catalog_revision_invalid",
            "expected_catalog_revision must be a non-negative integer.",
        )
    cutoff = payload.get("retention_before")
    if cutoff is not None and (
        isinstance(cutoff, bool)
        or not isinstance(cutoff, (int, float))
        or not 0 <= float(cutoff) <= 2**63
    ):
        raise UnslothMutationError(
            "storage_retention_cutoff_invalid",
            "retention_before must be a bounded epoch timestamp.",
        )
    return {
        "artifact_ids": normalized,
        "expected_catalog_revision": revision,
        "retention_before": float(cutoff) if cutoff is not None else None,
    }


def _bounded_json_descriptor(value: Any, *, reason_code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UnslothMutationError(
            reason_code,
            "Runtime descriptor must be a JSON object.",
        )
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        normalized = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise UnslothMutationError(
            reason_code,
            "Runtime descriptor must contain bounded JSON values.",
        ) from exc
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise UnslothMutationError(
            reason_code,
            "Runtime descriptor exceeds its size bound.",
        )
    _assert_no_direct_target(normalized)
    return normalized


def _evidence_ids(value: Any, *, prefix: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 64
        or any(not isinstance(item, str) for item in value)
    ):
        raise UnslothMutationError(
            "runtime_handoff_evidence_invalid",
            "Runtime handoff requires bounded trusted evidence IDs.",
        )
    normalized = sorted({item.strip() for item in value})
    if len(normalized) != len(value) or any(
        not item.startswith(prefix)
        or _RESOURCE_ID.fullmatch(item) is None
        for item in normalized
    ):
        raise UnslothMutationError(
            "runtime_handoff_evidence_invalid",
            "Runtime evidence IDs must be unique supplied SRC_/RUN_ identifiers.",
        )
    return normalized


def _safe_public_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UnslothMutationError(
            "unsloth_mutation_result_invalid",
            "Hub mutation executor returned an invalid result.",
            status_code=503,
        )
    result = dict(value)
    encoded = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_reject_non_json_result,
    )
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise UnslothMutationError(
            "unsloth_mutation_result_too_large",
            "Hub mutation result exceeds its public bound.",
            status_code=503,
        )
    _assert_no_direct_target(result)
    return result


def _assert_no_direct_target(value: Any, *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            normalized_key = str(child_key).strip().casefold().replace("-", "_")
            if normalized_key in {
                "worker_url",
                "studio_url",
                "filesystem_path",
                "host_path",
            }:
                raise UnslothMutationError(
                    "unsloth_mutation_result_unsafe",
                    "Hub mutation result exposed a direct execution target.",
                    status_code=503,
                )
            _assert_no_direct_target(child, key=normalized_key)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_direct_target(child, key=key)
        return
    if isinstance(value, str):
        lowered = value.strip().casefold()
        absolute_target = (
            lowered.startswith(("http://", "https://", "file://"))
            or (lowered.startswith("/") and key != "download_url")
        )
        if absolute_target:
            raise UnslothMutationError(
                "unsloth_mutation_result_unsafe",
                "Hub mutation result exposed a direct execution target.",
                status_code=503,
            )
        if key == "download_url" and not lowered.startswith(
            "/api/ml-intern-training/exports/"
        ):
            raise UnslothMutationError(
                "unsloth_mutation_result_unsafe",
                "Export download URL must remain Hub-relative.",
                status_code=503,
            )


def _reject_non_json_result(value: Any) -> Any:
    raise TypeError(f"unsupported mutation result type: {type(value).__name__}")


def _export_error(exc: AdapterExportError) -> UnslothMutationError:
    status = 404 if exc.reason_code in {"adapter_not_found", "export_not_found"} else 409
    return UnslothMutationError(
        exc.reason_code,
        str(exc),
        status_code=status,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "AdapterExportMutationExecutor",
    "SqliteUnslothMutationLedger",
    "UnslothMutationCommandService",
    "UnslothMutationError",
    "UnslothMutationExecutor",
    "UnslothOperationPayloadExecutor",
    "project_unsloth_capabilities",
]
