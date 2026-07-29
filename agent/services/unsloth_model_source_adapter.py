"""Hub-side planning for local artifacts and pinned model downloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal

from ananta_contracts.unsloth_task import canonical_unsloth_json
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_task_port import (
    HubTaskSubmissionPort,
    UnslothAuditPort,
)


class ModelSourceValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelSourceRequest:
    tenant_id: str
    project_id: str
    source_id: str
    kind: Literal["local_artifact", "huggingface_snapshot"]
    expected_sha256: str
    artifact_id: str | None = None
    model_id: str | None = None
    revision: str | None = None
    max_bytes: int = 20 * 1024**3
    allow_patterns: tuple[str, ...] = ()
    trust_remote_code: bool = False
    network_authorized: bool = False
    license_status: str = "pending"
    model_format: str = "transformers"
    architecture: str = "unknown"
    quantization: str | None = None
    capability_facets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelImportPlan:
    task_type: str
    tenant_id: str
    payload_json: str
    confirmation_digest: str


class UnslothModelSourceAdapter:
    """Creates explicit import plans; workers perform all I/O via Hub tasks."""

    _SHA256 = re.compile(r"^[0-9a-f]{64}$")
    _MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
    _REVISION = re.compile(r"^[0-9a-f]{40,64}$")
    _SCOPE_ID = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$"
    )

    def __init__(
        self,
        *,
        tasks: HubTaskSubmissionPort,
        audit: UnslothAuditPort,
        evidence: ProvidedEvidenceRegistry,
    ) -> None:
        self._tasks = tasks
        self._audit = audit
        self._evidence = evidence

    def plan(self, request: ModelSourceRequest) -> ModelImportPlan:
        source_id = self._validate(request)
        payload = {
            "schema_version": 2,
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "source_id": source_id,
            "kind": request.kind,
            "expected_sha256": request.expected_sha256,
            "artifact_id": request.artifact_id,
            "model_id": request.model_id,
            "revision": request.revision,
            "max_bytes": request.max_bytes,
            "allow_patterns": list(request.allow_patterns),
            "trust_remote_code": False,
            "network_authorized": request.network_authorized,
            "license_status": request.license_status,
            "format": request.model_format,
            "architecture": request.architecture,
            "quantization": request.quantization,
            "capability_facets": list(request.capability_facets),
        }
        payload_json = canonical_unsloth_json(payload)
        confirmation = hashlib.sha256(
            f"unsloth-model-import:{payload_json}".encode()
        ).hexdigest()
        return ModelImportPlan(
            task_type="ml.model.import",
            tenant_id=request.tenant_id,
            payload_json=payload_json,
            confirmation_digest=confirmation,
        )

    def submit(self, plan: ModelImportPlan, *, confirmation_digest: str) -> str:
        expected = hashlib.sha256(
            f"unsloth-model-import:{plan.payload_json}".encode()
        ).hexdigest()
        if (
            confirmation_digest != plan.confirmation_digest
            or confirmation_digest != expected
        ):
            raise ModelSourceValidationError(
                "model_import_confirmation_invalid",
                "The import plan must be explicitly confirmed without changes.",
            )
        payload = json.loads(plan.payload_json)
        task_id = self._tasks.submit(
            task_type=plan.task_type,
            tenant_id=plan.tenant_id,
            payload=payload,
            idempotency_key=expected,
        )
        self._audit.record(
            event_type="unsloth.model_import_submitted",
            tenant_id=plan.tenant_id,
            subject_id=task_id,
            details={
                "source_id": payload["source_id"],
                "kind": payload["kind"],
                "expected_sha256": payload["expected_sha256"],
            },
        )
        return task_id

    def _validate(self, request: ModelSourceRequest) -> str:
        if (
            self._SCOPE_ID.fullmatch(str(request.tenant_id or "")) is None
            or self._SCOPE_ID.fullmatch(str(request.project_id or "")) is None
        ):
            raise ModelSourceValidationError(
                "model_import_scope_missing",
                "Tenant and project IDs are required.",
            )
        source_id = self._evidence.require_source(request.source_id)
        if not self._SHA256.fullmatch(request.expected_sha256):
            raise ModelSourceValidationError(
                "model_import_hash_invalid",
                "A lowercase SHA-256 digest is required.",
            )
        if not 0 < request.max_bytes <= 100 * 1024**3:
            raise ModelSourceValidationError(
                "model_import_size_invalid",
                "The model import size limit is outside the supported range.",
            )
        if request.trust_remote_code:
            raise ModelSourceValidationError(
                "model_import_remote_code_forbidden",
                "Remote model code is disabled for this integration.",
            )
        if request.license_status != "approved":
            raise ModelSourceValidationError(
                "model_import_license_not_approved",
                "Only explicitly license-approved models can be imported.",
            )
        if request.model_format not in {"transformers", "safetensors", "gguf"}:
            raise ModelSourceValidationError(
                "model_import_format_invalid",
                "The model format is not supported.",
            )
        metadata_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
        if metadata_pattern.fullmatch(request.architecture) is None or (
            request.quantization is not None
            and metadata_pattern.fullmatch(request.quantization) is None
        ):
            raise ModelSourceValidationError(
                "model_import_metadata_invalid",
                "Architecture and quantization metadata are invalid.",
            )
        if (
            len(request.capability_facets) > 64
            or len(set(request.capability_facets)) != len(request.capability_facets)
            or any(
                re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", facet) is None
                for facet in request.capability_facets
            )
        ):
            raise ModelSourceValidationError(
                "model_import_capability_facets_invalid",
                "Capability facets must be unique bounded identifiers.",
            )
        if any(
            not pattern
            or len(pattern) > 512
            or pattern.startswith("/")
            or ".." in pattern.split("/")
            for pattern in request.allow_patterns
        ) or len(request.allow_patterns) > 128:
            raise ModelSourceValidationError(
                "model_import_pattern_invalid",
                "Allow patterns must be non-empty relative paths.",
            )
        if request.kind == "local_artifact":
            if (
                not request.artifact_id
                or self._SCOPE_ID.fullmatch(request.artifact_id) is None
                or request.model_id
                or request.revision
                or request.network_authorized
            ):
                raise ModelSourceValidationError(
                    "model_import_local_descriptor_invalid",
                    "Local imports require only an artifact ID.",
                )
        elif request.kind == "huggingface_snapshot":
            if (
                request.artifact_id
                or not request.model_id
                or len(request.model_id) > 256
                or not self._MODEL_ID.fullmatch(request.model_id)
                or not request.revision
                or not self._REVISION.fullmatch(request.revision)
                or not request.network_authorized
            ):
                raise ModelSourceValidationError(
                    "model_import_snapshot_descriptor_invalid",
                    "Downloads require a model ID and immutable commit revision.",
                )
        else:
            raise ModelSourceValidationError(
                "model_import_kind_unsupported",
                "The requested model source kind is unsupported.",
            )
        return source_id
