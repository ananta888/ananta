from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"


def test_eclipse_runtime_bootstrap_files_exist() -> None:
    required_files = [
        PLUGIN_ROOT / "settings.gradle",
        PLUGIN_ROOT / "build.gradle",
        PLUGIN_ROOT / "plugin.xml",
        PLUGIN_ROOT / "build.properties",
        PLUGIN_ROOT / "META-INF" / "MANIFEST.MF",
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "core"
        / "AnantaApiClient.java",
        PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "core" / "ClientProfile.java",
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "core"
        / "CapabilityGate.java",
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "security"
        / "TokenRedaction.java",
        ROOT / "scripts" / "build_eclipse_runtime_plugin.py",
        ROOT / "scripts" / "smoke_eclipse_runtime_bootstrap.py",
    ]
    for file_path in required_files:
        assert file_path.exists(), f"missing bootstrap runtime artifact: {file_path}"


def test_eclipse_plugin_metadata_registers_core_commands() -> None:
    plugin_xml = (PLUGIN_ROOT / "plugin.xml").read_text(encoding="utf-8")
    assert "io.ananta.eclipse.command.analyze" in plugin_xml
    assert "io.ananta.eclipse.command.review" in plugin_xml
    assert "io.ananta.eclipse.command.patch" in plugin_xml
    assert "io.ananta.eclipse.command.new_project" in plugin_xml
    assert "io.ananta.eclipse.command.evolve_project" in plugin_xml


def test_build_script_validate_mode_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_eclipse_runtime_plugin.py", "--mode", "validate"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "eclipse-runtime-build-validate-ok" in result.stdout


def test_smoke_script_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/smoke_eclipse_runtime_bootstrap.py"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "eclipse-runtime-bootstrap-smoke-ok" in result.stdout
