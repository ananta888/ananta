"""Deterministic, framework-independent schema upcasting foundation."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json, redact_json, sha256_json
from agent.services.workflow_runtime.errors import UnsupportedSchemaVersion

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class QuarantinedContract:
    contract_type: str
    source_schema: str
    target_schema: str
    reason: str
    payload_hash: str
    payload: dict[str, Any]
    schema: str = "ananta.quarantined_contract.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "contract_type": self.contract_type,
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "reason": self.reason,
            "payload_hash": self.payload_hash,
            "payload": copy.deepcopy(self.payload),
        }


@dataclass(frozen=True)
class _RegisteredUpcaster:
    contract_type: str
    source_schema: str
    target_schema: str
    function: Upcaster


class UpcasterRegistry:
    """Registry for pure one-version-at-a-time migration functions.

    Each function is executed twice on independent deep copies. Mutation of its
    input or non-deterministic output is rejected before migrated data can execute.
    """

    def __init__(self) -> None:
        self._upcasters: dict[tuple[str, str], _RegisteredUpcaster] = {}

    def register(
        self,
        *,
        contract_type: str,
        source_schema: str,
        target_schema: str,
        upcaster: Upcaster,
    ) -> None:
        key = (str(contract_type), str(source_schema))
        if not all((*key, str(target_schema))):
            raise ValueError("upcaster_schema_required")
        if source_schema == target_schema:
            raise ValueError("upcaster_self_transition_denied")
        if key in self._upcasters:
            raise ValueError("upcaster_already_registered")
        self._upcasters[key] = _RegisteredUpcaster(
            contract_type=key[0],
            source_schema=key[1],
            target_schema=str(target_schema),
            function=upcaster,
        )

    def upcast(
        self,
        payload: Mapping[str, Any],
        *,
        contract_type: str,
        target_schema: str,
    ) -> dict[str, Any]:
        current = copy.deepcopy(dict(payload))
        source_schema = str(current.get("schema") or "")
        if not source_schema:
            raise UnsupportedSchemaVersion("source_schema_missing")
        visited: set[str] = set()
        while source_schema != target_schema:
            if source_schema in visited:
                raise UnsupportedSchemaVersion("upcaster_cycle")
            visited.add(source_schema)
            registration = self._upcasters.get((str(contract_type), source_schema))
            if registration is None:
                raise UnsupportedSchemaVersion(
                    f"upcaster_missing:{contract_type}:{source_schema}:{target_schema}"
                )
            before = canonical_json(current)
            first_input = copy.deepcopy(current)
            second_input = copy.deepcopy(current)
            first = registration.function(first_input)
            second = registration.function(second_input)
            if (
                canonical_json(current) != before
                or canonical_json(first_input) != before
                or canonical_json(second_input) != before
            ):
                raise UnsupportedSchemaVersion("upcaster_mutated_input")
            if not isinstance(first, dict) or not isinstance(second, dict):
                raise UnsupportedSchemaVersion("upcaster_result_mapping_required")
            if canonical_json(first) != canonical_json(second):
                raise UnsupportedSchemaVersion("upcaster_non_deterministic")
            if str(first.get("schema") or "") != registration.target_schema:
                raise UnsupportedSchemaVersion("upcaster_target_schema_mismatch")
            current = copy.deepcopy(first)
            source_schema = registration.target_schema
        return current

    def upcast_or_quarantine(
        self,
        payload: Mapping[str, Any],
        *,
        contract_type: str,
        target_schema: str,
    ) -> dict[str, Any] | QuarantinedContract:
        raw = copy.deepcopy(dict(payload))
        try:
            return self.upcast(raw, contract_type=contract_type, target_schema=target_schema)
        except (UnsupportedSchemaVersion, TypeError, ValueError) as exc:
            return QuarantinedContract(
                contract_type=str(contract_type),
                source_schema=str(raw.get("schema") or "unknown"),
                target_schema=str(target_schema),
                reason=str(exc),
                payload_hash=sha256_json(raw),
                payload=dict(redact_json(raw)),
            )

    def migration_path(
        self,
        *,
        contract_type: str,
        source_schema: str,
        target_schema: str,
    ) -> tuple[str, ...]:
        path = [str(source_schema)]
        visited: set[str] = set()
        while path[-1] != target_schema:
            if path[-1] in visited:
                raise UnsupportedSchemaVersion("upcaster_cycle")
            visited.add(path[-1])
            registration = self._upcasters.get((str(contract_type), path[-1]))
            if registration is None:
                raise UnsupportedSchemaVersion("upcaster_path_missing")
            path.append(registration.target_schema)
        return tuple(path)
