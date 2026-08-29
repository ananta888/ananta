"""Canonical JSON-only DSPy state ingestion and native program export."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ananta_contracts.dspy_optimization import PromptProgramV1, canonical_digest, canonical_json
from worker.optimization.dspy.module_registry import DspyModuleRegistry

_FORBIDDEN_KEYS = frozenset(
    {"api_key", "authorization", "api_base", "base_url", "model_list", "callable", "class_path", "file_path", "tools"}
)


class DspyJsonProgramSerializer:
    def __init__(self, registry: DspyModuleRegistry | None = None) -> None:
        self._registry = registry or DspyModuleRegistry()

    def export(
        self,
        *,
        tenant_id: str,
        program_id: str,
        program_kind: str,
        dspy_state: Mapping[str, Any],
        model_roles: Mapping[str, str],
        dspy_version: str,
    ) -> PromptProgramV1:
        self._closed_json_state(dspy_state)
        allowed = {"module_graph", "signatures", "demonstrations", "metadata"}
        if set(dspy_state) - allowed:
            raise ValueError("dspy_state_unknown_field")
        graph = [dict(item) for item in dspy_state.get("module_graph") or ()]
        signatures = [dict(item) for item in dspy_state.get("signatures") or ()]
        demonstrations = [dict(item) for item in dspy_state.get("demonstrations") or ()]
        self._registry.validate(program_kind=program_kind, module_graph=graph, signatures=signatures)
        source_digest = canonical_digest(dspy_state)
        return PromptProgramV1(
            tenant_id=tenant_id,
            program_id=program_id,
            program_kind=program_kind,
            module_graph=graph,
            signatures=signatures,
            demonstrations=demonstrations,
            model_roles=dict(model_roles),
            source_program_digest=source_digest,
            exporter_version=f"dspy-json-v1:{dspy_version}",
        )

    def dumps(self, program: PromptProgramV1) -> bytes:
        return canonical_json(program.to_dict()).encode()

    def loads(self, payload: bytes) -> PromptProgramV1:
        if len(payload) > 5 * 1024 * 1024 or payload[:2] == b"\x80\x04":
            raise ValueError("dspy_program_payload_denied")
        try:
            raw = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("dspy_program_json_invalid") from exc
        if not isinstance(raw, dict):
            raise ValueError("dspy_program_json_invalid")
        allowed = set(PromptProgramV1.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError("dspy_program_unknown_field")
        return PromptProgramV1(**raw)

    def _closed_json_state(self, value: Any, depth: int = 0) -> None:
        if depth > 24:
            raise ValueError("dspy_state_depth_exceeded")
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in _FORBIDDEN_KEYS:
                    raise ValueError("dspy_state_unsafe_field")
                self._closed_json_state(item, depth + 1)
        elif isinstance(value, list):
            if len(value) > 100_000:
                raise ValueError("dspy_state_collection_too_large")
            for item in value:
                self._closed_json_state(item, depth + 1)
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("dspy_state_non_json_value")


__all__ = ["DspyJsonProgramSerializer"]
