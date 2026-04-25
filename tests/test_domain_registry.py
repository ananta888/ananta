from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.domain_registry import DomainRegistry

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain" / "domain_descriptor.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _descriptor(domain_id: str, capability_pack_ref: str) -> dict:
    return {
        "schema": "domain_descriptor.v1",
        "domain_id": domain_id,
        "display_name": f"{domain_id} domain",
        "version": "1.0.0",
        "lifecycle_status": "foundation_only",
        "runtime_status": "descriptor_only",
        "owner": "ananta",
        "description": "descriptor test",
        "supported_clients": ["cli"],
        "source_paths": {"descriptor_root": f"domains/{domain_id}", "code_paths": [], "docs_paths": []},
        "capability_pack": capability_pack_ref,
        "context_schemas": [],
        "artifact_schemas": [],
        "policy_packs": [],
        "rag_profiles": [],
        "bridge_adapter_type": "none",
        "extensions": {},
    }


def test_domain_registry_loads_valid_descriptors() -> None:
    registry = DomainRegistry(domain_dirs=[ROOT / "domains"], descriptor_schema_path=SCHEMA_PATH, repository_root=ROOT)
    descriptors = registry.load()
    assert "example" in descriptors
    assert registry.get_descriptor("example")["runtime_status"] == "descriptor_only"
    listed = registry.list_domains()
    assert any(item["domain_id"] == "example" for item in listed)


def test_domain_registry_rejects_malformed_descriptor(tmp_path: Path) -> None:
    domain_dir = tmp_path / "domains" / "alpha"
    _write_json(domain_dir / "domain.json", {"schema": "domain_descriptor.v1", "domain_id": "alpha"})
    _write_json(domain_dir / "capabilities.json", {"schema": "capability_pack.v1"})
    registry = DomainRegistry(
        domain_dirs=[tmp_path / "domains"],
        descriptor_schema_path=SCHEMA_PATH,
        repository_root=tmp_path,
    )
    with pytest.raises(ValueError, match="invalid domain descriptor"):
        registry.load()


def test_domain_registry_rejects_duplicate_domain_ids(tmp_path: Path) -> None:
    domains_root = tmp_path / "domains"
    for folder in ("one", "two"):
        domain_dir = domains_root / folder
        _write_json(domain_dir / "capabilities.json", {"schema": "capability_pack.v1"})
    _write_json(domains_root / "one" / "domain.json", _descriptor("dup", "domains/one/capabilities.json"))
    _write_json(domains_root / "two" / "domain.json", _descriptor("dup", "domains/two/capabilities.json"))
    registry = DomainRegistry(domain_dirs=[domains_root], descriptor_schema_path=SCHEMA_PATH, repository_root=tmp_path)
    with pytest.raises(ValueError, match="duplicate domain_id"):
        registry.load()


def test_domain_registry_rejects_missing_referenced_files(tmp_path: Path) -> None:
    domain_dir = tmp_path / "domains" / "missing"
    _write_json(domain_dir / "domain.json", _descriptor("missing", "domains/missing/capabilities.json"))
    registry = DomainRegistry(
        domain_dirs=[tmp_path / "domains"],
        descriptor_schema_path=SCHEMA_PATH,
        repository_root=tmp_path,
    )
    with pytest.raises(ValueError, match="reference not found"):
        registry.load()
