from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_policy_loader import DomainPolicyLoader

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_SCHEMA = ROOT / "schemas" / "domain" / "capability_pack.v1.json"
POLICY_SCHEMA = ROOT / "schemas" / "domain" / "policy_pack.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _capability_pack(domain_id: str = "example") -> dict:
    return {
        "schema": "capability_pack.v1",
        "domain_id": domain_id,
        "version": "1.0.0",
        "capabilities": [
            {
                "capability_id": "example.read.status",
                "domain_id": domain_id,
                "display_name": "Read",
                "category": "read",
                "risk": "low",
                "read_only": True,
                "mutating": False,
                "approval_required": False,
                "default_policy_state": "allow",
            }
        ],
    }


def test_domain_policy_loader_validates_rules_against_capabilities(tmp_path: Path) -> None:
    capability_pack_path = tmp_path / "domains" / "example" / "capabilities.json"
    _write_json(capability_pack_path, _capability_pack())
    capability_registry = CapabilityRegistry(schema_path=CAPABILITY_SCHEMA, repository_root=tmp_path)
    capability_registry.load_pack(capability_pack_path, known_domains={"example"})

    policy_path = tmp_path / "domains" / "example" / "policies" / "policy.v1.json"
    _write_json(
        policy_path,
        {
            "schema": "policy_pack.v1",
            "domain_id": "example",
            "version": "1.0.0",
            "default_decision": "default_deny",
            "rules": [{"capability_id": "example.read.status", "action_id": "read", "decision": "allow"}],
        },
    )
    loader = DomainPolicyLoader(
        capability_registry=capability_registry,
        schema_path=POLICY_SCHEMA,
        repository_root=tmp_path,
    )
    loaded = loader.load_pack(policy_path, known_domains={"example"})
    assert loaded["domain_id"] == "example"


def test_domain_policy_loader_uses_safe_defaults_when_missing_or_invalid(tmp_path: Path) -> None:
    capability_pack_path = tmp_path / "domains" / "example" / "capabilities.json"
    _write_json(capability_pack_path, _capability_pack())
    capability_registry = CapabilityRegistry(schema_path=CAPABILITY_SCHEMA, repository_root=tmp_path)
    capability_registry.load_pack(capability_pack_path, known_domains={"example"})
    loader = DomainPolicyLoader(
        capability_registry=capability_registry,
        schema_path=POLICY_SCHEMA,
        repository_root=tmp_path,
    )

    missing = loader.load_for_domain(domain_id="example", policy_refs=[], known_domains={"example"})
    assert missing["default_decision"] == "default_deny"
    assert missing["status"] == "degraded"

    invalid_path = tmp_path / "domains" / "example" / "policies" / "invalid.json"
    _write_json(invalid_path, {"schema": "policy_pack.v1", "domain_id": "example"})
    invalid = loader.load_for_domain(
        domain_id="example",
        policy_refs=[str(invalid_path.relative_to(tmp_path))],
        known_domains={"example"},
    )
    assert invalid["default_decision"] == "default_deny"
    assert invalid["status"] == "degraded"


def test_domain_policy_loader_rejects_unknown_capability_references(tmp_path: Path) -> None:
    capability_pack_path = tmp_path / "domains" / "example" / "capabilities.json"
    _write_json(capability_pack_path, _capability_pack())
    capability_registry = CapabilityRegistry(schema_path=CAPABILITY_SCHEMA, repository_root=tmp_path)
    capability_registry.load_pack(capability_pack_path, known_domains={"example"})

    policy_path = tmp_path / "domains" / "example" / "policies" / "policy.v1.json"
    _write_json(
        policy_path,
        {
            "schema": "policy_pack.v1",
            "domain_id": "example",
            "version": "1.0.0",
            "default_decision": "default_deny",
            "rules": [{"capability_id": "example.unknown.cap", "action_id": "read", "decision": "allow"}],
        },
    )
    loader = DomainPolicyLoader(
        capability_registry=capability_registry,
        schema_path=POLICY_SCHEMA,
        repository_root=tmp_path,
    )
    with pytest.raises(ValueError, match="unknown capability_id"):
        loader.load_pack(policy_path, known_domains={"example"})

