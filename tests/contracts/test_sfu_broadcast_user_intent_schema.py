from __future__ import annotations

import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schemas/webrtc/sfu_broadcast_user_intent.v1.json").read_text()
)
FIXTURES = ROOT / "tests/fixtures/contracts/sfu_broadcast_user_intent"


def test_v1_start_intent_is_valid() -> None:
    payload = json.loads((FIXTURES / "valid_start.json").read_text())
    jsonschema.Draft202012Validator(SCHEMA).validate(payload)


def test_v1_rejects_route_or_worker_injection() -> None:
    payload = json.loads((FIXTURES / "invalid_route_injection.json").read_text())
    errors = list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(payload))
    assert errors
