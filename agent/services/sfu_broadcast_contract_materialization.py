"""Bounded, versioned materialization for SFU broadcast contracts.

The schema validators remain responsible for contract-specific structural and
semantic validation.  This module owns the runtime-neutral boundary shared by
Python and TypeScript: bounded JSON decoding, dangerous-key rejection,
canonical bytes, compatibility negotiation, and immutable accepted values.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, FrozenSet, Mapping


class ContractMaterializationReason(str, Enum):
    JSON_INVALID = "contract_json_invalid"
    DUPLICATE_PROPERTY = "contract_duplicate_json_key"
    ROOT_TYPE_INVALID = "contract_root_type_invalid"
    PAYLOAD_TOO_LARGE = "contract_payload_too_large"
    DEPTH_EXCEEDED = "contract_depth_exceeded"
    NODE_LIMIT_EXCEEDED = "contract_node_limit_exceeded"
    COLLECTION_LIMIT_EXCEEDED = "contract_collection_limit_exceeded"
    STRING_LIMIT_EXCEEDED = "contract_string_limit_exceeded"
    TOTAL_STRING_LIMIT_EXCEEDED = "contract_total_string_limit_exceeded"
    DANGEROUS_PROPERTY = "contract_dangerous_property"
    UNICODE_NORMALIZATION_INVALID = "contract_unicode_normalization_invalid"
    INTEGER_RANGE_EXCEEDED = "contract_integer_range_exceeded"
    NONFINITE_NUMBER = "contract_nonfinite_number"
    SCHEMA_VERSION_UNSUPPORTED = "contract_schema_version_unsupported"
    MATERIALIZATION_VERSION_UNSUPPORTED = (
        "contract_materialization_version_unsupported"
    )


class ContractMaterializationError(ValueError):
    """Fail-closed error carrying a stable cross-runtime reason code."""

    def __init__(
        self,
        reason_code: ContractMaterializationReason,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ContractMaterializationLimits:
    maximum_payload_bytes: int = 262_144
    maximum_depth: int = 32
    maximum_nodes: int = 20_000
    maximum_collection_entries: int = 2_048
    maximum_string_bytes: int = 16_384
    maximum_total_string_bytes: int = 131_072

    def __post_init__(self) -> None:
        if min(
            self.maximum_payload_bytes,
            self.maximum_depth,
            self.maximum_nodes,
            self.maximum_collection_entries,
            self.maximum_string_bytes,
            self.maximum_total_string_bytes,
        ) < 1:
            raise ValueError("all contract materialization limits must be positive")


@dataclass(frozen=True, slots=True)
class ContractVersionDescriptor:
    contract_id: str
    schema_version: str
    materialization_version: int = 1

    def __post_init__(self) -> None:
        if not self.contract_id or not self.schema_version:
            raise ValueError("contract_id and schema_version are required")
        if self.materialization_version < 1:
            raise ValueError("materialization_version must be positive")


@dataclass(frozen=True, slots=True)
class ContractReaderCompatibility:
    """Explicit reader capabilities; missing contract entries deny by default."""

    schema_versions: Mapping[str, FrozenSet[str]]
    materialization_versions: FrozenSet[int] = frozenset({1})


@dataclass(frozen=True, slots=True)
class MaterializedSfuBroadcastContract:
    descriptor: ContractVersionDescriptor
    canonical_sha256: str
    canonical_bytes: bytes
    document: Mapping[str, Any]


@dataclass(slots=True)
class _TraversalState:
    nodes: int = 0
    total_string_bytes: int = 0


class SfuBroadcastContractMaterializer:
    """Materializes validated JSON without granting it new authority."""

    _DANGEROUS_PROPERTIES = frozenset({"__proto__", "prototype", "constructor"})
    _MAXIMUM_SAFE_INTEGER = 9_007_199_254_740_991

    def __init__(
        self,
        limits: ContractMaterializationLimits | None = None,
    ) -> None:
        self._limits = limits or ContractMaterializationLimits()

    def materialize(
        self,
        raw_document: bytes | str | Mapping[str, Any],
        descriptor: ContractVersionDescriptor,
        compatibility: ContractReaderCompatibility,
    ) -> MaterializedSfuBroadcastContract:
        self._assert_compatible(descriptor, compatibility)
        document, encoded = self._decode(raw_document)
        self._walk(document, depth=1, state=_TraversalState())
        canonical = self._canonical_bytes(document)
        return MaterializedSfuBroadcastContract(
            descriptor=descriptor,
            canonical_sha256=hashlib.sha256(canonical).hexdigest(),
            canonical_bytes=canonical,
            document=self._freeze_mapping(document),
        )

    @staticmethod
    def _assert_compatible(
        descriptor: ContractVersionDescriptor,
        compatibility: ContractReaderCompatibility,
    ) -> None:
        supported_schema_versions = compatibility.schema_versions.get(
            descriptor.contract_id,
            frozenset(),
        )
        if descriptor.schema_version not in supported_schema_versions:
            raise ContractMaterializationError(
                ContractMaterializationReason.SCHEMA_VERSION_UNSUPPORTED,
                "the reader does not support this contract schema version",
            )
        if (
            descriptor.materialization_version
            not in compatibility.materialization_versions
        ):
            raise ContractMaterializationError(
                ContractMaterializationReason.MATERIALIZATION_VERSION_UNSUPPORTED,
                "the reader does not support this materialization version",
            )

    def _decode(
        self,
        raw_document: bytes | str | Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], bytes]:
        if isinstance(raw_document, bytes):
            encoded = raw_document
            try:
                text = encoded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContractMaterializationError(
                    ContractMaterializationReason.JSON_INVALID,
                    "contract payload is not valid UTF-8",
                ) from exc
            document = self._loads(text)
        elif isinstance(raw_document, str):
            try:
                encoded = raw_document.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ContractMaterializationError(
                    ContractMaterializationReason.UNICODE_NORMALIZATION_INVALID,
                    "contract payload contains an invalid Unicode scalar",
                ) from exc
            document = self._loads(raw_document)
        elif isinstance(raw_document, Mapping):
            document = raw_document
            try:
                encoded = self._canonical_bytes(document)
            except ValueError as exc:
                reason = (
                    ContractMaterializationReason.NONFINITE_NUMBER
                    if "Out of range float values" in str(exc)
                    else ContractMaterializationReason.JSON_INVALID
                )
                raise ContractMaterializationError(
                    reason,
                    "contract mapping is not JSON-compatible",
                ) from exc
            except (TypeError, UnicodeEncodeError) as exc:
                raise ContractMaterializationError(
                    ContractMaterializationReason.JSON_INVALID,
                    "contract mapping is not JSON-compatible",
                ) from exc
        else:
            raise ContractMaterializationError(
                ContractMaterializationReason.ROOT_TYPE_INVALID,
                "contract root must be a JSON object",
            )

        if len(encoded) > self._limits.maximum_payload_bytes:
            raise ContractMaterializationError(
                ContractMaterializationReason.PAYLOAD_TOO_LARGE,
                "contract payload exceeds the byte limit",
            )
        if not isinstance(document, Mapping):
            raise ContractMaterializationError(
                ContractMaterializationReason.ROOT_TYPE_INVALID,
                "contract root must be a JSON object",
            )
        return document, encoded

    @staticmethod
    def _loads(text: str) -> Any:
        def reject_nonfinite(value: str) -> None:
            raise ValueError(f"non-finite JSON number: {value}")

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise _DuplicateJsonProperty(key)
                result[key] = value
            return result

        try:
            return json.loads(
                text,
                parse_constant=reject_nonfinite,
                object_pairs_hook=reject_duplicates,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            reason = (
                ContractMaterializationReason.DUPLICATE_PROPERTY
                if isinstance(exc, _DuplicateJsonProperty)
                else ContractMaterializationReason.NONFINITE_NUMBER
                if "non-finite" in str(exc)
                else ContractMaterializationReason.JSON_INVALID
            )
            raise ContractMaterializationError(reason, "contract JSON is invalid") from exc

    def _walk(self, value: Any, *, depth: int, state: _TraversalState) -> None:
        if depth > self._limits.maximum_depth:
            raise ContractMaterializationError(
                ContractMaterializationReason.DEPTH_EXCEEDED,
                "contract nesting exceeds the depth limit",
            )
        state.nodes += 1
        if state.nodes > self._limits.maximum_nodes:
            raise ContractMaterializationError(
                ContractMaterializationReason.NODE_LIMIT_EXCEEDED,
                "contract exceeds the node limit",
            )

        if isinstance(value, Mapping):
            if len(value) > self._limits.maximum_collection_entries:
                raise ContractMaterializationError(
                    ContractMaterializationReason.COLLECTION_LIMIT_EXCEEDED,
                    "contract object exceeds the entry limit",
                )
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ContractMaterializationError(
                        ContractMaterializationReason.JSON_INVALID,
                        "contract object keys must be strings",
                    )
                if key in self._DANGEROUS_PROPERTIES:
                    raise ContractMaterializationError(
                        ContractMaterializationReason.DANGEROUS_PROPERTY,
                        "contract contains a prototype-sensitive property",
                    )
                self._count_string(key, state)
                self._walk(child, depth=depth + 1, state=state)
            return

        if isinstance(value, (list, tuple)):
            if len(value) > self._limits.maximum_collection_entries:
                raise ContractMaterializationError(
                    ContractMaterializationReason.COLLECTION_LIMIT_EXCEEDED,
                    "contract array exceeds the entry limit",
                )
            for child in value:
                self._walk(child, depth=depth + 1, state=state)
            return

        if isinstance(value, str):
            self._count_string(value, state)
            return
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int):
            if abs(value) > self._MAXIMUM_SAFE_INTEGER:
                raise ContractMaterializationError(
                    ContractMaterializationReason.INTEGER_RANGE_EXCEEDED,
                    "contract integer is outside the cross-runtime safe range",
                )
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ContractMaterializationError(
                    ContractMaterializationReason.NONFINITE_NUMBER,
                    "contract number must be finite",
                )
            if value.is_integer() and abs(value) > self._MAXIMUM_SAFE_INTEGER:
                raise ContractMaterializationError(
                    ContractMaterializationReason.INTEGER_RANGE_EXCEEDED,
                    "contract integer is outside the cross-runtime safe range",
                )
            return
        raise ContractMaterializationError(
            ContractMaterializationReason.JSON_INVALID,
            "contract contains a non-JSON value",
        )

    def _count_string(self, value: str, state: _TraversalState) -> None:
        if unicodedata.normalize("NFC", value) != value:
            raise ContractMaterializationError(
                ContractMaterializationReason.UNICODE_NORMALIZATION_INVALID,
                "contract strings must use NFC normalization",
            )
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContractMaterializationError(
                ContractMaterializationReason.UNICODE_NORMALIZATION_INVALID,
                "contract contains an invalid Unicode scalar",
            ) from exc
        if len(encoded) > self._limits.maximum_string_bytes:
            raise ContractMaterializationError(
                ContractMaterializationReason.STRING_LIMIT_EXCEEDED,
                "contract string exceeds the byte limit",
            )
        state.total_string_bytes += len(encoded)
        if state.total_string_bytes > self._limits.maximum_total_string_bytes:
            raise ContractMaterializationError(
                ContractMaterializationReason.TOTAL_STRING_LIMIT_EXCEEDED,
                "contract strings exceed the aggregate byte limit",
            )

    @staticmethod
    def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def _freeze_mapping(cls, document: Mapping[str, Any]) -> Mapping[str, Any]:
        return MappingProxyType(
            {str(key): cls._freeze(value) for key, value in document.items()}
        )

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return cls._freeze_mapping(value)
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) for item in value)
        return value


class _DuplicateJsonProperty(ValueError):
    pass
