from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = ROOT / "scripts" / "build_freecad_workbench_package.py"
DEFAULT_REPORT_PATH = ROOT / "ci-artifacts" / "domain-runtime" / "freecad-install-smoke-report.json"
FREECAD_BINARIES = ("freecadcmd", "FreeCADCmd", "freecad", "FreeCAD")


def detect_freecad_binary() -> str | None:
    for candidate in FREECAD_BINARIES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def evaluate_install_smoke(*, root: Path = ROOT, binary: str | None = None) -> dict[str, Any]:
    freecad_binary = binary or detect_freecad_binary()
    if not freecad_binary:
        return {
            "schema": "freecad_install_smoke_report_v1",
            "ok": False,
            "status": "skipped",
            "reason": "freecad_binary_not_found",
        }

    package_path = root / "ci-artifacts" / "domain-runtime" / "freecad-workbench-addon.zip"
    build = subprocess.run(
        [_python_executable(), str(PACKAGE_SCRIPT), "--out", str(package_path)],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        return {
            "schema": "freecad_install_smoke_report_v1",
            "ok": False,
            "status": "failed",
            "reason": "package_build_failed",
            "build_output": (build.stdout + "\n" + build.stderr).strip()[-2000:],
        }

    with tempfile.TemporaryDirectory(prefix="ananta-freecad-install-smoke-") as tmp_dir:
        temp_root = Path(tmp_dir)
        extract_root = temp_root / "extracted"
        with ZipFile(package_path) as archive:
            archive.extractall(extract_root)
        script_path = temp_root / "freecad_install_smoke.py"
        script_path.write_text(
            "from client_surfaces.freecad.workbench.InitGui import WORKBENCH\n"
            "print('workbench=' + WORKBENCH.GetClassName())\n"
            "print('commands=' + ','.join(WORKBENCH.Initialize()))\n",
            encoding="utf-8",
        )
        env = dict(**__import__("os").environ)
        env["PYTHONPATH"] = f"{extract_root}:{env.get('PYTHONPATH', '')}".rstrip(":")
        result = subprocess.run(
            [freecad_binary, str(script_path)],
            cwd=str(extract_root),
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    return {
        "schema": "freecad_install_smoke_report_v1",
        "ok": result.returncode == 0,
        "status": "passed" if result.returncode == 0 else "failed",
        "binary": freecad_binary,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
        "artifact": str(package_path.relative_to(root)),
    }


def write_report(report: dict[str, Any], *, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run advisory install smoke against a local FreeCAD installation.")
    parser.add_argument("--report-out", default=str(DEFAULT_REPORT_PATH), help="Output report path.")
    parser.add_argument("--binary", default="", help="Optional explicit FreeCAD binary path.")
    args = parser.parse_args()

    report_path = Path(args.report_out)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report = evaluate_install_smoke(root=ROOT, binary=args.binary or None)
    write_report(report, report_path=report_path)
    print(report.get("status"))
    print(report.get("reason") or report.get("binary") or "")
    return 0 if report.get("ok") else (0 if report.get("status") == "skipped" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
