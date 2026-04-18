#!/usr/bin/env python3
"""Export route, policy and capability inventories for drift checks."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tool_capabilities import build_capability_contract  # noqa: E402

POLICY_PATHS = (
    Path("agent/runtime_policy.py"),
    Path("agent/routes/tasks/autopilot_dispatch_policy.py"),
    Path("agent/routes/tasks/dependency_policy.py"),
    Path("agent/services/execution_risk_policy_service.py"),
    Path("agent/services/exposure_policy_service.py"),
    Path("agent/services/remote_federation_policy_service.py"),
    Path("agent/services/task_execution_policy_service.py"),
    Path("agent/services/verification_policy_service.py"),
    Path("agent/services/worker_routing_policy_utils.py"),
)


def build_capability_inventory() -> list[dict[str, object]]:
    contract = build_capability_contract()
    return [
        {
            "name": name,
            "category": cap.category,
            "requires_admin": cap.requires_admin,
            "mutates_state": cap.mutates_state,
            "description": cap.description,
        }
        for name, cap in sorted(contract.items())
    ]


def build_policy_inventory(paths: tuple[Path, ...] = POLICY_PATHS) -> list[dict[str, object]]:
    policies: list[dict[str, object]] = []
    for path in paths:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and _looks_like_policy(node.name):
                policies.append(
                    {
                        "name": node.name,
                        "kind": "function",
                        "module": _module_name(path),
                        "path": str(path),
                        "lineno": node.lineno,
                    }
                )
            elif isinstance(node, ast.ClassDef) and _looks_like_policy(node.name):
                policies.append(
                    {
                        "name": node.name,
                        "kind": "class",
                        "module": _module_name(path),
                        "path": str(path),
                        "lineno": node.lineno,
                    }
                )
    return sorted(policies, key=lambda item: (str(item["module"]), str(item["name"])))


def build_contract_inventories() -> dict[str, object]:
    return {
        "version": "v1",
        "capabilities": build_capability_inventory(),
        "policies": build_policy_inventory(),
    }


def _looks_like_policy(name: str) -> bool:
    normalized = name.lower()
    return "policy" in normalized or normalized.startswith(("review_", "evaluate_", "resolve_"))


def _module_name(path: Path) -> str:
    return ".".join(path.with_suffix("").parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export policy and capability inventories")
    parser.add_argument("--output", default="docs/contract-inventories.json", help="Output JSON path")
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_contract_inventories(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote contract inventories to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
