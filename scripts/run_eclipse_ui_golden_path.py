from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "ci-artifacts" / "eclipse" / "eclipse-ui-golden-path-report.json"


def run_eclipse_ui_golden_path(*, report_path: Path = DEFAULT_REPORT, require_eclipse: bool = False) -> dict[str, Any]:
    eclipse_binary = shutil.which("eclipse")
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
    if eclipse_binary:
        result = subprocess.run(
            [eclipse_binary, "-nosplash", "-application", "org.eclipse.equinox.p2.director", "-help"],
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
        "runtime_complete_claim_allowed": bool(eclipse_binary) and all(bool(item.get("ok")) for item in checks),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run optional installed Eclipse UI golden path evidence.")
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--require-eclipse", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    report = run_eclipse_ui_golden_path(report_path=out, require_eclipse=args.require_eclipse)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
