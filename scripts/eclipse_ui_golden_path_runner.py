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
    update_site: Path | None,
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

    eclipse_home = eclipse_binary.parent
    eclipse_product = _eclipse_product(eclipse_home)
    checks.append({"check_id": "eclipse_product_detected", "ok": bool(eclipse_product), "product": eclipse_product})

    if update_site is not None:
        install_report = _install_from_p2_site(
            eclipse_binary=eclipse_binary,
            update_site=update_site,
            timeout_seconds=timeout_seconds,
        )
        checks.append(install_report)
    else:
        dropins_plugins = eclipse_home / "dropins"
        dropins_plugins.mkdir(parents=True, exist_ok=True)
        installed_plugin = dropins_plugins / plugin_jar.name
        shutil.copy2(plugin_jar, installed_plugin)
        checks.append({
            "check_id": "plugin_dropins_install",
            "ok": installed_plugin.exists(),
            "installed_plugin": str(installed_plugin),
        })

    workspace.mkdir(parents=True, exist_ok=True)
    if eclipse_binary.exists():
        availability_report = report_path.with_name("eclipse-ui-availability-report.json")
        verifier = _run_ui_availability_verifier(
            eclipse_binary=eclipse_binary,
            workspace=workspace,
            availability_report=availability_report,
            timeout_seconds=timeout_seconds,
        )
        checks.append(verifier)
    else:
        checks.append({"check_id": "ui_availability_verifier", "ok": False, "reason": "eclipse_binary_missing"})

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


def _eclipse_product(eclipse_home: Path) -> dict[str, str]:
    product_file = eclipse_home / ".eclipseproduct"
    if not product_file.exists():
        return {}
    values: dict[str, str] = {}
    for line in product_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _detect_profile(eclipse_home: Path) -> str:
    profile_registry = eclipse_home / "p2" / "org.eclipse.equinox.p2.engine" / "profileRegistry"
    if profile_registry.exists():
        profiles = sorted(path.name.removesuffix(".profile") for path in profile_registry.glob("*.profile"))
        if profiles:
            return profiles[0]
    return "SDKProfile"


def _install_from_p2_site(*, eclipse_binary: Path, update_site: Path, timeout_seconds: int) -> dict[str, Any]:
    profile = _detect_profile(eclipse_binary.parent)
    command = [
        str(eclipse_binary),
        "-nosplash",
        "-application",
        "org.eclipse.equinox.p2.director",
        "-repository",
        update_site.as_uri(),
        "-installIU",
        "io.ananta.eclipse.runtime.feature.feature.group",
        "-destination",
        str(eclipse_binary.parent),
        "-profile",
        profile,
        "-profileProperties",
        "org.eclipse.update.install.features=true",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)
    output = (result.stdout + "\n" + result.stderr).strip()
    return {
        "check_id": "p2_install_from_update_site",
        "ok": result.returncode == 0,
        "profile": profile,
        "update_site": str(update_site),
        "returncode": result.returncode,
        "output_tail": output[-4000:],
    }


def _run_ui_availability_verifier(
    *,
    eclipse_binary: Path,
    workspace: Path,
    availability_report: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        "xvfb-run",
        "-a",
        str(eclipse_binary),
        "-consoleLog",
        "-nosplash",
        "-data",
        str(workspace),
        "-application",
        "org.eclipse.ui.ide.workbench",
        "-vmargs",
        f"-Dananta.e2e.report={availability_report}",
        "-Doomph.setup.skip=true",
        "-Doomph.setup.sync.skip=true",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)
    output = (result.stdout + "\n" + result.stderr).strip()
    verifier_report: dict[str, Any] = {}
    if availability_report.exists():
        verifier_report = json.loads(availability_report.read_text(encoding="utf-8"))
    return {
        "check_id": "ui_availability_verifier",
        "ok": result.returncode == 0 and bool(verifier_report.get("ok")),
        "returncode": result.returncode,
        "availability_report": str(availability_report),
        "verifier_report": verifier_report,
        "output_tail": output[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run installed Eclipse UI golden path evidence inside Docker/Xvfb.")
    parser.add_argument("--eclipse-binary", default="/opt/eclipse/eclipse")
    parser.add_argument("--plugin-jar", required=True)
    parser.add_argument("--update-site")
    parser.add_argument("--workspace", default="/tmp/ananta-eclipse-workspace")
    parser.add_argument("--report", required=True)
    parser.add_argument("--root", default="/workspace")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()

    report = run_container_golden_path(
        eclipse_binary=Path(args.eclipse_binary),
        plugin_jar=Path(args.plugin_jar),
        update_site=Path(args.update_site) if args.update_site else None,
        workspace=Path(args.workspace),
        report_path=Path(args.report),
        root=Path(args.root),
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
