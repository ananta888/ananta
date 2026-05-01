from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from client_surfaces.blender.addon.context import capture_bounded_scene_context


ROOT = Path(__file__).resolve().parents[1]


def _validator() -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas/blender/blender_scene_context.v1.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_blender_scene_context_schema_accepts_minimal_payload() -> None:
    payload = capture_bounded_scene_context("Scene", [{"name": "Cube", "type": "MESH", "selected": True}], max_objects=8)
    errors = sorted(_validator().iter_errors(payload), key=lambda err: list(err.path))
    assert errors == []


def test_blender_scene_context_schema_rejects_unbounded_raw_dump() -> None:
    payload = capture_bounded_scene_context("Scene", [{"name": "Cube", "type": "MESH", "selected": True}], max_objects=8)
    payload["raw_bpy_dump"] = {"secret": "not allowed"}
    errors = sorted(_validator().iter_errors(payload), key=lambda err: list(err.path))
    assert errors
