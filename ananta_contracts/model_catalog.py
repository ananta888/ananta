"""Versioned, secret-free model catalog wire contracts."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MODEL_SUMMARY_SCHEMA = "ananta.model-summary.v1"
MODEL_CATALOG_SCHEMA = "ananta.model-catalog.v1"
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

    def to_wire(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


__all__ = [
    "MODEL_CATALOG_SCHEMA",
    "MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA",
    "MODEL_DEFAULT_SELECTION_SCHEMA",
    "MODEL_SUMMARY_SCHEMA",
    "ModelAvailability",
    "ModelCatalog",
    "ModelDefaultSelection",
    "ModelDefaultSelectionCommand",
    "ModelHealth",
    "ModelRuntime",
    "ModelSummary",
    "ProviderCatalogFailure",
]
