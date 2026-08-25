"""Versioned, secret-free model catalog wire contracts."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MODEL_SUMMARY_SCHEMA = "ananta.model-summary.v1"
MODEL_CATALOG_SCHEMA = "ananta.model-catalog.v1"
MODEL_CATALOG_V2_SCHEMA = "ananta.model-catalog.v2"
IMPORTED_MODEL_VERSION_SCHEMA = "ananta.imported-model-version.v1"
MODEL_CAPABILITY_FACET_SCHEMA = "ananta.model-capability-facet.v1"
MODEL_DEFAULT_SELECTION_SCHEMA = "ananta.model-default-selection.v1"
MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA = "ananta.model-default-selection-command.v1"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")


class ModelRuntime(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    REMOTE = "remote"
    VOICE = "voice"
    UNKNOWN = "unknown"


class ModelAvailability(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ModelHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ModelSourceKind(str, Enum):
    CONFIGURED = "configured"
    DISCOVERED = "discovered"
    OBSERVED_RUNTIME = "observed_runtime"
    IMPORTED = "imported"
    REMOTE = "remote"


class ModelMetadataEvidence(str, Enum):
    DECLARED = "declared"
    DETECTED = "detected"
    BENCHMARK = "benchmark"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class _ClosedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ModelSummary(_ClosedContract):
    schema_version: Literal["ananta.model-summary.v1"] = Field(
        default=MODEL_SUMMARY_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    provider_id: str
    runtime: ModelRuntime
    model_id: str
    display_name: str = Field(min_length=1, max_length=512)
    availability: ModelAvailability
    loaded: bool | None = None
    context_window: int | None = Field(default=None, ge=1, le=100_000_000)
    quantization: str | None = Field(default=None, min_length=1, max_length=64)
    capabilities: tuple[str, ...] = ()
    health: ModelHealth
    is_default: bool = False

    @field_validator("provider_id", "model_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_catalog_identifier_invalid")
        return value

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("model_catalog_display_name_invalid")
        return value

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            sorted(
                {
                    str(value).strip().lower()
                    for value in values
                    if str(value).strip()
                }
            )
        )
        if len(normalized) > 64 or any(
            not _CAPABILITY.fullmatch(value) for value in normalized
        ):
            raise ValueError("model_catalog_capabilities_invalid")
        return normalized


class ModelCapabilityFacet(_ClosedContract):
    schema_version: Literal["ananta.model-capability-facet.v1"] = Field(
        default=MODEL_CAPABILITY_FACET_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    facet_id: str = Field(
        validation_alias="id",
        serialization_alias="id",
    )
    available: bool
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("facet_id")
    @classmethod
    def _validate_facet_id(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _CAPABILITY.fullmatch(normalized):
            raise ValueError("model_catalog_capability_facet_invalid")
        return normalized


class ImportedModelVersion(_ClosedContract):
    schema_version: Literal["ananta.imported-model-version.v1"] = Field(
        default=IMPORTED_MODEL_VERSION_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    version_id: str
    catalog_revision: int = Field(ge=1)
    version: int = Field(ge=1)
    tenant_id: str
    model_id: str
    display_name: str = Field(min_length=1, max_length=512)
    source_id: str
    immutable_revision: str = Field(min_length=1, max_length=256)
    snapshot_sha256: str
    content_sha256: str
    license_status: Literal["pending", "approved", "rejected", "unknown"]
    model_format: str = Field(
        min_length=1,
        max_length=64,
        validation_alias="format",
        serialization_alias="format",
    )
    size_bytes: int = Field(ge=1, le=100 * 1024**3)
    architecture: str = Field(min_length=1, max_length=128)
    quantization: str | None = Field(default=None, min_length=1, max_length=64)
    capability_facets: tuple[ModelCapabilityFacet, ...] = ()
    import_task_id: str

    @field_validator(
        "version_id",
        "tenant_id",
        "model_id",
        "source_id",
        "import_task_id",
    )
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("imported_model_identifier_invalid")
        return value

    @field_validator("snapshot_sha256", "content_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("imported_model_hash_invalid")
        return value

    @field_validator("model_format", "architecture", "quantization")
    @classmethod
    def _validate_bounded_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized or re.fullmatch(r"[a-z0-9][a-z0-9_.+-]{0,127}", normalized) is None:
            raise ValueError("imported_model_metadata_invalid")
        return normalized

    @field_validator("capability_facets")
    @classmethod
    def _validate_unique_facets(
        cls,
        values: tuple[ModelCapabilityFacet, ...],
    ) -> tuple[ModelCapabilityFacet, ...]:
        ordered = tuple(sorted(values, key=lambda value: value.facet_id))
        if len(ordered) > 64 or len({value.facet_id for value in ordered}) != len(ordered):
            raise ValueError("imported_model_capability_facets_invalid")
        return ordered


class ProviderCatalogFailure(_ClosedContract):
    provider_id: str
    reason_code: str

    @field_validator("provider_id", "reason_code")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_catalog_failure_invalid")
        return value


class ModelDefaultSelection(_ClosedContract):
    schema_version: Literal["ananta.model-default-selection.v1"] = Field(
        default=MODEL_DEFAULT_SELECTION_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    provider_id: str
    model_id: str

    @field_validator("provider_id", "model_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_default_selection_identifier_invalid")
        return value


class ModelDefaultSelectionCommand(_ClosedContract):
    schema_version: Literal[
        "ananta.model-default-selection-command.v1"
    ] = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    provider_id: str
    model_id: str

    @field_validator("provider_id", "model_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_default_selection_identifier_invalid")
        return value


class ModelCatalog(_ClosedContract):
    schema_version: Literal["ananta.model-catalog.v1"] = Field(
        default=MODEL_CATALOG_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    default_selection: ModelDefaultSelection | None = None
    models: tuple[ModelSummary, ...] = ()
    provider_failures: tuple[ProviderCatalogFailure, ...] = ()
    catalog_revision: int | None = Field(default=None, ge=1)
    imported_models: tuple[ImportedModelVersion, ...] = ()

    def to_wire(self) -> dict:
        payload = self.model_dump(mode="json", by_alias=True)
        if self.catalog_revision is None:
            payload.pop("catalog_revision", None)
        if not self.imported_models:
            payload.pop("imported_models", None)
        return payload


class ModelCapabilityClaim(_ClosedContract):
    capability_id: str
    value: Literal["supported", "unsupported", "unknown"] = "unknown"
    evidence: ModelMetadataEvidence = ModelMetadataEvidence.UNKNOWN
    source_id: str | None = None

    @field_validator("capability_id")
    @classmethod
    def _capability_id(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _CAPABILITY.fullmatch(normalized):
            raise ValueError("model_capability_claim_invalid")
        return normalized

    @field_validator("source_id")
    @classmethod
    def _capability_source(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_capability_source_invalid")
        return value


class ModelMetadataFact(_ClosedContract):
    fact_id: str
    value: str = Field(min_length=1, max_length=512)
    evidence: ModelMetadataEvidence = ModelMetadataEvidence.UNKNOWN
    source_id: str
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("fact_id")
    @classmethod
    def _fact_id(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _CAPABILITY.fullmatch(normalized):
            raise ValueError("model_metadata_fact_id_invalid")
        return normalized

    @field_validator("source_id")
    @classmethod
    def _fact_source(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_metadata_fact_source_invalid")
        return value


class ModelInventoryDescriptor(_ClosedContract):
    schema_version: Literal["ananta.model-inventory-descriptor.v2"] = Field(
        default="ananta.model-inventory-descriptor.v2",
        validation_alias="schema",
        serialization_alias="schema",
    )
    provider_id: str
    model_id: str
    executor_id: str
    display_name: str = Field(min_length=1, max_length=512)
    runtime: ModelRuntime = ModelRuntime.UNKNOWN
    source_ids: tuple[str, ...] = ()
    source_kinds: tuple[ModelSourceKind, ...] = ()
    profile_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    availability: ModelAvailability = ModelAvailability.UNKNOWN
    health: ModelHealth = ModelHealth.UNKNOWN
    configured: bool = False
    installed: bool | None = None
    loaded: bool | None = None
    listing_supported: bool = False
    auth_mode: str | None = Field(default=None, max_length=64)
    auth_ready: bool | None = None
    context_window: int | None = Field(default=None, ge=1, le=100_000_000)
    quantization: str | None = Field(default=None, min_length=1, max_length=64)
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    price_input_per_million: float | None = Field(default=None, ge=0)
    price_output_per_million: float | None = Field(default=None, ge=0)
    capabilities: tuple[ModelCapabilityClaim, ...] = ()
    metadata_facts: tuple[ModelMetadataFact, ...] = ()
    conflicts: tuple[str, ...] = ()
    used_by_consumers: tuple[str, ...] = ()

    @field_validator("provider_id", "model_id", "executor_id")
    @classmethod
    def _inventory_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_inventory_identifier_invalid")
        return value

    @field_validator("source_ids", "profile_ids", "aliases", "used_by_consumers")
    @classmethod
    def _inventory_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(values)))
        if len(normalized) > 256 or any(
            not _IDENTIFIER.fullmatch(value) for value in normalized
        ):
            raise ValueError("model_inventory_identifiers_invalid")
        return normalized

    @field_validator("input_modalities", "output_modalities")
    @classmethod
    def _inventory_modalities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({str(value).strip().lower() for value in values}))
        if len(normalized) > 32 or any(
            not _CAPABILITY.fullmatch(value) for value in normalized
        ):
            raise ValueError("model_inventory_modalities_invalid")
        return normalized


class ModelInventorySourceStatus(_ClosedContract):
    source_id: str
    source_kind: ModelSourceKind
    status: Literal["healthy", "degraded", "unavailable", "stale", "unknown"]
    stale: bool = False
    from_cache: bool = False
    last_attempt_at: str | None = Field(default=None, max_length=64)
    last_success_at: str | None = Field(default=None, max_length=64)
    reason_code: str | None = Field(default=None, max_length=160)
    model_count: int = Field(default=0, ge=0, le=100_000)

    @field_validator("source_id")
    @classmethod
    def _source_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_inventory_source_invalid")
        return value


class ModelCatalogV2(_ClosedContract):
    schema_version: Literal["ananta.model-catalog.v2"] = Field(
        default=MODEL_CATALOG_V2_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    catalog_revision: int = Field(ge=1)
    models: tuple[ModelInventoryDescriptor, ...] = ()
    sources: tuple[ModelInventorySourceStatus, ...] = ()
    partial: bool = False


__all__ = [
    "MODEL_CATALOG_SCHEMA",
    "MODEL_CATALOG_V2_SCHEMA",
    "IMPORTED_MODEL_VERSION_SCHEMA",
    "MODEL_CAPABILITY_FACET_SCHEMA",
    "MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA",
    "MODEL_DEFAULT_SELECTION_SCHEMA",
    "MODEL_SUMMARY_SCHEMA",
    "ModelAvailability",
    "ModelCatalog",
    "ModelCatalogV2",
    "ModelCapabilityClaim",
    "ModelDefaultSelection",
    "ModelDefaultSelectionCommand",
    "ModelHealth",
    "ModelInventoryDescriptor",
    "ModelInventorySourceStatus",
    "ModelMetadataEvidence",
    "ModelMetadataFact",
    "ImportedModelVersion",
    "ModelCapabilityFacet",
    "ModelRuntime",
    "ModelSourceKind",
    "ModelSummary",
    "ProviderCatalogFailure",
]
