from __future__ import annotations

from pathlib import Path

from scripts.run_freecad_install_smoke import evaluate_install_smoke, write_report


def test_freecad_install_smoke_skips_without_local_binary(tmp_path: Path) -> None:
    report = evaluate_install_smoke(root=Path.cwd(), binary=None)
    assert report["status"] == "skipped"
    assert report["reason"] == "freecad_binary_not_found"

    out_path = tmp_path / "freecad-install-smoke.json"
    write_report(report, report_path=out_path)
    assert out_path.exists()
