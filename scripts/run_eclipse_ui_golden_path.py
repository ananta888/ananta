from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "ci-artifacts" / "eclipse" / "eclipse-ui-golden-path-report.json"
PLUGIN_ROOT = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"
DEFAULT_PLUGIN_JAR = PLUGIN_ROOT / "build" / "libs" / "ananta-eclipse-plugin-runtime-0.1.0-bootstrap.jar"
DEFAULT_UPDATE_SITE = ROOT / "ci-artifacts" / "eclipse" / "ananta-eclipse-update-site"
DEFAULT_DOCKER_IMAGE = "ananta/eclipse-ui-e2e:local"
DOCKERFILE = ROOT / "docker" / "eclipse-ui-e2e" / "Dockerfile"


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_report(report_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _build_plugin() -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "scripts/build_eclipse_runtime_plugin.py", "--mode", "build"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, (result.stdout + "\n" + result.stderr).strip()


def _docker_env() -> dict[str, str]:
    env = dict(os.environ)
    if env.get("ANANTA_DOCKER_CLEAN_PATH") == "1":
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env.setdefault("DOCKER_CONFIG", "/tmp/ananta-docker-config")
    return env


def _docker_command(
    *,
    image: str,
    plugin_jar: Path,
    update_site: Path | None,
    report_path: Path,
    timeout_seconds: int,
) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ROOT}:/workspace",
        "-w",
        "/workspace",
        image,
        "--plugin-jar",
        f"/workspace/{plugin_jar.resolve().relative_to(ROOT.resolve()).as_posix()}",
        "--report",
        f"/workspace/{report_path.resolve().relative_to(ROOT.resolve()).as_posix()}",
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if update_site is not None:
        command.extend([
            "--update-site",
            f"/workspace/{update_site.resolve().relative_to(ROOT.resolve()).as_posix()}",
        ])
    return command


def _run_docker_golden_path(
    *,
    report_path: Path,
    require_eclipse: bool,
    plugin_jar: Path,
    docker_image: str,
    build_docker_image: bool,
    build_plugin: bool,
    update_site: Path | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {"check_id": "docker_available", "ok": bool(shutil.which("docker")) or not require_eclipse},
        {"check_id": "dockerfile_present", "ok": DOCKERFILE.exists(), "dockerfile": _relative_or_absolute(DOCKERFILE)},
    ]
    if not shutil.which("docker"):
        checks.append({
            "check_id": "docker_eclipse_ui_golden_path",
            "ok": not require_eclipse,
            "skipped": True,
            "reason": "docker_missing",
        })
        return _write_report(report_path, {
            "schema": "eclipse_ui_golden_path_report_v1",
            "environment": "docker_xvfb_eclipse",
            "ok": all(bool(item.get("ok")) for item in checks),
            "skipped": not require_eclipse,
            "skip_reason": "docker_missing",
            "checks": checks,
            "runtime_complete_claim_allowed": False,
        })

    if build_plugin:
        ok, output = _build_plugin()
        checks.append({"check_id": "plugin_build", "ok": ok, "output_tail": output[-4000:]})
        if not ok:
            return _write_report(report_path, {
                "schema": "eclipse_ui_golden_path_report_v1",
                "environment": "docker_xvfb_eclipse",
                "ok": False,
                "skipped": False,
                "skip_reason": "",
                "checks": checks,
                "runtime_complete_claim_allowed": False,
            })

    checks.append({"check_id": "plugin_jar_present", "ok": plugin_jar.exists(), "plugin_jar": _relative_or_absolute(plugin_jar)})
    if not plugin_jar.exists():
        return _write_report(report_path, {
            "schema": "eclipse_ui_golden_path_report_v1",
            "environment": "docker_xvfb_eclipse",
            "ok": False,
            "skipped": False,
            "skip_reason": "",
            "checks": checks,
            "runtime_complete_claim_allowed": False,
        })

    if update_site is not None:
        checks.append({"check_id": "p2_update_site_present", "ok": update_site.exists(), "update_site": _relative_or_absolute(update_site)})
        for metadata_name in ("content.jar", "artifacts.jar"):
            checks.append({
                "check_id": f"p2_{metadata_name}_present",
                "ok": (update_site / metadata_name).exists(),
                "path": _relative_or_absolute(update_site / metadata_name),
            })
        if not all(bool(item.get("ok")) for item in checks):
            return _write_report(report_path, {
                "schema": "eclipse_ui_golden_path_report_v1",
                "environment": "docker_xvfb_eclipse_jee_2026_03",
                "ok": False,
                "skipped": False,
                "skip_reason": "",
                "checks": checks,
                "runtime_complete_claim_allowed": False,
            })

    if build_docker_image:
        build_result = subprocess.run(
            ["docker", "build", "-f", str(DOCKERFILE), "-t", docker_image, str(ROOT)],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
            env=_docker_env(),
        )
        checks.append({
            "check_id": "docker_image_build",
            "ok": build_result.returncode == 0,
            "image": docker_image,
            "output_tail": (build_result.stdout + "\n" + build_result.stderr).strip()[-4000:],
        })
        if build_result.returncode != 0:
            return _write_report(report_path, {
                "schema": "eclipse_ui_golden_path_report_v1",
                "environment": "docker_xvfb_eclipse",
                "ok": False,
                "skipped": False,
                "skip_reason": "",
                "checks": checks,
                "runtime_complete_claim_allowed": False,
            })

    report_path.unlink(missing_ok=True)
    report_path.with_name("eclipse-ui-availability-report.json").unlink(missing_ok=True)
    run_result = subprocess.run(
        _docker_command(
            image=docker_image,
            plugin_jar=plugin_jar,
            update_site=update_site,
            report_path=report_path,
            timeout_seconds=timeout_seconds,
        ),
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 60,
        env=_docker_env(),
    )
    checks.append({
        "check_id": "docker_container_golden_path",
        "ok": run_result.returncode == 0,
        "image": docker_image,
        "output_tail": (run_result.stdout + "\n" + run_result.stderr).strip()[-4000:],
    })

    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report.setdefault("host_checks", checks)
        expected_container_checks = {str(item.get("check_id")) for item in list(report.get("checks") or [])}
        if update_site is not None and not {"p2_install_from_update_site", "ui_availability_verifier"}.issubset(expected_container_checks):
            checks.append({
                "check_id": "fresh_p2_ui_report",
                "ok": False,
                "reason": "container report did not include p2 install and UI availability checks",
            })
        report["runtime_complete_claim_allowed"] = bool(report.get("runtime_complete_claim_allowed")) and all(
            bool(item.get("ok")) for item in checks
        )
        report["ok"] = bool(report.get("ok")) and all(bool(item.get("ok")) for item in checks)
        return _write_report(report_path, report)

    return _write_report(report_path, {
        "schema": "eclipse_ui_golden_path_report_v1",
        "environment": "docker_xvfb_eclipse",
        "ok": all(bool(item.get("ok")) for item in checks),
        "skipped": False,
        "skip_reason": "",
        "checks": checks,
        "runtime_complete_claim_allowed": False,
    })


def run_eclipse_ui_golden_path(
    *,
    report_path: Path = DEFAULT_REPORT,
    require_eclipse: bool = False,
    eclipse_binary: Path | None = None,
    use_docker: bool = False,
    docker_image: str = DEFAULT_DOCKER_IMAGE,
    build_docker_image: bool = True,
    build_plugin: bool = False,
    plugin_jar: Path = DEFAULT_PLUGIN_JAR,
    update_site: Path | None = DEFAULT_UPDATE_SITE,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    if use_docker:
        return _run_docker_golden_path(
            report_path=report_path,
            require_eclipse=require_eclipse,
            plugin_jar=plugin_jar,
            docker_image=docker_image,
            build_docker_image=build_docker_image,
            build_plugin=build_plugin,
            update_site=update_site,
            timeout_seconds=timeout_seconds,
        )

    resolved_eclipse_binary = str(eclipse_binary) if eclipse_binary else shutil.which("eclipse")
    checks: list[dict[str, Any]] = [
        {
            "check_id": "plugin_metadata_present",
            "ok": (ROOT / "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml").exists(),
        },
        {
            "check_id": "viewpart_adapters_present",
            "ok": (ROOT / "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/views/eclipse/AbstractAnantaRuntimeViewPart.java").exists(),
        },
    ]
    if resolved_eclipse_binary:
        result = subprocess.run(
            [resolved_eclipse_binary, "-nosplash", "-application", "org.eclipse.equinox.p2.director", "-help"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        checks.append({"check_id": "installed_eclipse_launch", "ok": result.returncode in {0, 13}, "returncode": result.returncode})
        skipped = False
    else:
        checks.append({
            "check_id": "installed_eclipse_launch",
            "ok": not require_eclipse,
            "skipped": True,
            "reason": "eclipse_binary_missing",
        })
        skipped = True

    report = {
        "schema": "eclipse_ui_golden_path_report_v1",
        "ok": all(bool(item.get("ok")) for item in checks),
        "skipped": skipped,
        "skip_reason": "eclipse_binary_missing" if skipped else "",
        "checks": checks,
        "runtime_complete_claim_allowed": bool(resolved_eclipse_binary) and all(bool(item.get("ok")) for item in checks),
    }
    return _write_report(report_path, report)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run optional installed Eclipse UI golden path evidence.")
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--require-eclipse", action="store_true")
    parser.add_argument("--eclipse-binary")
    parser.add_argument("--docker", action="store_true", help="Run the installed Eclipse evidence in a Docker/Xvfb container.")
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--skip-docker-build", action="store_true")
    parser.add_argument("--build-plugin", action="store_true")
    parser.add_argument("--plugin-jar", default=str(DEFAULT_PLUGIN_JAR))
    parser.add_argument("--update-site", default=str(DEFAULT_UPDATE_SITE))
    parser.add_argument("--dropins", action="store_true", help="Use dropins instead of p2 update-site installation.")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    plugin_jar = Path(args.plugin_jar)
    if not plugin_jar.is_absolute():
        plugin_jar = ROOT / plugin_jar
    update_site = None
    if not args.dropins:
        update_site = Path(args.update_site)
        if not update_site.is_absolute():
            update_site = ROOT / update_site
    eclipse_binary = Path(args.eclipse_binary) if args.eclipse_binary else None
    report = run_eclipse_ui_golden_path(
        report_path=out,
        require_eclipse=args.require_eclipse,
        eclipse_binary=eclipse_binary,
        use_docker=args.docker,
        docker_image=args.docker_image,
        build_docker_image=not args.skip_docker_build,
        build_plugin=args.build_plugin,
        plugin_jar=plugin_jar,
        update_site=update_site,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
