from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"

REQUIRED_PATHS = [
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
    / "views"
    / "eclipse"
    / "AbstractAnantaRuntimeViewPart.java",
    PLUGIN_ROOT
    / "src"
    / "test"
    / "java"
    / "io"
    / "ananta"
    / "eclipse"
    / "runtime"
    / "product"
    / "EclipseProductRuntimeModelTest.java",
    ROOT / "scripts" / "smoke_eclipse_runtime_bootstrap.py",
    ROOT / "scripts" / "build_eclipse_runtime_plugin.py",
    ROOT / ".github" / "workflows" / "quality-and-docs.yml",
]


def _run(command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    output = f"{result.stdout}\n{result.stderr}".strip()
    return result.returncode == 0, output


def run_headless_smoke_once() -> tuple[bool, str]:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_PATHS if not path.exists()]
    if missing:
        return False, f"missing_runtime_hardening_artifacts={missing}"

    quality_workflow = (ROOT / ".github" / "workflows" / "quality-and-docs.yml").read_text(encoding="utf-8")
    if "eclipse-runtime-headless" not in quality_workflow:
        return False, "missing_ci_lane=eclipse-runtime-headless"
    if "python3 scripts/smoke_eclipse_runtime_headless.py" not in quality_workflow:
        return False, "missing_ci_smoke_command"

    bootstrap_ok, bootstrap_output = _run([sys.executable, "scripts/smoke_eclipse_runtime_bootstrap.py"])
    if not bootstrap_ok:
        return False, f"bootstrap_smoke_failed\n{bootstrap_output}"

    runtime_tests_ok, runtime_tests_output = _run(
        [sys.executable, "scripts/build_eclipse_runtime_plugin.py", "--mode", "test"]
    )
    if not runtime_tests_ok:
        return False, f"runtime_headless_tests_failed\n{runtime_tests_output}"

    audit_ok, audit_output = _run(
        [
            sys.executable,
            "scripts/audit_client_surface_entrypoints.py",
            "--todo",
            "todo.json",
            "--fail-on-warning",
        ]
    )
    if not audit_ok:
        return False, f"audit_failed\n{audit_output}"

    return (
        True,
        f"eclipse-runtime-headless-smoke-ok\n{bootstrap_output}\n{runtime_tests_output}\n{audit_output}",
    )


def main() -> int:
    ok, output = run_headless_smoke_once()
    if ok:
        print(output)
        return 0
    print("eclipse-runtime-headless-smoke-failed")
    print(output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
