from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from ananta_contracts.collaboration_contract_gate import CollaborationContractGate

FIXTURE = Path("tests/fixtures/scenarios/collaboration-contract-parity.v1.json")
SCHEMAS = {
    "workspace": "workspace.v1.json",
    "actor": "actor-binding.v1.json",
    "room": "room.v1.json",
    "event": "workspace-event.v1.json",
    "membership": "membership.v1.json",
    "live": "live-envelope.v1.json",
    "resource": "resource-offer.v1.json",
    "intent": "agent-intent.v1.json",
    "bridge_capability": "bridge-capability.v1.json",
}


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def mutate(source: dict, mutation: dict) -> dict:
    result = copy.deepcopy(source)
    if mutation["operation"] == "merge":
        result.update(mutation["value"])
        return result
    target = result
    parts = mutation["path"].split(".")
    for part in parts[:-1]:
        target = target[part]
    value = mutation["value"]
    if mutation["operation"] == "repeat":
        value *= mutation["count"]
    target[parts[-1]] = value
    return result


def test_every_positive_fixture_matches_python_and_public_schema() -> None:
    gate = CollaborationContractGate()
    for kind, payload in fixture()["contracts"].items():
        assert gate.validate(kind, payload) == payload
        schema = json.loads(Path("schemas/collaboration", SCHEMAS[kind]).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("case", fixture()["negative_cases"], ids=lambda case: case["id"])
def test_shared_negative_matrix_has_stable_python_reason_codes(case: dict) -> None:
    payload = mutate(fixture()["contracts"][case["contract"]], case["mutation"])
    with pytest.raises((PermissionError, ValueError)) as caught:
        CollaborationContractGate().validate(case["contract"], payload, **case["context"])
    assert str(caught.value) == case["expected_reason_code"]
