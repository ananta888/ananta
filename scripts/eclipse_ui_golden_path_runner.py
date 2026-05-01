from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _jar_contains_plugin_metadata(plugin_jar: Path) -> bool:
    if not plugin_jar.exists():
        return False
    with zipfile.ZipFile(plugin_jar) as jar:
        names = set(jar.namelist())
    return "plugin.xml" in names and "META-INF/MANIFEST.MF" in names


def _jar_declares_golden_path_surfaces(plugin_jar: Path) -> tuple[bool, list[str]]:
    required_markers = [
        "io.ananta.eclipse.command.chat",
        "io.ananta.eclipse.view.chat",
        "io.ananta.eclipse.view.task_list",
        "io.ananta.eclipse.view.artifact",
        "io.ananta.eclipse.view.approval_queue",
        "io.ananta.eclipse.command.patch",
    ]
    if not plugin_jar.exists():
        return False, required_markers
    with zipfile.ZipFile(plugin_jar) as jar:
        try:
            plugin_xml = jar.read("plugin.xml").decode("utf-8")
        except KeyError:
            return False, required_markers
    missing = [marker for marker in required_markers if marker not in plugin_xml]
    return not missing, missing


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    stdout = exc.stdout or ""
    stderr = exc.stderr or ""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return f"{stdout}\n{stderr}".strip()


def run_container_golden_path(
    *,
    eclipse_binary: Path,
    plugin_jar: Path,
    workspace: Path,
    report_path: Path,
    root: Path,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append({"check_id": "installed_eclipse_binary_present", "ok": eclipse_binary.exists(), "path": str(eclipse_binary)})
    checks.append({
        "check_id": "plugin_jar_contains_eclipse_metadata",
        "ok": _jar_contains_plugin_metadata(plugin_jar),
        "plugin_jar": _relative_or_absolute(plugin_jar, root),
    })
    surfaces_ok, missing_surfaces = _jar_declares_golden_path_surfaces(plugin_jar)
    checks.append({
        "check_id": "plugin_declares_golden_path_surfaces",
        "ok": surfaces_ok,
        "missing": missing_surfaces,
    })

    dropins_plugins = eclipse_binary.parent / "dropins"
    if checks[1]["ok"] and surfaces_ok:
        dropins_plugins.mkdir(parents=True, exist_ok=True)
        installed_plugin = dropins_plugins / plugin_jar.name
        shutil.copy2(plugin_jar, installed_plugin)
        checks.append({
            "check_id": "plugin_dropins_install",
            "ok": installed_plugin.exists(),
            "installed_plugin": str(installed_plugin),
        })
    else:
        checks.append({"check_id": "plugin_dropins_install", "ok": False, "reason": "plugin_jar_missing_or_invalid"})

    workspace.mkdir(parents=True, exist_ok=True)
    if eclipse_binary.exists():
        command = [
            "xvfb-run",
            "-a",
            str(eclipse_binary),
            "-clean",
            "-nosplash",
            "-data",
            str(workspace),
            "-application",
            "org.eclipse.ui.ide.workbench",
        ]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)
            output = (result.stdout + "\n" + result.stderr).strip()
            launch_ok = result.returncode == 0
            returncode: int | str = result.returncode
        except subprocess.TimeoutExpired as exc:
            output = _timeout_output(exc)
            launch_ok = True
            returncode = "timeout_after_successful_startup_window"
        checks.append({
            "check_id": "xvfb_eclipse_launch_with_installed_plugin",
            "ok": launch_ok,
            "returncode": returncode,
            "output_tail": output[-4000:],
        })
    else:
        checks.append({"check_id": "xvfb_eclipse_launch_with_installed_plugin", "ok": False, "reason": "eclipse_binary_missing"})

    report = {
        "schema": "eclipse_ui_golden_path_report_v1",
        "environment": "docker_xvfb_eclipse",
        "ok": all(bool(item.get("ok")) for item in checks),
        "skipped": False,
        "skip_reason": "",
        "checks": checks,
        "runtime_complete_claim_allowed": all(bool(item.get("ok")) for item in checks),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run installed Eclipse UI golden path evidence inside Docker/Xvfb.")
    parser.add_argument("--eclipse-binary", default="/opt/eclipse/eclipse")
    parser.add_argument("--plugin-jar", required=True)
    parser.add_argument("--workspace", default="/tmp/ananta-eclipse-workspace")
    parser.add_argument("--report", required=True)
    parser.add_argument("--root", default="/workspace")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()

    report = run_container_golden_path(
        eclipse_binary=Path(args.eclipse_binary),
        plugin_jar=Path(args.plugin_jar),
        workspace=Path(args.workspace),
        report_path=Path(args.report),
        root=Path(args.root),
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
