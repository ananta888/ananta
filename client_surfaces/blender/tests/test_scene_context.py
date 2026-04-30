from __future__ import annotations

from client_surfaces.blender.addon.context import capture_bounded_scene_context
from client_surfaces.blender.addon.context_preview import summarize_context_for_preview


def test_context_preview_has_exclusion_marker():
    ctx=capture_bounded_scene_context('S',[{"name":"Cube","type":"MESH","selected":True}],max_objects=8)
    prev=summarize_context_for_preview(ctx)
    assert prev['object_count']==1
    assert prev['excluded']=='bounded_payload'
