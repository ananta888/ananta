from __future__ import annotations

from pathlib import Path

from scripts.build_eclipse_update_site import build_update_site
from scripts.run_eclipse_ui_golden_path import run_eclipse_ui_golden_path


def test_eclipse_update_site_builder_creates_p2_layout_without_publisher(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "dist"
    bundle_dir.mkdir()
    bundle = bundle_dir / "ananta-eclipse-plugin-runtime-0.1.0-bootstrap.jar"
    bundle.write_bytes(b"test-bundle")

    report = build_update_site(
        out_dir=tmp_path / "site",
        build_plugin=False,
        bundle_path=bundle,
        publish_p2=False,
    )

    assert report["ok"] is True
    assert (tmp_path / "site" / "site.json").exists()
    assert (tmp_path / "site" / "plugins" / "io.ananta.eclipse.runtime_0.1.0.qualifier.jar").exists()
    assert (tmp_path / "site" / "features" / "io.ananta.eclipse.runtime.feature_0.1.0.qualifier.jar").exists()
    assert (tmp_path / "site" / "category.xml").exists()


def test_eclipse_ui_golden_path_reports_skip_without_binary(tmp_path: Path) -> None:
    report = run_eclipse_ui_golden_path(report_path=tmp_path / "ui-report.json", require_eclipse=False)

    assert report["ok"] is True
    assert "runtime_complete_claim_allowed" in report
    assert (tmp_path / "ui-report.json").exists()


def test_eclipse_ui_golden_path_reports_skip_without_docker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.run_eclipse_ui_golden_path.shutil.which", lambda name: None)

    report = run_eclipse_ui_golden_path(
        report_path=tmp_path / "docker-ui-report.json",
        require_eclipse=False,
        use_docker=True,
    )

    assert report["ok"] is True
    assert report["skipped"] is True
    assert report["skip_reason"] == "docker_missing"
    assert report["runtime_complete_claim_allowed"] is False
