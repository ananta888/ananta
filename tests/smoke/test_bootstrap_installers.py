from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_bootstrap_installers_exist_with_expected_headers() -> None:
    sh = ROOT / "scripts" / "install-ananta.sh"
    ps1 = ROOT / "scripts" / "install-ananta.ps1"
    assert sh.exists()
    assert ps1.exists()
    assert _read("scripts/install-ananta.sh").startswith("#!/usr/bin/env bash")


def test_bootstrap_installers_include_safety_checks_and_smoke() -> None:
    sh = _read("scripts/install-ananta.sh")
    ps1 = _read("scripts/install-ananta.ps1")

    assert "set -euo pipefail" in sh
    assert "git status --porcelain" in sh
    assert "pip install -e ." in sh
    assert "agent.cli.main --help" in sh
    assert "--allow-dirty" in sh

    assert "git status --porcelain" in ps1
    assert "-m venv" in ps1
    assert "pip install -e ." in ps1
    assert "agent.cli.main --help" in ps1
    assert "-AllowDirty" in ps1


def test_bootstrap_installers_do_not_auto_install_container_runtimes() -> None:
    sh = _read("scripts/install-ananta.sh").lower()
    ps1 = _read("scripts/install-ananta.ps1").lower()

    forbidden = ["apt install docker", "brew install docker", "choco install docker", "podman machine init"]
    for token in forbidden:
        assert token not in sh
        assert token not in ps1
