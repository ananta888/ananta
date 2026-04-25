from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

ADDON_PATH = Path("client_surfaces/blender/addon/__init__.py")
BRIDGE_PATH = Path("client_surfaces/blender/bridge/ananta_blender_bridge.py")
DEFAULT_REPORT_PATH = Path("ci-artifacts/domain-runtime/blender-smoke-report.json")


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module load failed for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate_blender_runtime(*, root: Path = ROOT) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    addon_file = root / ADDON_PATH
    bridge_file = root / BRIDGE_PATH

    for rel_path in (ADDON_PATH, BRIDGE_PATH):
        exists = (root / rel_path).exists()
        checks.append(
            {
                "check_id": f"file_exists:{rel_path.as_posix()}",
                "ok": exists,
                "detail": "found" if exists else "missing",
            }
        )

    if addon_file.exists():
        addon_module = _load_module("ananta_blender_addon", addon_file)
        has_bl_info = isinstance(getattr(addon_module, "bl_info", None), dict)
        has_register = callable(getattr(addon_module, "register", None))
        has_unregister = callable(getattr(addon_module, "unregister", None))
        checks.append(
            {
                "check_id": "addon_contract",
                "ok": has_bl_info and has_register and has_unregister,
                "detail": {
                    "has_bl_info": has_bl_info,
                    "has_register": has_register,
                    "has_unregister": has_unregister,
                },
            }
        )

    if bridge_file.exists():
        bridge_module = _load_module("ananta_blender_bridge", bridge_file)
        envelope = bridge_module.build_bridge_envelope(
            capability_id="blender.scene.read",
            action_id="read_scene",
            payload={"mode": "smoke"},
            session_id="smoke-test",
        )
        bridge_errors = bridge_module.validate_bridge_envelope(envelope)
        checks.append(
            {
                "check_id": "bridge_envelope_contract",
                "ok": not bridge_errors,
                "detail": {"errors": bridge_errors},
            }
        )

    ok = all(bool(item.get("ok")) for item in checks)
    return {
        "schema": "blender_runtime_smoke_report_v1",
        "domain_id": "blender",
        "ok": ok,
        "checks": checks,
        "required_runtime_files": [ADDON_PATH.as_posix(), BRIDGE_PATH.as_posix()],
    }


def write_report(report: dict[str, Any], *, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Blender runtime smoke checks.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root path.")
    parser.add_argument(
        "--report-out",
        default=DEFAULT_REPORT_PATH.as_posix(),
        help="Path for the smoke report JSON (relative paths use --root).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(args.root).resolve()
    report_path = Path(args.report_out)
    if not report_path.is_absolute():
        report_path = root / report_path
    report = evaluate_blender_runtime(root=root)
    write_report(report, report_path=report_path)
    for check in list(report.get("checks") or []):
        state = "OK" if check.get("ok") else "FAIL"
        print(f"[{state}] {check.get('check_id')}")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
