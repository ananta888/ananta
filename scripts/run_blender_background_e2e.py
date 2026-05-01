from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from scripts.build_blender_addon_package import DEFAULT_OUT, ROOT
from scripts.run_blender_install_smoke import run_install_smoke


def run_background_e2e(*, require_blender: bool = False) -> dict[str, Any]:
    blender_binary = shutil.which("blender")
    smoke = run_install_smoke(package_path=DEFAULT_OUT, require_blender=require_blender)
    return {
        "schema": "blender_background_e2e_report_v1",
        "ok": bool(smoke.get("ok")) and (bool(blender_binary) or not require_blender),
        "skipped": not bool(blender_binary),
        "skip_reason": "" if blender_binary else "blender_binary_missing",
        "install_smoke": smoke,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run optional real Blender background E2E evidence.")
    parser.add_argument("--require-blender", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    report = run_background_e2e(require_blender=args.require_blender)
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
