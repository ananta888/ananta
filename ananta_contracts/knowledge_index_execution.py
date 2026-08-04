"""Closed v2 execution contracts for Hub-delegated knowledge indexing."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ananta_contracts.source_control import MAX_SOURCE_ADMISSION_FILES

KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA = (
    "ananta.knowledge_index_execution_job.v2"
)
KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA = (
    "ananta.knowledge_index_execution_result.v2"
)
# One Hub-owned absolute deadline covers Worker execution plus the response and
# every result-artifact transfer.  The transfer share is explicit so lease and
# grant planning cannot accidentally budget only the Worker POST.
KNOWLEDGE_INDEX_RESULT_TRANSFER_BUDGET_SECONDS = 30
KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS = (
    KNOWLEDGE_INDEX_RESULT_TRANSFER_BUDGET_SECONDS
)
KNOWLEDGE_INDEX_PRE_DISPATCH_RESERVE_SECONDS = 120
KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON = (
    "knowledge_index_execution_dispatch_window_insufficient"
)
KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON = (
    "knowledge_index_execution_dispatch_lease_expired"
)
MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES = 128 * 1024 * 1024
MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES = 1_048_576
MAX_KNOWLEDGE_INDEX_MANIFEST_PATH_WIRE_BYTES = 512

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class KnowledgeIndexExecutionContractError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _json_string_payload_size(value: str) -> int:
    """Measure one path exactly as it expands inside the UTF-8 JSON wire.

    The snapshot transport uses ``ensure_ascii=False``. JSON still escapes
    quotes and control characters, so measuring only ``value.encode()`` can
    undercount a legal Python string by up to six times. The surrounding
    quote bytes are envelope overhead; the bounded path payload is the bytes
    between them.
    """

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("knowledge_index_manifest_path_invalid") from exc
    if len(encoded) < 2 or encoded[:1] != b'"' or encoded[-1:] != b'"':
        raise ValueError("knowledge_index_manifest_path_invalid")
    return len(encoded) - 2


class _Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class KnowledgeIndexFileEntry(_Closed):
    relative_path: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=512,
        ),
    ]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0, le=128 * 1024 * 1024)]

    @field_validator("relative_path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or any(part in {"", ".", ".."} for part in path.parts)
            or _json_string_payload_size(value)
            > MAX_KNOWLEDGE_INDEX_MANIFEST_PATH_WIRE_BYTES
        ):
            raise ValueError("knowledge_index_manifest_path_invalid")
        return value


class KnowledgeIndexFileManifest(_Closed):
    manifest_id: Annotated[
        str, StringConstraints(pattern=r"^manifest_[0-9a-f]{64}$")
    ]
    manifest_digest: Sha256
    files: tuple[KnowledgeIndexFileEntry, ...] = Field(
        min_length=1,
        max_length=MAX_SOURCE_ADMISSION_FILES,
    )
    total_bytes: Annotated[int, Field(ge=0, le=512 * 1024 * 1024)]

    @model_validator(mode="after")
    def _manifest_is_canonical(self) -> "KnowledgeIndexFileManifest":
        wire_files = [item.to_wire() for item in self.files]
        if wire_files != sorted(
            wire_files,
            key=lambda item: item["relative_path"],
        ):
            raise ValueError("knowledge_index_manifest_not_sorted")
        if len({item.relative_path for item in self.files}) != len(
            self.files
        ):
            raise ValueError("knowledge_index_manifest_path_duplicate")
        if sum(item.size_bytes for item in self.files) != self.total_bytes:
            raise ValueError("knowledge_index_manifest_size_mismatch")
        expected = _digest(wire_files)
        if (
            self.manifest_digest != expected
            or self.manifest_id != f"manifest_{expected}"
        ):
            raise ValueError("knowledge_index_manifest_digest_mismatch")
        return self

    @classmethod
    def create(
        cls,
        files: list[Mapping[str, Any]],
    ) -> "KnowledgeIndexFileManifest":
        normalized = tuple(
            sorted(
                (
                    KnowledgeIndexFileEntry.model_validate(dict(item))
                    for item in files
                ),
                key=lambda item: item.relative_path,
            )
        )
        digest = _digest([item.to_wire() for item in normalized])
        return cls(
            manifest_id=f"manifest_{digest}",
            manifest_digest=digest,
            files=normalized,
            total_bytes=sum(item.size_bytes for item in normalized),
        )


class KnowledgeIndexResourceBudget(_Closed):
    max_files: Annotated[
        int,
        Field(ge=1, le=MAX_SOURCE_ADMISSION_FILES),
    ]
    max_total_bytes: Annotated[
        int, Field(ge=1, le=512 * 1024 * 1024)
    ]
    max_file_bytes: Annotated[
        int, Field(ge=1, le=128 * 1024 * 1024)
    ]
    max_runtime_seconds: Annotated[int, Field(ge=1, le=86_400)]
    max_memory_bytes: Annotated[
        int, Field(ge=64 * 1024 * 1024, le=256 * 1024 * 1024 * 1024)
    ]
    max_output_bytes: Annotated[
        int, Field(ge=1, le=512 * 1024 * 1024)
    ]


class KnowledgeIndexAuthorityBinding(_Closed):
    tenant_id: Identifier
    project_id: Identifier
    source_revision_id: Annotated[
        str, StringConstraints(pattern=r"^srev_[0-9a-f]{64}$")
    ]
    source_revision_digest: Sha256
    admission_digest: Sha256
    policy_snapshot_id: Identifier
    policy_snapshot_digest: Sha256
    destination_id: Annotated[
        str, StringConstraints(pattern=r"^dst_[0-9a-f]{64}$")
    ]
    destination_digest: Sha256
    source_access_grant_id: Annotated[
        str, StringConstraints(pattern=r"^grant_[0-9a-f]{64}$")
    ]
    source_access_grant_digest: Sha256
    binding_digest: Sha256

    @model_validator(mode="after")
    def _binding_digest_matches(self) -> "KnowledgeIndexAuthorityBinding":
        coordinates = self.to_wire()
        coordinates.pop("binding_digest")
        if self.binding_digest != _digest(coordinates):
            raise ValueError("knowledge_index_authority_binding_mismatch")
        return self

    @classmethod
    def create(cls, **values: Any) -> "KnowledgeIndexAuthorityBinding":
        normalized = dict(values)
        normalized["binding_digest"] = _digest(normalized)
        return cls.model_validate(normalized)


class KnowledgeIndexExecutionAssignment(_Closed):
    assignment_id: Identifier
    worker_id: Identifier
    lease_id: Identifier
    lease_generation: Annotated[int, Field(ge=1)]
    lease_issued_epoch_ms: Annotated[int, Field(ge=0)]
    lease_expires_epoch_ms: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _lease_window_is_positive(
        self,
    ) -> "KnowledgeIndexExecutionAssignment":
        if self.lease_expires_epoch_ms <= self.lease_issued_epoch_ms:
            raise ValueError("knowledge_index_lease_window_invalid")
        return self


class KnowledgeIndexPayloadArtifactRef(_Closed):
    artifact_id: Identifier
    sha256: Sha256
    size_bytes: Annotated[
        int,
        Field(ge=1, le=MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES),
    ]
    media_type: Literal[
        "application/vnd.ananta.knowledge-index-job+json"
    ]
    encoding: Literal["json"] = "json"


class KnowledgeIndexExecutionPayload(_Closed):
    payload_artifact_ref: KnowledgeIndexPayloadArtifactRef


def derive_execution_fingerprint(
    *,
    hub_task_id: str,
    idempotency_key_digest: str,
    authority_binding: KnowledgeIndexAuthorityBinding,
    file_manifest: KnowledgeIndexFileManifest,
    resources: KnowledgeIndexResourceBudget,
    payload: KnowledgeIndexExecutionPayload,
    profile_name: str,
) -> str:
    return _digest(
        {
            "authority_binding": authority_binding.to_wire(),
            "file_manifest": file_manifest.to_wire(),
            "hub_task_id": hub_task_id,
            "idempotency_key_digest": idempotency_key_digest,
            "payload": payload.to_wire(),
            "profile_name": profile_name,
            "resources": resources.to_wire(),
        }
    )


class KnowledgeIndexExecutionJob(_Closed):
    schema_version: Literal[
        "ananta.knowledge_index_execution_job.v2"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    authority: Literal["hub"]
    job_id: Annotated[
        str,
        StringConstraints(pattern=r"^knowledge-index-[0-9a-f]{32}$"),
    ]
    hub_task_id: Identifier
    job_type: Literal["source_records"]
    scope_id: Identifier
    source_scope: Identifier
    profile_name: Identifier
    created_by: Identifier
    created_at_epoch_ms: Annotated[int, Field(ge=0)]
    attempt: Annotated[int, Field(ge=1, le=100)]
    idempotency_key_digest: Sha256
    idempotency_fingerprint: Sha256
    authority_binding: KnowledgeIndexAuthorityBinding
    file_manifest: KnowledgeIndexFileManifest
    resources: KnowledgeIndexResourceBudget
    assignment: KnowledgeIndexExecutionAssignment
    payload: KnowledgeIndexExecutionPayload

    @model_validator(mode="after")
    def _job_is_bound_and_bounded(self) -> "KnowledgeIndexExecutionJob":
        expected = derive_execution_fingerprint(
            hub_task_id=self.hub_task_id,
            idempotency_key_digest=self.idempotency_key_digest,
            authority_binding=self.authority_binding,
            file_manifest=self.file_manifest,
            resources=self.resources,
            payload=self.payload,
            profile_name=self.profile_name,
        )
        if (
            self.idempotency_fingerprint != expected
            or self.job_id != f"knowledge-index-{expected[:32]}"
        ):
            raise ValueError("knowledge_index_job_identity_mismatch")
        if (
            len(self.file_manifest.files) > self.resources.max_files
            or self.file_manifest.total_bytes
            > self.resources.max_total_bytes
            or any(
                item.size_bytes > self.resources.max_file_bytes
                for item in self.file_manifest.files
            )
        ):
            raise ValueError("knowledge_index_job_resource_budget_exceeded")
        return self

    @classmethod
    def create(cls, **values: Any) -> "KnowledgeIndexExecutionJob":
        normalized = dict(values)
        fingerprint = derive_execution_fingerprint(
            hub_task_id=normalized["hub_task_id"],
            idempotency_key_digest=normalized[
                "idempotency_key_digest"
            ],
            authority_binding=normalized["authority_binding"],
            file_manifest=normalized["file_manifest"],
            resources=normalized["resources"],
            payload=normalized["payload"],
            profile_name=normalized["profile_name"],
        )
        normalized.update(
            {
                "schema": KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
                "authority": "hub",
                "idempotency_fingerprint": fingerprint,
                "job_id": f"knowledge-index-{fingerprint[:32]}",
            }
        )
        return cls.model_validate(normalized)


class KnowledgeIndexExecutionResult(_Closed):
    schema_version: Literal[
        "ananta.knowledge_index_execution_result.v2"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    job_id: Annotated[
        str,
        StringConstraints(pattern=r"^knowledge-index-[0-9a-f]{32}$"),
    ]
    idempotency_fingerprint: Sha256
    assignment_id: Identifier
    worker_id: Identifier
    lease_id: Identifier
    lease_generation: Annotated[int, Field(ge=1)]
    source_revision_id: Annotated[
        str, StringConstraints(pattern=r"^srev_[0-9a-f]{64}$")
    ]
    source_revision_digest: Sha256
    admission_digest: Sha256
    policy_snapshot_id: Identifier
    policy_snapshot_digest: Sha256
    destination_id: Annotated[
        str, StringConstraints(pattern=r"^dst_[0-9a-f]{64}$")
    ]
    destination_digest: Sha256
    source_access_grant_id: Annotated[
        str, StringConstraints(pattern=r"^grant_[0-9a-f]{64}$")
    ]
    source_access_grant_digest: Sha256
    authority_binding_digest: Sha256
    file_manifest_digest: Sha256
    status: Literal["completed", "failed"]
    reason_code: str | None
    knowledge_index: dict[str, Any] | None
    run: dict[str, Any] | None
    results: list[dict[str, Any]] | None
    artifact_refs: list[dict[str, Any]]
    error: str | None

    @classmethod
    def create(
        cls,
        job: KnowledgeIndexExecutionJob,
        *,
        status: str,
        reason_code: str | None,
        knowledge_index: Mapping[str, Any] | None = None,
        run: Mapping[str, Any] | None = None,
        results: list[Mapping[str, Any]] | None = None,
        artifact_refs: list[Mapping[str, Any]] | None = None,
        error: str | None = None,
    ) -> "KnowledgeIndexExecutionResult":
        binding = job.authority_binding
        assignment = job.assignment
        return cls(
            schema=KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA,
            job_id=job.job_id,
            idempotency_fingerprint=job.idempotency_fingerprint,
            assignment_id=assignment.assignment_id,
            worker_id=assignment.worker_id,
            lease_id=assignment.lease_id,
            lease_generation=assignment.lease_generation,
            source_revision_id=binding.source_revision_id,
            source_revision_digest=binding.source_revision_digest,
            admission_digest=binding.admission_digest,
            policy_snapshot_id=binding.policy_snapshot_id,
            policy_snapshot_digest=binding.policy_snapshot_digest,
            destination_id=binding.destination_id,
            destination_digest=binding.destination_digest,
            source_access_grant_id=binding.source_access_grant_id,
            source_access_grant_digest=(
                binding.source_access_grant_digest
            ),
            authority_binding_digest=binding.binding_digest,
            file_manifest_digest=job.file_manifest.manifest_digest,
            status=status,
            reason_code=reason_code,
            knowledge_index=(
                dict(knowledge_index) if knowledge_index is not None else None
            ),
            run=dict(run) if run is not None else None,
            results=(
                [dict(item) for item in results]
                if results is not None
                else None
            ),
            artifact_refs=[
                dict(item) for item in (artifact_refs or [])
            ],
            error=error,
        )


def parse_execution_job(
    payload: Mapping[str, Any],
) -> KnowledgeIndexExecutionJob:
    try:
        return KnowledgeIndexExecutionJob.model_validate(dict(payload))
    except (TypeError, ValueError, ValidationError):
        raise KnowledgeIndexExecutionContractError(
            "knowledge_index_execution_job_invalid"
        ) from None


def parse_execution_result(
    payload: Mapping[str, Any],
) -> KnowledgeIndexExecutionResult:
    try:
        return KnowledgeIndexExecutionResult.model_validate(dict(payload))
    except (TypeError, ValueError, ValidationError):
        raise KnowledgeIndexExecutionContractError(
            "knowledge_index_execution_result_invalid"
        ) from None
