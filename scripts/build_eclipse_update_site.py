from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "client_surfaces" / "eclipse_runtime" / "ananta_eclipse_plugin"
DEFAULT_OUT = ROOT / "ci-artifacts" / "eclipse" / "ananta-eclipse-update-site"


def build_update_site(
    *,
    out_dir: Path = DEFAULT_OUT,
    build_plugin: bool = True,
    bundle_path: Path | None = None,
) -> dict[str, Any]:
    if build_plugin:
        result = subprocess.run(
            [sys.executable, "scripts/build_eclipse_runtime_plugin.py", "--mode", "build"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {
                "schema": "eclipse_update_site_report_v1",
                "ok": False,
                "reason": "plugin_build_failed",
                "output": (result.stdout + "\n" + result.stderr).strip(),
            }

    bundle = bundle_path or PLUGIN_ROOT / "build" / "distributions" / "ananta-eclipse-plugin-0.1.0-bootstrap.zip"
    if not bundle.exists():
        return {
            "schema": "eclipse_update_site_report_v1",
            "ok": False,
            "reason": "bundle_missing",
            "bundle": str(bundle.relative_to(ROOT)),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = out_dir / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    copied_bundle = plugins_dir / bundle.name
    shutil.copy2(bundle, copied_bundle)
    site = {
        "schema": "eclipse_update_site_manifest_v1",
        "plugin_id": "io.ananta.eclipse.runtime",
        "version": "0.1.0-bootstrap",
        "artifacts": [str(copied_bundle.relative_to(ROOT) if copied_bundle.is_relative_to(ROOT) else copied_bundle)],
        "scope": "headless_bootstrap_update_site",
        "install_evidence_required_for_runtime_complete": True,
    }
    (out_dir / "site.json").write_text(json.dumps(site, indent=2) + "\n", encoding="utf-8")
    return {
        "schema": "eclipse_update_site_report_v1",
        "ok": True,
        "update_site": str(out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir),
        "bundle": str(copied_bundle.relative_to(ROOT) if copied_bundle.is_relative_to(ROOT) else copied_bundle),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic Eclipse update-site style artifact.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--skip-plugin-build", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    report = build_update_site(out_dir=out_dir, build_plugin=not args.skip_plugin_build)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
