"""Strict Hub-to-Worker contract for one SIRA index operation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA = "ananta.sira-index-operation.v1"
TASK_KIND = "codecompass_sira_index_operation"
CONTEXT_KEY = "sira_index_operation"
OPERATIONS = frozenset({"sync", "compact"})

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,190}$")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "operation",
        "tenant_id",
        "project_id",
        "repository_id",
        "snapshot_artifact_id",
        "idempotency_key",
        "request_digest",
    }
)


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_id(payload: Mapping[str, Any]) -> str:
    identity = {
        key: payload[key]
        for key in (
            "tenant_id",
            "project_id",
            "repository_id",
            "idempotency_key",
        )
    }
    return f"sira-operation-{_canonical_digest(identity)[:32]}"


@dataclass(frozen=True, slots=True)
class SiraIndexOperation:
    operation_id: str
    operation: str
    tenant_id: str
    project_id: str
    repository_id: str
    snapshot_artifact_id: str
    idempotency_key: str
    request_digest: str
    schema: str = SCHEMA

    @classmethod
    def create(
        cls,
        *,
        operation: str,
        tenant_id: str,
        project_id: str,
        repository_id: str,
        snapshot_artifact_id: str,
        idempotency_key: str,
    ) -> "SiraIndexOperation":
        unsigned = {
            "schema": SCHEMA,
            "operation": str(operation).strip().lower(),
            "tenant_id": str(tenant_id).strip(),
            "project_id": str(project_id).strip(),
            "repository_id": str(repository_id).strip(),
            "snapshot_artifact_id": str(snapshot_artifact_id).strip(),
            "idempotency_key": str(idempotency_key).strip(),
        }
        digest = _canonical_digest(unsigned)
        return cls.from_mapping(
            {
                **unsigned,
                "operation_id": _operation_id(unsigned),
                "request_digest": digest,
            }
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "SiraIndexOperation":
        if not isinstance(raw, Mapping):
            raise ValueError("sira_index_operation_object_required")
        payload = dict(raw)
        unknown = sorted(set(payload).difference(_FIELDS))
        missing = sorted(_FIELDS.difference(payload))
        if unknown:
            raise ValueError("sira_index_operation_unknown_fields:" + ",".join(unknown))
        if missing:
            raise ValueError("sira_index_operation_missing_fields:" + ",".join(missing))
        if payload.get("schema") != SCHEMA:
            raise ValueError("sira_index_operation_schema_invalid")
        operation = str(payload.get("operation") or "").strip().lower()
        if operation not in OPERATIONS:
            raise ValueError("sira_index_operation_kind_invalid")
        values = {
            key: str(payload.get(key) or "").strip()
            for key in (
                "operation_id",
                "tenant_id",
                "project_id",
                "repository_id",
                "snapshot_artifact_id",
                "idempotency_key",
                "request_digest",
            )
        }
        for key in ("operation_id", "tenant_id", "project_id", "repository_id"):
            if _IDENTIFIER.fullmatch(values[key]) is None:
                raise ValueError(f"sira_index_operation_{key}_invalid")
        if values["snapshot_artifact_id"] and _ARTIFACT_ID.fullmatch(values["snapshot_artifact_id"]) is None:
            raise ValueError("sira_index_operation_snapshot_artifact_id_invalid")
        if operation == "sync" and not values["snapshot_artifact_id"]:
            raise ValueError("sira_index_operation_snapshot_artifact_id_required")
        if operation == "compact" and values["snapshot_artifact_id"]:
            raise ValueError("sira_index_operation_snapshot_artifact_id_forbidden")
        if _IDEMPOTENCY_KEY.fullmatch(values["idempotency_key"]) is None:
            raise ValueError("sira_index_operation_idempotency_key_invalid")
        if _SHA256.fullmatch(values["request_digest"]) is None:
            raise ValueError("sira_index_operation_request_digest_invalid")
        unsigned = {
            "schema": SCHEMA,
            "operation": operation,
            "tenant_id": values["tenant_id"],
            "project_id": values["project_id"],
            "repository_id": values["repository_id"],
            "snapshot_artifact_id": values["snapshot_artifact_id"],
            "idempotency_key": values["idempotency_key"],
        }
        expected_digest = _canonical_digest(unsigned)
        if values["request_digest"] != expected_digest:
            raise ValueError("sira_index_operation_request_digest_mismatch")
        if values["operation_id"] != _operation_id(unsigned):
            raise ValueError("sira_index_operation_id_mismatch")
        return cls(operation=operation, schema=SCHEMA, **values)

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "snapshot_artifact_id": self.snapshot_artifact_id,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest,
        }


__all__ = [
    "CONTEXT_KEY",
    "OPERATIONS",
    "SCHEMA",
    "TASK_KIND",
    "SiraIndexOperation",
]
