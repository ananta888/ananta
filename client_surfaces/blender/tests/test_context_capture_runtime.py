from __future__ import annotations

from types import SimpleNamespace

from client_surfaces.blender.addon.context import capture_context_from_bpy


def test_context_capture_from_fake_bpy() -> None:
    cube = SimpleNamespace(name="Cube", type="MESH", material_slots=[], modifiers=[], visible_get=lambda: True)
    bpy = SimpleNamespace(
        context=SimpleNamespace(scene=SimpleNamespace(name="Scene"), selected_objects=[cube]),
        data=SimpleNamespace(objects=[cube]),
        app=SimpleNamespace(version=(4, 0, 0)),
    )

    ctx = capture_context_from_bpy(bpy, max_objects=8)

    assert ctx["scene_name"] == "Scene"
    assert ctx["selection"] == ["Cube"]
    assert ctx["objects"][0]["visible"] is True
