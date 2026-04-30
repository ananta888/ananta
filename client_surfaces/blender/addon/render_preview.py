from __future__ import annotations


def build_preview_plan(*, width: int = 512, height: int = 512) -> dict:
    return {"kind":"preview_render","width":int(width),"height":int(height),"bounded":True}
