from __future__ import annotations

import json
from pathlib import Path

from agent.services.artifact_type_registry import ArtifactTypeRegistry

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain" / "artifact_type_pack.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_artifact_type_registry_discovers_types_for_clients(tmp_path: Path) -> None:
    artifact_schema_path = tmp_path / "domains" / "example" / "schemas" / "artifact.v1.json"
    _write_json(
        artifact_schema_path,
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["artifact_id"],
            "properties": {"artifact_id": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    pack_path = tmp_path / "domains" / "example" / "artifact_types.json"
    _write_json(
        pack_path,
        {
            "schema": "artifact_type_pack.v1",
            "domain_id": "example",
            "version": "1.0.0",
            "artifact_types": [
                {
                    "artifact_type_id": "example.report.summary",
                    "domain_id": "example",
                    "display_name": "Summary",
                    "schema_ref": str(artifact_schema_path.relative_to(tmp_path)),
                    "render_hint": "markdown",
                    "safety_classification": "internal",
                    "allowed_clients": ["cli", "web"],
                }
            ],
        },
    )
    registry = ArtifactTypeRegistry(schema_path=SCHEMA_PATH, repository_root=tmp_path)
    registry.load_pack(pack_path, known_domains={"example"})
    assert registry.list_artifact_types(domain_id="example", client="cli")
    assert not registry.list_artifact_types(domain_id="example", client="tui")


def test_artifact_type_registry_validates_payload_and_reports_unknown_or_unsupported(tmp_path: Path) -> None:
    registry = ArtifactTypeRegistry(schema_path=SCHEMA_PATH, repository_root=tmp_path)
    unknown = registry.validate_artifact_payload(artifact_type_id="missing", payload={})
    assert unknown["status"] == "unknown"

    pack_path = tmp_path / "domains" / "example" / "artifact_types.json"
    _write_json(
        pack_path,
        {
            "schema": "artifact_type_pack.v1",
            "domain_id": "example",
            "version": "1.0.0",
            "artifact_types": [
                {
                    "artifact_type_id": "example.report.unknown_schema",
                    "domain_id": "example",
                    "display_name": "Unknown schema",
                    "schema_ref": "domains/example/schemas/missing.json",
                    "render_hint": "json",
                    "safety_classification": "internal",
                    "allowed_clients": ["cli"],
                }
            ],
        },
    )
    registry.load_pack(pack_path, known_domains={"example"})
    unsupported = registry.validate_artifact_payload(
        artifact_type_id="example.report.unknown_schema",
        payload={"artifact_id": "x"},
    )
    assert unsupported["status"] == "unsupported"

