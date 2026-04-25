from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain" / "domain_approval_request.v1.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_domain_approval_request_schema_supports_correlated_metadata() -> None:
    payload = {
        "schema": "domain_approval_request.v1",
        "approval_id": "approval-001",
        "task_id": "task-001",
        "domain_id": "example",
        "capability_id": "example.script.execute",
        "action_id": "execute",
        "expected_effects": ["run bounded script"],
        "risk": "high",
        "artifact_refs": ["artifacts/domain/example/plan.md"],
        "context_hash": "abcdef0123456789",
        "status": "approved",
        "decision_reason": "approved after review",
        "decided_by": "operator-1",
        "decided_at": "2026-04-25T15:25:39+02:00",
    }
    errors = list(Draft202012Validator(_load_schema()).iter_errors(payload))
    assert errors == []


def test_domain_approval_request_schema_allows_stale_or_denied_states() -> None:
    denied = {
        "schema": "domain_approval_request.v1",
        "approval_id": "approval-002",
        "task_id": "task-002",
        "domain_id": "example",
        "capability_id": "example.script.execute",
        "action_id": "execute",
        "expected_effects": ["run bounded script"],
        "risk": "high",
        "artifact_refs": [],
        "context_hash": "abcdef0123456789",
        "status": "denied",
        "decision_reason": "unauthorized approver",
    }
    expired = dict(denied)
    expired["approval_id"] = "approval-003"
    expired["status"] = "expired"
    validator = Draft202012Validator(_load_schema())
    assert list(validator.iter_errors(denied)) == []
    assert list(validator.iter_errors(expired)) == []

