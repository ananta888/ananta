from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WORKBENCH_INITGUI_PATH = Path("client_surfaces/freecad/workbench/InitGui.py")
BRIDGE_PATH = Path("client_surfaces/freecad/bridge/ananta_freecad_bridge.py")
DEFAULT_REPORT_PATH = Path("ci-artifacts/domain-runtime/freecad-smoke-report.json")


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module load failed for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def evaluate_freecad_runtime(*, root: Path = ROOT) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    workbench_file = root / WORKBENCH_INITGUI_PATH
    bridge_file = root / BRIDGE_PATH
    for rel_path in (WORKBENCH_INITGUI_PATH, BRIDGE_PATH):
        exists = (root / rel_path).exists()
        checks.append({"check_id": f"file_exists:{rel_path.as_posix()}", "ok": exists, "detail": "found" if exists else "missing"})

    if workbench_file.exists():
        workbench_module = _load_module("ananta_freecad_initgui", workbench_file)
        workbench = getattr(workbench_module, "WORKBENCH", None)
        checks.append(
            {
                "check_id": "workbench_contract",
                "ok": workbench is not None and callable(getattr(workbench, "Initialize", None)) and callable(getattr(workbench, "GetClassName", None)),
                "detail": {
                    "has_workbench": workbench is not None,
                    "has_initialize": callable(getattr(workbench, "Initialize", None)) if workbench is not None else False,
                    "has_class_name": callable(getattr(workbench, "GetClassName", None)) if workbench is not None else False,
                },
            }
        )

    if bridge_file.exists():
        bridge_module = _load_module("ananta_freecad_bridge", bridge_file)
        envelope = bridge_module.build_freecad_bridge_envelope(
            capability_id="freecad.document.read",
            action_id="capture_context",
            payload={"mode": "smoke"},
            session_id="smoke-session",
            correlation_id="smoke-correlation",
        )
        bridge_errors = bridge_module.validate_freecad_bridge_envelope(envelope)
        checks.append({"check_id": "bridge_envelope_contract", "ok": not bridge_errors, "detail": {"errors": bridge_errors}})

    ok = all(bool(item.get("ok")) for item in checks)
    return {
        "schema": "freecad_runtime_smoke_report_v1",
        "domain_id": "freecad",
        "ok": ok,
        "checks": checks,
        "required_runtime_files": [WORKBENCH_INITGUI_PATH.as_posix(), BRIDGE_PATH.as_posix()],
    }


def write_report(report: dict[str, Any], *, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FreeCAD runtime smoke checks.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root path.")
    parser.add_argument("--report-out", default=DEFAULT_REPORT_PATH.as_posix(), help="Output report path.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report_path = Path(args.report_out)
    if not report_path.is_absolute():
        report_path = root / report_path
    report = evaluate_freecad_runtime(root=root)
    write_report(report, report_path=report_path)
    for check in list(report.get("checks") or []):
        state = "OK" if check.get("ok") else "FAIL"
        print(f"[{state}] {check.get('check_id')}")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
