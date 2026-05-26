from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_source_schemas_validate_examples() -> None:
    descriptor_validator = _validator(Path("schemas/sources/source_descriptor.v1.json"))
    source_pack_validator = _validator(Path("schemas/sources/source_pack.v1.json"))
    snapshot_validator = _validator(Path("schemas/sources/source_snapshot.v1.json"))
    reference_validator = _validator(Path("schemas/sources/source_reference.v1.json"))

    eclipse_platform = json.loads(Path("sources/eclipse/eclipse-platform.source_descriptor.json").read_text(encoding="utf-8"))
    eclipse_jdt = json.loads(Path("sources/eclipse/eclipse-jdt-core.source_descriptor.json").read_text(encoding="utf-8"))
    eclipse_pde = json.loads(Path("sources/eclipse/eclipse-pde.source_descriptor.json").read_text(encoding="utf-8"))
    eclipse_swt = json.loads(Path("sources/eclipse/eclipse-swt.source_descriptor.json").read_text(encoding="utf-8"))
    eclipse_equinox = json.loads(Path("sources/eclipse/eclipse-equinox.source_descriptor.json").read_text(encoding="utf-8"))
    keycloak = json.loads(Path("sources/keycloak/source_descriptor.json").read_text(encoding="utf-8"))
    wikipedia = json.loads(Path("sources/wikipedia/source_descriptor.json").read_text(encoding="utf-8"))
    source_pack = json.loads(Path("sources/source-packs/ananta-dev-default.source-pack.json").read_text(encoding="utf-8"))
    assert list(descriptor_validator.iter_errors(eclipse_platform)) == []
    assert list(descriptor_validator.iter_errors(eclipse_jdt)) == []
    assert list(descriptor_validator.iter_errors(eclipse_pde)) == []
    assert list(descriptor_validator.iter_errors(eclipse_swt)) == []
    assert list(descriptor_validator.iter_errors(eclipse_equinox)) == []
    assert list(descriptor_validator.iter_errors(keycloak)) == []
    assert list(descriptor_validator.iter_errors(wikipedia)) == []
    assert list(source_pack_validator.iter_errors(source_pack)) == []

    snapshot = {
        "schema": "source_snapshot.v1",
        "snapshot_id": "snap_0123456789ab",
        "source_id": "keycloak-official-docs",
        "created_at": "2026-05-26T00:00:00Z",
        "retrieved_at": "2026-05-26T00:00:00Z",
        "content_hash": "a" * 64,
        "metadata_hash": "b" * 64,
        "descriptor_hash": "c" * 64,
        "byte_size": 12,
        "item_count": 1,
        "status": "indexed",
        "reason_code": "",
        "human_message": "",
        "extensions": {}
    }
    assert list(snapshot_validator.iter_errors(snapshot)) == []

    reference = {
        "schema": "source_reference.v1",
        "source_id": "keycloak-official-docs",
        "snapshot_id": "snap_0123456789ab",
        "chunk_id": "keycloak:abc",
        "canonical_url": "https://www.keycloak.org/documentation",
        "title": "Keycloak Documentation",
        "license_ref": "license_unknown",
        "retrieved_at": "2026-05-26T00:00:00Z",
        "attribution_text": "Keycloak docs citation"
    }
    assert list(reference_validator.iter_errors(reference)) == []


def test_source_descriptor_requires_citation_source() -> None:
    descriptor_validator = _validator(Path("schemas/sources/source_descriptor.v1.json"))
    payload = json.loads(Path("sources/keycloak/source_descriptor.json").read_text(encoding="utf-8"))
    payload.pop("citation_source", None)
    assert list(descriptor_validator.iter_errors(payload))


def test_source_reference_requires_source_and_snapshot_ids() -> None:
    reference_validator = _validator(Path("schemas/sources/source_reference.v1.json"))
    payload = {
        "schema": "source_reference.v1",
        "chunk_id": "chunk-1",
        "canonical_url": "https://example.com",
        "title": "Example",
        "license_ref": "CC BY-SA",
        "retrieved_at": "2026-05-26T00:00:00Z"
    }
    assert list(reference_validator.iter_errors(payload))
