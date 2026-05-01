from __future__ import annotations

import json
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
        / "test"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "core"
        / "EclipseRuntimeUnitTest.java",
        PLUGIN_ROOT
        / "src"
        / "test"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "integration"
        / "EclipseRuntimeIntegrationUiTest.java",
        PLUGIN_ROOT
        / "src"
        / "test"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "security"
        / "EclipseRuntimeSecurityGovernanceTest.java",
        PLUGIN_ROOT
        / "src"
        / "test"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "core"
        / "EclipseRuntimeApiContractCompatibilityTest.java",
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
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "views"
        / "eclipse"
        / "AbstractAnantaRuntimeViewPart.java",
        PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "chat" / "ChatRuntimeModel.java",
        PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "patch" / "ApprovalGatedPatchApplier.java",
        PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "project" / "NewAnantaProjectWizard.java",
        PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "completion" / "AnantaCompletionProposalComputer.java",
        ROOT / "scripts" / "build_eclipse_runtime_plugin.py",
        ROOT / "scripts" / "smoke_eclipse_runtime_bootstrap.py",
        ROOT / "scripts" / "smoke_eclipse_runtime_headless.py",
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
    assert "io.ananta.eclipse.runtime.views.eclipse.AnantaGoalViewPart" in plugin_xml
    assert "io.ananta.eclipse.runtime.views.eclipse.AnantaTaskListViewPart" in plugin_xml
    assert "io.ananta.eclipse.runtime.views.eclipse.AnantaChatViewPart" in plugin_xml
    assert "io.ananta.eclipse.view.status" in plugin_xml
    assert "io.ananta.eclipse.perspective" in plugin_xml
    assert "io.ananta.eclipse.runtime.views.EclipseViewsExtensionRegistry" not in plugin_xml


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
    assert '"/tasks/analyze"' not in (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "core"
        / "AnantaApiClient.java"
    ).read_text(encoding="utf-8")

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
    view_part = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "views"
        / "eclipse"
        / "AbstractAnantaRuntimeViewPart.java"
    ).read_text(encoding="utf-8")
    assert "extends ViewPart" in view_part


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


def test_headless_smoke_and_ci_lane_artifacts_exist() -> None:
    headless_smoke = (ROOT / "scripts" / "smoke_eclipse_runtime_headless.py").read_text(encoding="utf-8")
    assert "run_headless_smoke_once" in headless_smoke
    assert "eclipse-runtime-headless-smoke-ok" in headless_smoke
    assert "scripts/build_eclipse_runtime_plugin.py" in headless_smoke
    assert '"--mode", "test"' in headless_smoke

    workflow = (ROOT / ".github" / "workflows" / "quality-and-docs.yml").read_text(encoding="utf-8")
    assert "eclipse-runtime-headless" in workflow
    assert "python3 scripts/smoke_eclipse_runtime_headless.py" in workflow


def test_eclipse_build_script_pins_java17_baseline_and_build_command() -> None:
    build_script = (ROOT / "scripts" / "build_eclipse_runtime_plugin.py").read_text(encoding="utf-8")
    assert 'DEFAULT_GRADLE_IMAGE = "gradle:8.10.2-jdk17"' in build_script
    assert 'parser.add_argument("--mode", choices=["validate", "build", "test"], default="validate")' in build_script
    assert 'gradle_tasks = ["clean", "build"] if args.mode == "build" else ["clean", "test"]' in build_script
    build_gradle = (PLUGIN_ROOT / "build.gradle").read_text(encoding="utf-8")
    assert "org.eclipse.platform:org.eclipse.core.commands" in build_gradle
    assert "org.eclipse.platform:org.eclipse.ui" in build_gradle


def test_eclipse_runtime_status_stays_mvp_until_p2_workbench_verifier_passes() -> None:
    status_payload = json.loads((ROOT / "data" / "client_surface_runtime_status.json").read_text(encoding="utf-8"))
    surface_status = dict(status_payload.get("surface_status") or {})
    assert surface_status.get("eclipse_plugin") == "runtime_mvp"
    assert surface_status.get("eclipse_views_extension") == "runtime_mvp"
    ui_report = json.loads((ROOT / "ci-artifacts" / "eclipse" / "eclipse-ui-golden-path-report.json").read_text(encoding="utf-8"))
    assert "p2_install_from_update_site" in {str(item.get("check_id")) for item in list(ui_report.get("checks") or [])}
    assert ui_report.get("skipped") is False
    assert ui_report.get("runtime_complete_claim_allowed") is False


def test_eclipse_command_handlers_route_via_api_client_only() -> None:
    analyze_review_patch = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "commands"
        / "AnalyzeReviewPatchRuntimeHandler.java"
    ).read_text(encoding="utf-8")
    project_runtime = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "commands"
        / "ProjectRuntimeHandler.java"
    ).read_text(encoding="utf-8")
    goal_panel = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "commands"
        / "GoalSubmissionRuntimePanel.java"
    ).read_text(encoding="utf-8")

    assert "apiClient.analyzeContext(" in analyze_review_patch
    assert "apiClient.reviewContext(" in analyze_review_patch
    assert "apiClient.patchPlan(" in analyze_review_patch
    assert "apiClient.createProjectNew(" in project_runtime
    assert "apiClient.createProjectEvolve(" in project_runtime
    assert "apiClient.submitGoal(" in goal_panel


def test_eclipse_runtime_smoke_checklist_covers_expected_results_and_failures() -> None:
    checklist = (ROOT / "docs" / "eclipse-runtime-smoke-checklist.md").read_text(encoding="utf-8")
    assert "python3 scripts/smoke_eclipse_runtime_bootstrap.py" in checklist
    assert "python3 scripts/smoke_eclipse_runtime_headless.py" in checklist
    assert "Expected result" in checklist
    assert "Known failure symptoms" in checklist
    assert "task and artifact references" in checklist
