"""Small versioned wire contracts for model-intelligence work.

The contracts deliberately contain no routing, queue, persistence, Flask, or
worker imports.  The Hub owns ``AnalysisJob`` creation and scheduling; workers
may only consume the immutable job description and emit artifact references or
error envelopes.

Version 1 is closed by default.  Additive vendor/domain metadata must use
bounded ``x-*`` extensions.  A change to required fields or field semantics
requires a new schema version instead of silently changing v1.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

MODEL_IDENTITY_SCHEMA = "ananta.model-intelligence.model-identity.v1"
CAPABILITY_DESCRIPTOR_SCHEMA = (
    "ananta.model-intelligence.capability-descriptor.v1"
)
ANALYSIS_JOB_SCHEMA = "ananta.model-intelligence.analysis-job.v1"
ARTIFACT_REF_SCHEMA = "ananta.model-intelligence.artifact-ref.v1"
ERROR_ENVELOPE_SCHEMA = "ananta.model-intelligence.error-envelope.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ID_RE = re.compile(r"^model_[0-9a-f]{64}$")
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_COORDINATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+@:/-]{0,511}$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+@:/-]{0,127}$")
_KIND_RE = re.compile(
    r"^[a-z][a-z0-9-]{0,31}(?:\.[a-z][a-z0-9-]{0,31}){1,7}$"
)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_MEDIA_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$"
)
_EXTENSION_KEY_RE = re.compile(
    r"^x-[a-z0-9]+(?:[._-][a-z0-9]+){0,7}$"
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(bearer\s+|password\s*=|api[_-]?key\s*=|token\s*=|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "private",
    "secret",
    "token",
)
_ERROR_DETAIL_KEYS = frozenset(
    {
        "adapter_id",
        "analysis_kind",
        "artifact_id",
        "capability_id",
        "field",
        "job_id",
        "limit_name",
        "model_id",
        "operation",
        "task_id",
    }
)

JsonScalar = str | int | float | bool | None
ContractKind = Literal[
    "model_identity",
    "capability_descriptor",
    "analysis_job",
    "artifact_ref",
    "error_envelope",
]


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CapabilityEvidence(str, Enum):
    DECLARED = "declared"
    PROBED = "probed"
    INFERRED = "inferred"


class CapabilityReasonCode(str, Enum):
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    CAPABILITY_NOT_DECLARED = "capability_not_declared"
    CAPABILITY_PROBE_FAILED = "capability_probe_failed"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    FORMAT_UNSUPPORTED = "format_unsupported"
    REQUIRES_COMPATIBLE_MODEL_TASK = "requires_compatible_model_task"
    REQUIRES_SENTENCE_TRANSFORMERS_MODE = "requires_sentence_transformers_mode"
    RUNTIME_UNSUPPORTED = "runtime_unsupported"


class ModelIntelligenceReasonCode(str, Enum):
    CONTRACT_INVALID = "contract_invalid"
    MODEL_IDENTITY_INVALID = "model_identity_invalid"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    ANALYSIS_REQUEST_INVALID = "analysis_request_invalid"
    ANALYSIS_CANCELLED = "analysis_cancelled"
    ANALYSIS_DEADLINE_EXCEEDED = "analysis_deadline_exceeded"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    ARTIFACT_INTEGRITY_MISMATCH = "artifact_integrity_mismatch"
    ARTIFACT_STORE_UNAVAILABLE = "artifact_store_unavailable"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    POLICY_DENIED = "policy_denied"
    INTERNAL_ERROR = "internal_error"


RETRYABLE_REASON_CODES = frozenset(
    {
        ModelIntelligenceReasonCode.ANALYSIS_DEADLINE_EXCEEDED,
        ModelIntelligenceReasonCode.ARTIFACT_STORE_UNAVAILABLE,
        ModelIntelligenceReasonCode.RUNTIME_UNAVAILABLE,
    }
)


class ModelIntelligenceContractError(ValueError):
    """Stable boundary error raised by the generic wire parser."""

    def __init__(self, reason_code: ModelIntelligenceReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


def _bounded_scalar(value: Any, *, maximum_string_length: int = 256) -> JsonScalar:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("model_intelligence_scalar_not_finite")
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > maximum_string_length
            or any(ord(character) < 32 for character in normalized)
            or _SENSITIVE_VALUE_RE.search(normalized)
        ):
            raise ValueError("model_intelligence_scalar_not_sanitized")
        return normalized
    raise ValueError("model_intelligence_scalar_invalid")


def _extensions(value: Any) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping) or len(value) > 16:
        raise ValueError("model_intelligence_extensions_invalid")
    normalized: dict[str, JsonScalar] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip().lower()
        if (
            not _EXTENSION_KEY_RE.fullmatch(key)
            or any(part in key for part in _SENSITIVE_KEY_PARTS)
        ):
            raise ValueError("model_intelligence_extension_key_invalid")
        normalized[key] = _bounded_scalar(raw_value)
    return dict(sorted(normalized.items()))


def _coordinate(value: str, *, pattern: re.Pattern[str], reason: str) -> str:
    normalized = str(value).strip()
    if (
        not pattern.fullmatch(normalized)
        or "\\" in normalized
        or "://" in normalized
        or "//" in normalized
        or any(segment in {".", ".."} for segment in normalized.split("/"))
    ):
        raise ValueError(reason)
    return normalized


def canonical_model_coordinates(
    *,
    source: str,
    locator: str,
    revision: str,
    content_sha256: str,
) -> dict[str, str]:
    """Return the one canonical coordinate representation used for IDs."""

    normalized_source = str(source).strip().lower()
    if not _SOURCE_RE.fullmatch(normalized_source):
        raise ValueError("model_identity_source_invalid")
    normalized_locator = _coordinate(
        locator,
        pattern=_COORDINATE_RE,
        reason="model_identity_locator_invalid",
    )
    normalized_revision = _coordinate(
        revision,
        pattern=_REVISION_RE,
        reason="model_identity_revision_invalid",
    )
    normalized_digest = str(content_sha256).strip().lower()
    if not _SHA256_RE.fullmatch(normalized_digest):
        raise ValueError("model_identity_content_digest_invalid")
    return {
        "content_sha256": normalized_digest,
        "locator": normalized_locator,
        "revision": normalized_revision,
        "source": normalized_source,
    }


def derive_model_id(
    *,
    source: str,
    locator: str,
    revision: str,
    content_sha256: str,
) -> str:
    """Derive a stable ID without environment paths, timestamps, or randomness."""

    coordinates = canonical_model_coordinates(
        source=source,
        locator=locator,
        revision=revision,
        content_sha256=content_sha256,
    )
    canonical = json.dumps(
        coordinates,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"model_{hashlib.sha256(canonical).hexdigest()}"


class _ClosedContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    extensions: dict[str, JsonScalar] = Field(default_factory=dict)

    @field_validator("extensions", mode="before")
    @classmethod
    def _validate_extensions(cls, value: Any) -> dict[str, JsonScalar]:
        return _extensions(value)

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class ModelIdentity(_ClosedContract):
    schema_version: Literal[
        "ananta.model-intelligence.model-identity.v1"
    ] = Field(
        default=MODEL_IDENTITY_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    model_id: str
    source: str
    locator: str
    revision: str
    content_sha256: str

    @field_validator("model_id")
    @classmethod
    def _validate_model_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _MODEL_ID_RE.fullmatch(normalized):
            raise ValueError("model_identity_id_invalid")
        return normalized

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SOURCE_RE.fullmatch(normalized):
            raise ValueError("model_identity_source_invalid")
        return normalized

    @field_validator("locator")
    @classmethod
    def _validate_locator(cls, value: str) -> str:
        return _coordinate(
            value,
            pattern=_COORDINATE_RE,
            reason="model_identity_locator_invalid",
        )

    @field_validator("revision")
    @classmethod
    def _validate_revision(cls, value: str) -> str:
        return _coordinate(
            value,
            pattern=_REVISION_RE,
            reason="model_identity_revision_invalid",
        )

    @field_validator("content_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("model_identity_content_digest_invalid")
        return normalized

    @model_validator(mode="after")
    def _validate_derived_id(self) -> "ModelIdentity":
        expected = derive_model_id(
            source=self.source,
            locator=self.locator,
            revision=self.revision,
            content_sha256=self.content_sha256,
        )
        if self.model_id != expected:
            raise ValueError("model_identity_id_mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        source: str,
        locator: str,
        revision: str,
        content_sha256: str,
        extensions: Mapping[str, JsonScalar] | None = None,
    ) -> "ModelIdentity":
        coordinates = canonical_model_coordinates(
            source=source,
            locator=locator,
            revision=revision,
            content_sha256=content_sha256,
        )
        return cls(
            model_id=derive_model_id(**coordinates),
            extensions=dict(extensions or {}),
            **coordinates,
        )


class CapabilityDescriptor(_ClosedContract):
    schema_version: Literal[
        "ananta.model-intelligence.capability-descriptor.v1"
    ] = Field(
        default=CAPABILITY_DESCRIPTOR_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    model_id: str
    capability_id: str
    state: CapabilityState
    evidence: CapabilityEvidence
    adapter_id: str
    adapter_version: str
    reason_code: CapabilityReasonCode | None = None

    @field_validator("model_id")
    @classmethod
    def _validate_model_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _MODEL_ID_RE.fullmatch(normalized):
            raise ValueError("capability_model_id_invalid")
        return normalized

    @field_validator("capability_id")
    @classmethod
    def _validate_capability_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KIND_RE.fullmatch(normalized):
            raise ValueError("capability_id_invalid")
        return normalized

    @field_validator("adapter_id")
    @classmethod
    def _validate_adapter_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("capability_adapter_id_invalid")
        return normalized

    @field_validator("adapter_version")
    @classmethod
    def _validate_adapter_version(cls, value: str) -> str:
        normalized = value.strip()
        if not _VERSION_RE.fullmatch(normalized):
            raise ValueError("capability_adapter_version_invalid")
        return normalized

    @model_validator(mode="after")
    def _validate_truthful_state(self) -> "CapabilityDescriptor":
        if self.state is CapabilityState.SUPPORTED and self.reason_code is not None:
            raise ValueError("capability_supported_reason_forbidden")
        if self.state is not CapabilityState.SUPPORTED and self.reason_code is None:
            raise ValueError("capability_unavailable_reason_required")
        return self


class AnalysisJob(_ClosedContract):
    """Immutable work description created and queued by the Hub."""

    schema_version: Literal[
        "ananta.model-intelligence.analysis-job.v1"
    ] = Field(
        default=ANALYSIS_JOB_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    job_id: str
    hub_task_id: str
    tenant_id: str
    model_id: str
    analysis_kind: str
    profile_id: str
    request_sha256: str
    requested_artifact_kinds: tuple[str, ...]
    max_runtime_seconds: int = Field(ge=1, le=86_400)
    max_output_bytes: int = Field(ge=1, le=1_073_741_824)

    @field_validator("job_id", "hub_task_id", "tenant_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("analysis_job_identifier_invalid")
        return normalized

    @field_validator("model_id")
    @classmethod
    def _validate_model_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _MODEL_ID_RE.fullmatch(normalized):
            raise ValueError("analysis_job_model_id_invalid")
        return normalized

    @field_validator("analysis_kind")
    @classmethod
    def _validate_analysis_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KIND_RE.fullmatch(normalized):
            raise ValueError("analysis_job_kind_invalid")
        return normalized

    @field_validator("profile_id")
    @classmethod
    def _validate_profile_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KIND_RE.fullmatch(normalized):
            raise ValueError("analysis_job_profile_invalid")
        return normalized

    @field_validator("request_sha256")
    @classmethod
    def _validate_request_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("analysis_job_request_digest_invalid")
        return normalized

    @field_validator("requested_artifact_kinds")
    @classmethod
    def _validate_artifact_kinds(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(sorted({str(value).strip().lower() for value in values}))
        if (
            not normalized
            or len(normalized) > 16
            or any(not _KIND_RE.fullmatch(value) for value in normalized)
        ):
            raise ValueError("analysis_job_artifact_kinds_invalid")
        return normalized


class ArtifactRef(_ClosedContract):
    """Content-addressed artifact identity without a shared-filesystem path."""

    schema_version: Literal[
        "ananta.model-intelligence.artifact-ref.v1"
    ] = Field(
        default=ARTIFACT_REF_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    artifact_id: str
    job_id: str
    kind: str
    sha256: str
    size_bytes: int = Field(ge=0, le=107_374_182_400)
    media_type: str

    @field_validator("artifact_id", "job_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("artifact_ref_identifier_invalid")
        return normalized

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KIND_RE.fullmatch(normalized):
            raise ValueError("artifact_ref_kind_invalid")
        return normalized

    @field_validator("sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("artifact_ref_digest_invalid")
        return normalized

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _MEDIA_TYPE_RE.fullmatch(normalized):
            raise ValueError("artifact_ref_media_type_invalid")
        return normalized


def sanitize_error_details(raw: Mapping[str, Any] | None) -> dict[str, JsonScalar]:
    """Drop unknown or sensitive diagnostic values before wire serialization."""

    if not isinstance(raw, Mapping):
        return {}
    sanitized: dict[str, JsonScalar] = {}
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip().lower()
        if key not in _ERROR_DETAIL_KEYS:
            continue
        try:
            sanitized[key] = _bounded_scalar(raw_value)
        except ValueError:
            continue
        if len(sanitized) == 16:
            break
    return dict(sorted(sanitized.items()))


def _strict_error_details(value: Any) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping) or len(value) > 16:
        raise ValueError("error_envelope_details_invalid")
    sanitized = sanitize_error_details(value)
    if len(sanitized) != len(value):
        raise ValueError("error_envelope_details_not_sanitized")
    return sanitized


class ErrorEnvelope(_ClosedContract):
    schema_version: Literal[
        "ananta.model-intelligence.error-envelope.v1"
    ] = Field(
        default=ERROR_ENVELOPE_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    reason_code: ModelIntelligenceReasonCode
    retryable: bool
    details: dict[str, JsonScalar]

    @field_validator("details", mode="before")
    @classmethod
    def _validate_details(cls, value: Any) -> dict[str, JsonScalar]:
        return _strict_error_details(value)

    @model_validator(mode="after")
    def _validate_retryability(self) -> "ErrorEnvelope":
        expected = self.reason_code in RETRYABLE_REASON_CODES
        if self.retryable is not expected:
            raise ValueError("error_envelope_retryability_mismatch")
        return self


def build_error_envelope(
    reason_code: ModelIntelligenceReasonCode,
    *,
    details: Mapping[str, Any] | None = None,
    extensions: Mapping[str, JsonScalar] | None = None,
) -> ErrorEnvelope:
    """Build an envelope with policy-derived retryability and safe details."""

    return ErrorEnvelope(
        reason_code=reason_code,
        retryable=reason_code in RETRYABLE_REASON_CODES,
        details=sanitize_error_details(details),
        extensions=dict(extensions or {}),
    )


_CONTRACT_MODELS: dict[
    ContractKind,
    type[ModelIdentity]
    | type[CapabilityDescriptor]
    | type[AnalysisJob]
    | type[ArtifactRef]
    | type[ErrorEnvelope],
] = {
    "model_identity": ModelIdentity,
    "capability_descriptor": CapabilityDescriptor,
    "analysis_job": AnalysisJob,
    "artifact_ref": ArtifactRef,
    "error_envelope": ErrorEnvelope,
}


def parse_model_intelligence_contract(
    kind: ContractKind,
    raw: Mapping[str, Any],
) -> ModelIdentity | CapabilityDescriptor | AnalysisJob | ArtifactRef | ErrorEnvelope:
    """Parse a v1 payload while exposing only a stable boundary reason code."""

    model = _CONTRACT_MODELS.get(kind)
    if model is None or not isinstance(raw, Mapping):
        raise ModelIntelligenceContractError(
            ModelIntelligenceReasonCode.CONTRACT_INVALID
        )
    try:
        return model.model_validate(dict(raw))
    except (TypeError, ValueError, ValidationError) as exc:
        raise ModelIntelligenceContractError(
            ModelIntelligenceReasonCode.CONTRACT_INVALID
        ) from exc


__all__ = [
    "ANALYSIS_JOB_SCHEMA",
    "ARTIFACT_REF_SCHEMA",
    "CAPABILITY_DESCRIPTOR_SCHEMA",
    "ERROR_ENVELOPE_SCHEMA",
    "MODEL_IDENTITY_SCHEMA",
    "RETRYABLE_REASON_CODES",
    "AnalysisJob",
    "ArtifactRef",
    "CapabilityDescriptor",
    "CapabilityEvidence",
    "CapabilityReasonCode",
    "CapabilityState",
    "ErrorEnvelope",
    "ModelIdentity",
    "ModelIntelligenceContractError",
    "ModelIntelligenceReasonCode",
    "build_error_envelope",
    "canonical_model_coordinates",
    "derive_model_id",
    "parse_model_intelligence_contract",
    "sanitize_error_details",
]
