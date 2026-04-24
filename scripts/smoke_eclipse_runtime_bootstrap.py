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
    / "security"
    / "TokenRedaction.java",
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
    if "mapStatusToState" not in api_client_source or "isRetriable" not in api_client_source:
        return False, "client_core_degraded_state_mapping_missing"

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
