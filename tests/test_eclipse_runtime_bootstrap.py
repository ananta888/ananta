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
        / "commands"
        / "EclipseCommandRegistry.java",
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "context"
        / "EclipseContextCaptureRuntime.java",
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "views"
        / "EclipseViewsExtensionRegistry.java",
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
    assert "io.ananta.eclipse.runtime.commands.handlers.AnalyzeCommandHandler" in plugin_xml
    assert "io.ananta.eclipse.runtime.commands.handlers.ReviewCommandHandler" in plugin_xml
    assert "io.ananta.eclipse.runtime.commands.handlers.PatchCommandHandler" in plugin_xml
    assert "io.ananta.eclipse.runtime.commands.handlers.NewProjectCommandHandler" in plugin_xml
    assert "io.ananta.eclipse.runtime.commands.handlers.EvolveProjectCommandHandler" in plugin_xml
    assert "io.ananta.eclipse.view.goal" in plugin_xml
    assert "io.ananta.eclipse.view.task_list" in plugin_xml
    assert "io.ananta.eclipse.view.task_detail" in plugin_xml
    assert "io.ananta.eclipse.view.artifact" in plugin_xml
    assert "io.ananta.eclipse.view.approval_queue" in plugin_xml
    assert "io.ananta.eclipse.view.audit" in plugin_xml
    assert "io.ananta.eclipse.view.repair" in plugin_xml
    assert "io.ananta.eclipse.view.tui_status" in plugin_xml
    assert "io.ananta.eclipse.view.policy_fallback" in plugin_xml


def test_runtime_operation_sources_cover_command_registry_context_and_views() -> None:
    command_registry = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "commands"
        / "EclipseCommandRegistry.java"
    ).read_text(encoding="utf-8")
    assert "capabilityGate.evaluate" in command_registry
    assert "RuntimeCommandType.ANALYZE.commandId()" in command_registry
    assert "RuntimeCommandType.REVIEW.commandId()" in command_registry
    assert "RuntimeCommandType.PATCH.commandId()" in command_registry
    assert "RuntimeCommandType.NEW_PROJECT.commandId()" in command_registry
    assert "RuntimeCommandType.EVOLVE_PROJECT.commandId()" in command_registry
    assert "submitGoalFromPanel" in command_registry

    context_capture = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "context"
        / "EclipseContextCaptureRuntime.java"
    ).read_text(encoding="utf-8")
    assert "DEFAULT_MAX_SELECTION_CHARS" in context_capture
    assert "DEFAULT_MAX_PATHS" in context_capture
    assert "userReviewRequiredBeforeSend" in context_capture
    assert "implicit_unrelated_paths_included" in context_capture

    views_registry = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "views"
        / "EclipseViewsExtensionRegistry.java"
    ).read_text(encoding="utf-8")
    assert "io.ananta.eclipse.view.goal" in views_registry
    assert "io.ananta.eclipse.view.task_list" in views_registry
    assert "io.ananta.eclipse.view.task_detail" in views_registry
    assert "io.ananta.eclipse.view.artifact" in views_registry
    assert "io.ananta.eclipse.view.approval_queue" in views_registry
    assert "io.ananta.eclipse.view.audit" in views_registry
    assert "io.ananta.eclipse.view.repair" in views_registry
    assert "io.ananta.eclipse.view.tui_status" in views_registry
    assert "io.ananta.eclipse.view.policy_fallback" in views_registry


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
