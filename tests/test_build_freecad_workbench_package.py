from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from scripts.build_freecad_workbench_package import build_freecad_package

ROOT = Path(__file__).resolve().parents[1]


def test_build_freecad_package_creates_installable_zip(tmp_path: Path) -> None:
    out_path = tmp_path / "freecad-addon.zip"
    result = build_freecad_package(out_path=out_path)

    assert result["ok"] is True
    assert out_path.exists()
    with ZipFile(out_path) as archive:
        names = set(archive.namelist())
    assert "package.xml" in names
    assert "client_surfaces/freecad/workbench/Init.py" in names
    assert "client_surfaces/freecad/workbench/InitGui.py" in names
    assert "client_surfaces/freecad/bridge/ananta_freecad_bridge.py" in names
