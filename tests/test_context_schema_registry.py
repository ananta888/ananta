from __future__ import annotations

import json
from pathlib import Path

from agent.services.context_schema_registry import ContextSchemaRegistry


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_context_schema_registry_accepts_valid_payload(tmp_path: Path) -> None:
    schema_path = tmp_path / "domains" / "example" / "schemas" / "context.v1.json"
    _write_json(
        schema_path,
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["scene_id"],
            "properties": {"scene_id": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    descriptors = {"example": {"context_schemas": [str(schema_path.relative_to(tmp_path))]}}
    registry = ContextSchemaRegistry(repository_root=tmp_path)
    registry.load_from_descriptors(descriptors)
    result = registry.validate_context(domain_id="example", payload={"scene_id": "main"})
    assert result["status"] == "accepted"


def test_context_schema_registry_rejects_malformed_payload_and_unknown_domain(tmp_path: Path) -> None:
    registry = ContextSchemaRegistry(repository_root=tmp_path)
    registry.load_from_descriptors({"example": {"context_schemas": []}})
    malformed = registry.validate_context(domain_id="example", payload="invalid")
    unknown = registry.validate_context(domain_id="unknown", payload={"x": 1})
    assert malformed["status"] == "rejected"
    assert unknown["status"] == "degraded"


def test_context_schema_registry_marks_oversized_payload_as_degraded(tmp_path: Path) -> None:
    schema_path = tmp_path / "domains" / "example" / "schemas" / "context.v1.json"
    _write_json(schema_path, {"type": "object"})
    registry = ContextSchemaRegistry(repository_root=tmp_path, max_payload_bytes=20)
    registry.load_from_descriptors({"example": {"context_schemas": [str(schema_path.relative_to(tmp_path))]}})
    result = registry.validate_context(domain_id="example", payload={"large": "x" * 100})
    assert result["status"] == "degraded"

