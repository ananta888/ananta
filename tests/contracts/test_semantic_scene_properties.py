from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from ananta_contracts.semantic_visual import SemanticVisualContractError, validate_semantic_scene

ROOT = Path(__file__).resolve().parents[2]


def golden() -> dict:
    return json.loads((ROOT / "tests/fixtures/webrtc/semantic_scene.v1.json").read_text())


def test_normalized_rectangle_property_accepts_in_bounds_and_rejects_overflow() -> None:
    rng = random.Random(0x5CE1E)
    for _ in range(500):
        x = rng.random() * 0.9
        y = rng.random() * 0.9
        width = rng.random() * (1 - x - 0.001) + 0.001
        height = rng.random() * (1 - y - 0.001) + 0.001
        valid = golden()
        valid["nodes"][0]["geometry"]["value"].update(x=x, y=y, width=width, height=height)
        assert validate_semantic_scene(valid)["nodes"][0]["id"] == "region-1"

        invalid = copy.deepcopy(valid)
        invalid["nodes"][0]["geometry"]["value"]["width"] = 1 - x + 0.001
        with pytest.raises(SemanticVisualContractError) as error:
            validate_semantic_scene(invalid)
        assert error.value.reason_code == "geometry_out_of_bounds"


def test_non_finite_property_never_crosses_contract_boundary() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        scene = golden()
        scene["nodes"][0]["motion"]["value"]["dx_per_second"] = value
        with pytest.raises(SemanticVisualContractError) as error:
            validate_semantic_scene(scene)
        assert error.value.reason_code == "non_finite"
