from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"
REQUIRED_PATHS = [
    PLUGIN_ROOT / "plugin.xml",
    PLUGIN_ROOT / "build.gradle",
    PLUGIN_ROOT / "settings.gradle",
    PLUGIN_ROOT / "build.properties",
    PLUGIN_ROOT / "META-INF" / "MANIFEST.MF",
    PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "core" / "AnantaApiClient.java",
    PLUGIN_ROOT / "src" / "main" / "java" / "io" / "ananta" / "eclipse" / "runtime" / "core" / "CapabilityGate.java",
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
    / "views"
    / "eclipse"
    / "AbstractAnantaRuntimeViewPart.java",
    PLUGIN_ROOT
    / "src"
    / "main"
    / "java"
    / "io"
    / "ananta"
    / "eclipse"
    / "runtime"
    / "chat"
    / "ChatRuntimeModel.java",
    PLUGIN_ROOT
    / "src"
    / "main"
    / "java"
    / "io"
    / "ananta"
    / "eclipse"
    / "runtime"
    / "patch"
    / "ApprovalGatedPatchApplier.java",
    PLUGIN_ROOT
    / "src"
    / "main"
    / "java"
    / "io"
    / "ananta"
    / "eclipse"
    / "runtime"
    / "project"
    / "NewAnantaProjectWizard.java",
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
    ROOT / "scripts" / "smoke_eclipse_runtime_headless.py",
]


def run_smoke_once() -> tuple[bool, str]:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_PATHS if not path.exists()]
    if missing:
        return False, f"missing_runtime_files={missing}"

    plugin_xml = (PLUGIN_ROOT / "plugin.xml").read_text(encoding="utf-8")
    required_commands = [
        "io.ananta.eclipse.command.analyze",
        "io.ananta.eclipse.command.review",
        "io.ananta.eclipse.command.patch",
        "io.ananta.eclipse.command.new_project",
        "io.ananta.eclipse.command.evolve_project",
    ]
    missing_commands = [command for command in required_commands if command not in plugin_xml]
    if missing_commands:
        return False, f"missing_plugin_commands={missing_commands}"
    required_handlers = [
        "io.ananta.eclipse.runtime.commands.handlers.AnalyzeCommandHandler",
        "io.ananta.eclipse.runtime.commands.handlers.ReviewCommandHandler",
        "io.ananta.eclipse.runtime.commands.handlers.PatchCommandHandler",
        "io.ananta.eclipse.runtime.commands.handlers.NewProjectCommandHandler",
        "io.ananta.eclipse.runtime.commands.handlers.EvolveProjectCommandHandler",
    ]
    missing_handlers = [handler for handler in required_handlers if handler not in plugin_xml]
    if missing_handlers:
        return False, f"missing_plugin_handlers={missing_handlers}"
    required_view_classes = [
        "io.ananta.eclipse.runtime.views.eclipse.AnantaGoalViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaTaskListViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaTaskDetailViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaArtifactViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaApprovalQueueViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaAuditViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaRepairViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaTuiStatusViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaPolicyFallbackViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaChatViewPart",
        "io.ananta.eclipse.runtime.views.eclipse.AnantaStatusViewPart",
    ]
    missing_view_classes = [view_class for view_class in required_view_classes if view_class not in plugin_xml]
    if missing_view_classes:
        return False, f"missing_eclipse_viewpart_classes={missing_view_classes}"
    required_views = [
        "io.ananta.eclipse.view.goal",
        "io.ananta.eclipse.view.task_list",
        "io.ananta.eclipse.view.task_detail",
        "io.ananta.eclipse.view.artifact",
        "io.ananta.eclipse.view.approval_queue",
        "io.ananta.eclipse.view.audit",
        "io.ananta.eclipse.view.repair",
        "io.ananta.eclipse.view.tui_status",
        "io.ananta.eclipse.view.policy_fallback",
    ]
    missing_views = [view for view in required_views if view not in plugin_xml]
    if missing_views:
        return False, f"missing_plugin_views={missing_views}"

    api_client_source = (
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
    required_client_methods = [
        "mapStatusToState",
        "isRetriable",
        "analyzeContext",
        "reviewContext",
        "patchPlan",
        "createProjectNew",
        "createProjectEvolve",
        "approveApproval",
        "listAuditEvents(String severity, String eventType, String objectId)",
    ]
    missing_client_methods = [method for method in required_client_methods if method not in api_client_source]
    if missing_client_methods:
        return False, f"client_core_methods_missing={missing_client_methods}"

    command_registry_source = (
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
    command_registry_requirements = [
        "capabilityGate.evaluate",
        "RuntimeCommandType.ANALYZE.commandId()",
        "RuntimeCommandType.REVIEW.commandId()",
        "RuntimeCommandType.PATCH.commandId()",
        "RuntimeCommandType.NEW_PROJECT.commandId()",
        "RuntimeCommandType.EVOLVE_PROJECT.commandId()",
        "submitGoalFromPanel",
    ]
    missing_registry_requirements = [
        requirement for requirement in command_registry_requirements if requirement not in command_registry_source
    ]
    if missing_registry_requirements:
        return False, f"command_registry_requirements_missing={missing_registry_requirements}"

    views_registry_source = (
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
    views_registry_requirements = [
        "io.ananta.eclipse.view.goal",
        "io.ananta.eclipse.view.task_list",
        "io.ananta.eclipse.view.task_detail",
        "io.ananta.eclipse.view.artifact",
        "io.ananta.eclipse.view.approval_queue",
        "io.ananta.eclipse.view.audit",
        "io.ananta.eclipse.view.repair",
        "io.ananta.eclipse.view.tui_status",
        "io.ananta.eclipse.view.policy_fallback",
    ]
    missing_views_registry_requirements = [
        requirement for requirement in views_registry_requirements if requirement not in views_registry_source
    ]
    if missing_views_registry_requirements:
        return False, f"views_registry_requirements_missing={missing_views_registry_requirements}"

    context_capture_source = (
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
    context_capture_requirements = [
        "DEFAULT_MAX_SELECTION_CHARS",
        "DEFAULT_MAX_PATHS",
        "userReviewRequiredBeforeSend",
        "implicit_unrelated_paths_included",
    ]
    missing_context_capture_requirements = [
        requirement for requirement in context_capture_requirements if requirement not in context_capture_source
    ]
    if missing_context_capture_requirements:
        return False, f"context_capture_requirements_missing={missing_context_capture_requirements}"

    if "mapStatusToState" not in api_client_source or "isRetriable" not in api_client_source:
        return False, "client_core_degraded_state_mapping_missing"
    if 'body.append("{\\"goal\\":\\"")' not in api_client_source or '"/tasks/analyze"' in api_client_source:
        return False, "client_core_hub_goal_contract_mismatch"
    handler_source = (
        PLUGIN_ROOT
        / "src"
        / "main"
        / "java"
        / "io"
        / "ananta"
        / "eclipse"
        / "runtime"
        / "commands"
        / "handlers"
        / "AnalyzeCommandHandler.java"
    ).read_text(encoding="utf-8")
    view_part_source = (
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
    if "extends AbstractHandler" not in handler_source:
        return False, "eclipse_command_handler_adapter_missing"
    if "extends ViewPart" not in view_part_source:
        return False, "eclipse_viewpart_adapter_missing"

    validate_result = subprocess.run(
        [sys.executable, "scripts/build_eclipse_runtime_plugin.py", "--mode", "validate"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    validate_output = f"{validate_result.stdout}\n{validate_result.stderr}".strip()
    if validate_result.returncode != 0:
        return False, validate_output

    return True, f"eclipse-runtime-bootstrap-smoke-ok\n{validate_output}"


def main() -> int:
    ok, output = run_smoke_once()
    if ok:
        print(output)
        return 0
    print("eclipse-runtime-bootstrap-smoke-failed")
    print(output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
