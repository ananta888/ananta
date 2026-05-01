from __future__ import annotations

from scripts.run_blender_background_e2e import run_background_e2e


def test_run_blender_background_e2e_skips_when_binary_missing() -> None:
    report = run_background_e2e(require_blender=False)

    assert report["schema"] == "blender_background_e2e_report_v1"
    assert "install_smoke" in report
    assert report["ok"] is True
