from __future__ import annotations

import json
from pathlib import Path

from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_policy_service import DomainPolicyService

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_SCHEMA = ROOT / "schemas" / "domain" / "capability_pack.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _prepare_service(tmp_path: Path) -> DomainPolicyService:
    pack = {
        "schema": "capability_pack.v1",
        "domain_id": "example",
        "version": "1.0.0",
        "capabilities": [
            {
                "capability_id": "example.read.status",
                "domain_id": "example",
                "display_name": "Read",
                "category": "read",
                "risk": "low",
                "read_only": True,
                "mutating": False,
                "approval_required": False,
                "default_policy_state": "allow",
            },
            {
                "capability_id": "example.script.execute",
                "domain_id": "example",
                "display_name": "Execute script",
                "category": "exec",
                "risk": "critical",
                "read_only": False,
                "mutating": True,
                "approval_required": True,
                "default_policy_state": "approval_required",
            },
        ],
    }
    path = tmp_path / "domains" / "example" / "capabilities.json"
    _write_json(path, pack)
    registry = CapabilityRegistry(schema_path=CAPABILITY_SCHEMA, repository_root=tmp_path)
    registry.load_pack(path, known_domains={"example"})
    return DomainPolicyService(capability_registry=registry)


def test_domain_policy_service_returns_allow_approval_and_deny_for_known_rules(tmp_path: Path) -> None:
    service = _prepare_service(tmp_path)
    policy_document = {
        "schema": "policy_pack.v1",
        "domain_id": "example",
        "version": "1.0.0",
        "status": "loaded",
        "default_decision": "default_deny",
        "rules": [
            {"capability_id": "example.read.status", "action_id": "read", "decision": "allow"},
            {"capability_id": "example.script.execute", "action_id": "preview", "decision": "approval_required"},
            {"capability_id": "example.script.execute", "action_id": "execute", "decision": "deny"},
        ],
    }
    allow_decision = service.evaluate(
        domain_id="example",
        capability_id="example.read.status",
        action_id="read",
        context_summary={"source": "unit"},
        actor_metadata={"role": "user"},
        policy_document=policy_document,
    ).as_dict()
    approval_decision = service.evaluate(
        domain_id="example",
        capability_id="example.script.execute",
        action_id="preview",
        context_summary={"source": "unit"},
        actor_metadata={"role": "user"},
        policy_document=policy_document,
    ).as_dict()
    deny_decision = service.evaluate(
        domain_id="example",
        capability_id="example.script.execute",
        action_id="execute",
        context_summary={"source": "unit"},
        actor_metadata={"role": "user"},
        policy_document=policy_document,
    ).as_dict()

    assert allow_decision["decision"] == "allow"
    assert approval_decision["decision"] == "approval_required"
    assert deny_decision["decision"] == "deny"


def test_domain_policy_service_denies_unknown_capability_and_handles_degraded_policy(tmp_path: Path) -> None:
    service = _prepare_service(tmp_path)
    unknown_capability = service.evaluate(
        domain_id="example",
        capability_id="example.unknown",
        action_id="read",
        context_summary={},
        actor_metadata={},
        policy_document={"status": "loaded", "default_decision": "allow", "rules": []},
    ).as_dict()
    degraded_policy = service.evaluate(
        domain_id="example",
        capability_id="example.read.status",
        action_id="read",
        context_summary={},
        actor_metadata={},
        policy_document={"status": "degraded", "reason": "missing_policy_pack"},
    ).as_dict()

    assert unknown_capability["decision"] == "deny"
    assert unknown_capability["reason"] == "unknown_capability"
    assert degraded_policy["decision"] == "degraded"
    assert degraded_policy["reason"] == "policy_pack_not_loaded"

