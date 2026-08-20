#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.operation_policy_service import OperationAuthContext, get_operation_policy_service
from agent.services.operation_registry_service import get_operation_registry_service

OUTPUT = ROOT / "artifacts" / "test-gates" / "mcp-api-operation-allowlists.json"


def _declared_operations(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name):
                continue
            if decorator.func.id == "operation_gate" and decorator.args and isinstance(decorator.args[0], ast.Constant):
                result[node.name] = str(decorator.args[0].value)
    return result


def build_report(*, tests_passed: bool) -> dict:
    registry = get_operation_registry_service()
    policy_service = get_operation_policy_service()
    legacy = policy_service.legacy_mcp_policy()
    admin = OperationAuthContext("user_jwt", is_admin=True, approval_granted=True)
    read_decision = policy_service.decide(registry.get("mcp.tool.health.get"), legacy, admin)
    write_decision = policy_service.decide(registry.get("mcp.tool.evolution.analyze"), legacy, admin)
    unknown_decision = policy_service.decide(None, legacy, admin)
    schema = json.loads((ROOT / "schemas" / "policies" / "operation_policy.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    settings_ops = _declared_operations(ROOT / "agent" / "routes" / "config" / "settings.py")
    read_model_ops = _declared_operations(ROOT / "agent" / "routes" / "config" / "read_models.py")
    expected_routes = {
        "get_config": "api.config.get",
        "set_config": "api.config.update.post",
        "rollback_operation_policy": "api.config.operation_policy.rollback.post",
        "governance_policy_read_model": "api.governance.policy.get",
        "operation_policy_inventory_read_model": "api.governance.operations.get",
    }
    declared_routes = {**settings_ops, **read_model_ops}
    route_metadata_ok = all(declared_routes.get(name) == operation_id for name, operation_id in expected_routes.items())
    unique_ids = len(registry.list_descriptors()) == len({item.operation_id for item in registry.list_descriptors()})
    probes = {
        "registry_ids_unique": unique_ids,
        "versioned_groups_expand": all(registry.group_members(group_id) for group_id in registry.list_groups()),
        "legacy_read_visible": read_decision.allowed,
        "legacy_write_default_denied": not write_decision.allowed,
        "unknown_operation_fail_closed": not unknown_decision.allowed,
        "prioritized_routes_have_operation_ids": route_metadata_ok,
        "config_schema_valid": True,
        "selected_tests_passed": tests_passed,
    }
    report = {
        "schema": "ananta.mcp_api_operation_allowlists_gate.v1",
        "gate_id": "mcp-api-operation-allowlists",
        "status": "passed" if all(probes.values()) else "failed",
        "probes": probes,
        "catalog": {
            "operation_count": len(registry.list_descriptors()),
            "groups": {key: list(value) for key, value in registry.list_groups().items()},
        },
        "effective_migration_policy": policy_service.public_projection(legacy),
        "no_bypass": {
            "unknown_reason": unknown_decision.reason_code,
            "write_reason": write_decision.reason_code,
            "direct_dispatch_gate": "agent.routes.mcp._authorize_mcp_target",
        },
        "verification_commands": [
            "python -m pytest -q tests/test_operation_policy.py tests/test_mcp_route.py tests/test_mcp_tool_registry.py tests/client_surfaces/operator_tui/test_operation_policy_inventory.py",
            "cd frontend-angular && npm run test:unit -- src/app/services/operation-policy-api.service.spec.ts",
            "python scripts/check_mcp_api_operation_allowlists.py --tests-passed",
        ],
    }
    digest_payload = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    report["artifact_hash"] = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-passed", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = build_report(tests_passed=args.tests_passed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
