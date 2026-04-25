from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.capability_registry import CapabilityRegistry

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain" / "capability_pack.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _pack(domain_id: str, capability_id: str, *, risk: str = "low") -> dict:
    return {
        "schema": "capability_pack.v1",
        "domain_id": domain_id,
        "version": "1.0.0",
        "capabilities": [
            {
                "capability_id": capability_id,
                "domain_id": domain_id,
                "display_name": "cap",
                "category": "read",
                "risk": risk,
                "read_only": True,
                "mutating": False,
                "approval_required": False,
                "default_policy_state": "allow",
            }
        ],
    }


def test_capability_registry_loads_and_groups_by_domain_and_category(tmp_path: Path) -> None:
    pack_path = tmp_path / "domains" / "example" / "capabilities.json"
    _write_json(pack_path, _pack("example", "example.read.status"))
    registry = CapabilityRegistry(schema_path=SCHEMA_PATH, repository_root=tmp_path)
    registry.load_pack(pack_path, known_domains={"example"})
    assert registry.capability("example.read.status")["category"] == "read"
    grouped = registry.capabilities_by_category("example")
    assert "read" in grouped


def test_capability_registry_rejects_unknown_risk_levels(tmp_path: Path) -> None:
    pack_path = tmp_path / "domains" / "example" / "capabilities.json"
    _write_json(pack_path, _pack("example", "example.read.bad", risk="severe"))
    registry = CapabilityRegistry(schema_path=SCHEMA_PATH, repository_root=tmp_path)
    with pytest.raises(ValueError, match="invalid capability pack"):
        registry.load_pack(pack_path, known_domains={"example"})


def test_capability_registry_rejects_duplicate_capability_ids(tmp_path: Path) -> None:
    first = tmp_path / "domains" / "a" / "capabilities.json"
    second = tmp_path / "domains" / "b" / "capabilities.json"
    _write_json(first, _pack("a", "shared.cap"))
    _write_json(second, _pack("b", "shared.cap"))
    registry = CapabilityRegistry(schema_path=SCHEMA_PATH, repository_root=tmp_path)
    registry.load_pack(first, known_domains={"a", "b"})
    with pytest.raises(ValueError, match="duplicate capability_id"):
        registry.load_pack(second, known_domains={"a", "b"})


def test_capability_registry_rejects_missing_domain_reference(tmp_path: Path) -> None:
    pack_path = tmp_path / "domains" / "ghost" / "capabilities.json"
    _write_json(pack_path, _pack("ghost", "ghost.read.status"))
    registry = CapabilityRegistry(schema_path=SCHEMA_PATH, repository_root=tmp_path)
    with pytest.raises(ValueError, match="unknown domain_id"):
        registry.load_pack(pack_path, known_domains={"example"})

