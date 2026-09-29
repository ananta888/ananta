from __future__ import annotations

from client_surfaces.blender.addon.context import capture_bounded_scene_context
from client_surfaces.blender.addon.context_preview import summarize_context_for_preview


def test_blender_context_contract_preview_and_budget() -> None:
    objects = [{"name": f"Cube{i}", "type": "MESH", "selected": i == 0} for i in range(20)]
    ctx = capture_bounded_scene_context("Scene", objects, max_objects=4)
    preview = summarize_context_for_preview(ctx, max_payload_bytes=128)

    assert len(ctx["objects"]) == 4
    assert ctx["provenance"]["objects_clipped"] is True
    assert "objects_clipped" in preview["warnings"]
