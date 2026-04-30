from __future__ import annotations

from client_surfaces.blender.addon.context import capture_bounded_scene_context


def test_scene_context_contract_bounded():
    objects=[{"name":f"o{i}","type":"MESH","selected":i==0} for i in range(200)]
    ctx=capture_bounded_scene_context("Scene",objects,max_objects=32)
    assert ctx["scene"]["name"]=="Scene"
    assert len(ctx["objects"])==32
    assert ctx["provenance"]["bounded"] is True
