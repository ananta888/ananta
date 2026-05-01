from __future__ import annotations

from pathlib import Path

from scripts.build_blender_addon_package import build_package
from scripts.run_blender_install_smoke import run_install_smoke


def test_run_blender_install_smoke_without_required_binary(tmp_path: Path) -> None:
    package_path = tmp_path / "addon.zip"
    build_package(out_path=package_path)

    report = run_install_smoke(package_path=package_path, require_blender=False)

    assert report["schema"] == "blender_install_smoke_report_v1"
    assert report["ok"] is True
    assert any(item["check_id"] == "package_contains_addon_init" for item in report["checks"])
