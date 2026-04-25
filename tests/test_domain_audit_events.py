from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain" / "domain_audit_event.v1.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_domain_audit_event_schema_accepts_denied_and_approved_flows() -> None:
    events = [
        {
            "schema": "domain_audit_event.v1",
            "event_id": "evt-001",
            "event_type": "policy_denied",
            "task_id": "task-1",
            "approval_id": "",
            "domain_id": "example",
            "capability_id": "example.script.execute",
            "action_id": "execute",
            "policy_decision": "deny",
            "result_status": "denied",
            "context_hash": "abcdef0123456789",
            "artifact_ids": [],
            "notes": ["unsafe operation blocked"],
        },
        {
            "schema": "domain_audit_event.v1",
            "event_id": "evt-002",
            "event_type": "approval_decided",
            "task_id": "task-1",
            "approval_id": "approval-1",
            "domain_id": "example",
            "capability_id": "example.script.execute",
            "action_id": "execute",
            "policy_decision": "approval_required",
            "result_status": "approved",
            "context_hash": "abcdef0123456789",
            "artifact_ids": ["artifact-1"],
            "notes": ["operator approved bounded execution"],
        },
    ]
    validator = _validator()
    for event in events:
        assert list(validator.iter_errors(event)) == []


def test_domain_audit_event_schema_rejects_unknown_event_type() -> None:
    payload = {
        "schema": "domain_audit_event.v1",
        "event_id": "evt-003",
        "event_type": "unknown_transition",
        "task_id": "task-1",
        "domain_id": "example",
        "capability_id": "example.script.execute",
        "action_id": "execute",
        "policy_decision": "deny",
        "result_status": "denied",
        "context_hash": "abcdef0123456789",
        "artifact_ids": [],
    }
    errors = list(_validator().iter_errors(payload))
    assert errors

