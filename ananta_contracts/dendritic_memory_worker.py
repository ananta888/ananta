"""Dependency-free Hub↔Worker contracts for dendritic-memory execution."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ananta_contracts.dendritic_memory import (
    DENDRITIC_WORKER_CONTRACT,
    DendriticJobSpecV1,
    canonical_digest,
    require_digest,
    require_id,
)

DENDRITIC_WORKER_BASE_PATH = "/internal/v1/dendritic-memory"
DENDRITIC_CHECKPOINT_SCHEMA = "ananta.dendritic-memory-checkpoint.v1"
DENDRITIC_RESULT_SCHEMA = "ananta.dendritic-memory-worker-result.v1"
_REASON = re.compile(r"^dendritic_[a-z0-9_]{1,159}$")


@dataclass(frozen=True, slots=True)
class DendriticCheckpointV1:
    tenant_id: str
    run_id: str
    attempt_id: str
    fencing_token: int
    spec_digest: str
    base_model_snapshot_digest: str
    configuration_digest: str
    step: int
    payload_digest: str
    schema: str = DENDRITIC_CHECKPOINT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DENDRITIC_CHECKPOINT_SCHEMA:
            raise ValueError("dendritic_checkpoint_schema_invalid")
        for value, field in (
            (self.tenant_id, "checkpoint_tenant_id"),
            (self.run_id, "checkpoint_run_id"),
            (self.attempt_id, "checkpoint_attempt_id"),
        ):
            require_id(value, field)
        if isinstance(self.fencing_token, bool) or not 1 <= self.fencing_token <= 2**63 - 1:
            raise ValueError("dendritic_checkpoint_fencing_token_invalid")
        if isinstance(self.step, bool) or not 0 <= self.step <= 100_000:
            raise ValueError("dendritic_checkpoint_step_invalid")
        for value, field in (
            (self.spec_digest, "checkpoint_spec_digest"),
            (self.base_model_snapshot_digest, "checkpoint_base_model_snapshot_digest"),
            (self.configuration_digest, "checkpoint_configuration_digest"),
            (self.payload_digest, "checkpoint_payload_digest"),
        ):
            require_digest(value, field)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticCheckpointV1":
        if set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("dendritic_checkpoint_fields_invalid")
        return cls(**dict(raw))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class DendriticWorkerAssignmentV1:
    run_id: str
    attempt_id: str
    fencing_token: int
    tenant_scope_digest: str
    correlation_id: str
    deadline_epoch_ms: int
    worker_authorization: str
    spec: DendriticJobSpecV1 | Mapping[str, Any]
    checkpoint: DendriticCheckpointV1 | Mapping[str, Any] | None = None
    contract_version: str = DENDRITIC_WORKER_CONTRACT

    def __post_init__(self) -> None:
        if self.contract_version != DENDRITIC_WORKER_CONTRACT:
            raise ValueError("dendritic_worker_contract_version_invalid")
        require_id(self.run_id, "worker_run_id")
        require_id(self.attempt_id, "worker_attempt_id")
        require_id(self.correlation_id, "worker_correlation_id")
        if isinstance(self.fencing_token, bool) or not 1 <= self.fencing_token <= 2**63 - 1:
            raise ValueError("dendritic_worker_fencing_token_invalid")
        if isinstance(self.deadline_epoch_ms, bool) or not 1 <= self.deadline_epoch_ms <= 2**63 - 1:
            raise ValueError("dendritic_worker_deadline_invalid")
        require_digest(self.tenant_scope_digest, "worker_tenant_scope_digest")
        require_digest(self.worker_authorization, "worker_authorization")
        parsed_spec = (
            self.spec if isinstance(self.spec, DendriticJobSpecV1) else DendriticJobSpecV1.from_mapping(self.spec)
        )
        parsed_checkpoint = (
            None
            if self.checkpoint is None
            else self.checkpoint
            if isinstance(self.checkpoint, DendriticCheckpointV1)
            else DendriticCheckpointV1.from_mapping(self.checkpoint)
        )
        if parsed_checkpoint is not None and (
            parsed_checkpoint.tenant_id != parsed_spec.tenant_id
            or parsed_checkpoint.run_id != self.run_id
            or parsed_checkpoint.spec_digest != parsed_spec.digest
            or parsed_checkpoint.base_model_snapshot_digest != parsed_spec.base_model_snapshot_digest
            or parsed_checkpoint.configuration_digest != canonical_digest(parsed_spec.configuration.to_dict())
            or parsed_checkpoint.fencing_token >= self.fencing_token
        ):
            raise ValueError("dendritic_checkpoint_assignment_binding_invalid")
        object.__setattr__(self, "spec", parsed_spec)
        object.__setattr__(self, "checkpoint", parsed_checkpoint)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticWorkerAssignmentV1":
        if set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("dendritic_worker_assignment_fields_invalid")
        return cls(**dict(raw))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["spec"] = self.spec.to_dict()
        value["checkpoint"] = self.checkpoint.to_dict() if self.checkpoint else None
        return value


@dataclass(frozen=True, slots=True)
class DendriticWorkerResultV1:
    run_id: str
    attempt_id: str
    fencing_token: int
    state: str
    reason_code: str
    event_count: int
    artifact: Mapping[str, Any] | None = None
    manifest: Mapping[str, Any] | None = None
    output: Mapping[str, Any] | None = None
    checkpoint: DendriticCheckpointV1 | Mapping[str, Any] | None = None
    schema: str = DENDRITIC_RESULT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DENDRITIC_RESULT_SCHEMA:
            raise ValueError("dendritic_worker_result_schema_invalid")
        require_id(self.run_id, "worker_result_run_id")
        require_id(self.attempt_id, "worker_result_attempt_id")
        if isinstance(self.fencing_token, bool) or not 1 <= self.fencing_token <= 2**63 - 1:
            raise ValueError("dendritic_worker_result_fencing_invalid")
        if self.state not in {"completed", "failed", "cancelled"} or not _REASON.fullmatch(self.reason_code):
            raise ValueError("dendritic_worker_result_status_invalid")
        if isinstance(self.event_count, bool) or not 0 <= self.event_count <= 100_000:
            raise ValueError("dendritic_worker_result_event_count_invalid")
        has_artifact = isinstance(self.artifact, Mapping) and isinstance(self.manifest, Mapping)
        has_output = isinstance(self.output, Mapping)
        if self.state == "completed" and has_artifact == has_output:
            raise ValueError("dendritic_worker_result_artifact_required")
        if self.state != "completed" and (
            self.artifact is not None or self.manifest is not None or self.output is not None
        ):
            raise ValueError("dendritic_worker_result_artifact_forbidden")
        parsed_checkpoint = (
            None
            if self.checkpoint is None
            else self.checkpoint
            if isinstance(self.checkpoint, DendriticCheckpointV1)
            else DendriticCheckpointV1.from_mapping(self.checkpoint)
        )
        object.__setattr__(self, "artifact", dict(self.artifact) if self.artifact is not None else None)
        object.__setattr__(self, "manifest", dict(self.manifest) if self.manifest is not None else None)
        object.__setattr__(self, "output", dict(self.output) if self.output is not None else None)
        object.__setattr__(self, "checkpoint", parsed_checkpoint)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticWorkerResultV1":
        if set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("dendritic_worker_result_fields_invalid")
        return cls(**dict(raw))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checkpoint"] = self.checkpoint.to_dict() if self.checkpoint else None
        return value


__all__ = [
    "DENDRITIC_CHECKPOINT_SCHEMA",
    "DENDRITIC_RESULT_SCHEMA",
    "DENDRITIC_WORKER_BASE_PATH",
    "DendriticCheckpointV1",
    "DendriticWorkerAssignmentV1",
    "DendriticWorkerResultV1",
]
