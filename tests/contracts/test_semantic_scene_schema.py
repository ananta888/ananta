from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from ananta_contracts.semantic_visual import (
    SemanticVisualContractError,
    validate_semantic_scene,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/webrtc/semantic_scene.v1.json"
SCHEMA = ROOT / "schemas/webrtc/semantic_scene.v1.json"


def scene() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_golden_scene_validates_and_roundtrips_canonically() -> None:
    value = scene()
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text())).validate(value)
    assert validate_semantic_scene(value) == value


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda value: value["nodes"][0]["geometry"]["value"].update(x=0.9, width=0.2), "geometry_out_of_bounds"),
        (lambda value: value["security"].update(encryption="invented"), "unknown_security_field"),
        (lambda value: value["nodes"][0]["label"]["provenance"].update(authoritative=True), "model_not_authoritative"),
    ],
)
def test_scene_rejects_unsafe_semantics(mutate, reason: str) -> None:
    value = scene()
    mutate(value)
    with pytest.raises(SemanticVisualContractError) as error:
        validate_semantic_scene(value)
    assert error.value.reason_code == reason


def test_scene_rejects_cycles_and_excessive_depth() -> None:
    cyclic = scene()
    cyclic["nodes"][0]["parent_id"] = "region-1"
    with pytest.raises(SemanticVisualContractError) as error:
        validate_semantic_scene(cyclic)
    assert error.value.reason_code == "scene_cycle"

    deep = scene()
    template = deep["nodes"][0]
    deep["nodes"] = []
    for index in range(9):
        node = copy.deepcopy(template)
        node["id"] = f"node-{index}"
        node["parent_id"] = None if index == 0 else f"node-{index - 1}"
        deep["nodes"].append(node)
    with pytest.raises(SemanticVisualContractError) as error:
        validate_semantic_scene(deep)
    assert error.value.reason_code == "scene_depth_exceeded"
