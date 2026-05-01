from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from scripts.build_blender_addon_package import DEFAULT_OUT, ROOT, build_package


def run_install_smoke(*, package_path: Path = DEFAULT_OUT, require_blender: bool = False) -> dict[str, Any]:
    if not package_path.is_absolute():
        package_path = ROOT / package_path
    if not package_path.exists():
        build_package(out_path=package_path)
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        with zipfile.ZipFile(package_path) as archive:
            archive.extractall(target)
        addon_init = target / "client_surfaces/blender/addon/__init__.py"
        checks.append({"check_id": "package_contains_addon_init", "ok": addon_init.exists()})
    blender_binary = shutil.which("blender")
    if blender_binary:
        cmd = [blender_binary, "--background", "--python-expr", "import sys; print('ananta-blender-smoke')"]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
        checks.append({"check_id": "blender_background_available", "ok": result.returncode == 0, "returncode": result.returncode})
    else:
        checks.append({"check_id": "blender_background_available", "ok": not require_blender, "skipped": True, "reason": "blender_binary_missing"})
    return {
        "schema": "blender_install_smoke_report_v1",
        "ok": all(bool(item.get("ok")) for item in checks),
        "package_path": str(package_path.relative_to(ROOT) if package_path.is_relative_to(ROOT) else package_path),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Blender addon package install/load smoke.")
    parser.add_argument("--package", default=DEFAULT_OUT.as_posix())
    parser.add_argument("--require-blender", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    report = run_install_smoke(package_path=Path(args.package), require_blender=args.require_blender)
    print(json.dumps(report, indent=2))
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
