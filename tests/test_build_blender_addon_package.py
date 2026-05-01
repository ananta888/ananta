from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.build_blender_addon_package import build_package


def test_build_blender_addon_package(tmp_path: Path) -> None:
    out = tmp_path / "ananta-blender-addon.zip"
    report = build_package(out_path=out)

    assert report["ok"] is True
    assert out.exists()
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
    assert "client_surfaces/blender/addon/__init__.py" in names
    assert "client_surfaces/blender/bridge/ananta_blender_bridge.py" in names
    assert not any("__pycache__" in name for name in names)
