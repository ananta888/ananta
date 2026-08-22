#!/usr/bin/env python3
"""Deterministic gate for the passive HRM research decision pack."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "todos" / "todo.hrm-experiment-reasoning-workbench.json"
PACK_PATH = ROOT / "docs" / "research" / "hrm" / "decision-pack.v1.json"
SOURCE_PATH = ROOT / "docs" / "research" / "hrm" / "source-manifest.v1.json"
THREAT_PATH = ROOT / "docs" / "research" / "hrm" / "threat-model.v1.json"
PROFILE_PATH = ROOT / "docs" / "research" / "hrm" / "feasibility-profiles.v1.json"
CONTRACT_PATH = ROOT / "schemas" / "hrm-experiments" / "contracts.v1.json"
OPENAPI_PATH = ROOT / "docs" / "contracts" / "hrm-experiments.openapi.yaml"

SHA256 = re.compile(r"^[a-f0-9]{64}$")
ALLOWED_DECISIONS = {"REUSE", "EXTEND", "NEW", "REJECT"}


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=_reject_duplicate_pairs)
    if not isinstance(value, dict):
        raise ValueError(f"root_not_object:{path}")
    return value


def _closed_object_schemas(value: Any, path: str = "$") -> list[str]:
    failures: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            failures.append(path)
        for key, nested in value.items():
            failures.extend(_closed_object_schemas(nested, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            failures.extend(_closed_object_schemas(nested, f"{path}[{index}]"))
    return failures


def _validate_sudoku(profile: dict[str, Any]) -> None:
    fixture = profile["sudoku_fixture"]
    puzzle = fixture["puzzle"]
    solution = fixture["solution"]
    expected = set(range(1, 10))
    if len(puzzle) != 9 or len(solution) != 9:
        raise ValueError("sudoku_rows_invalid")
    if any(len(row) != 9 for row in puzzle + solution):
        raise ValueError("sudoku_columns_invalid")
    if any(set(row) != expected for row in solution):
        raise ValueError("sudoku_solution_row_invalid")
    if any({solution[row][column] for row in range(9)} != expected for column in range(9)):
        raise ValueError("sudoku_solution_column_invalid")
    for row_start in (0, 3, 6):
        for column_start in (0, 3, 6):
            box = {
                solution[row][column]
                for row in range(row_start, row_start + 3)
                for column in range(column_start, column_start + 3)
            }
            if box != expected:
                raise ValueError("sudoku_solution_box_invalid")
    for row in range(9):
        for column in range(9):
            given = puzzle[row][column]
            if given not in range(10):
                raise ValueError("sudoku_value_invalid")
            if given and given != solution[row][column]:
                raise ValueError("sudoku_given_changed")


def validate_pack() -> dict[str, Any]:
    todo = _load(TODO_PATH)
    pack = _load(PACK_PATH)
    source = _load(SOURCE_PATH)
    threat = _load(THREAT_PATH)
    profile = _load(PROFILE_PATH)
    contract = _load(CONTRACT_PATH)

    todo_items = [item for category in todo["categories"] for item in category["items"]]
    expected_ids = todo["meta"]["recommended_order"]
    actual_ids = [item["id"] for item in pack["items"]]
    if len(todo_items) != 32 or len(set(expected_ids)) != 32:
        raise ValueError("todo_item_set_invalid")
    if actual_ids != expected_ids:
        raise ValueError("decision_pack_order_or_scope_mismatch")
    for item in pack["items"]:
        decisions = set(item["classification"])
        if not decisions or not decisions <= ALLOWED_DECISIONS:
            raise ValueError(f"decision_classification_invalid:{item['id']}")
        if not item["owner"] or not item["required_gates"]:
            raise ValueError(f"decision_owner_or_gate_missing:{item['id']}")
    if pack["promotion"]["completed_item_decisions"] != 31:
        raise ValueError("prepared_decision_count_invalid")
    if [item["id"] for item in pack["items"] if item["research_status"] != "decided"] != ["HRMR-PLAN-003"]:
        raise ValueError("promotion_boundary_invalid")

    repository = source["upstream_repository"]
    paper = source["paper"]
    for digest in (repository["archive_sha256"], *(entry["sha256"] for entry in repository["selected_files"]), *(entry["sha256"] for entry in paper["revision_digests"])):
        if not SHA256.fullmatch(digest):
            raise ValueError("source_digest_invalid")
    if len(repository["commit_sha"]) != 40 or len(repository["tree_sha"]) != 40:
        raise ValueError("source_revision_invalid")
    if [entry["revision"] for entry in paper["revision_digests"]] != ["v1", "v2", "v3"]:
        raise ValueError("paper_revision_set_invalid")
    dependency = source["upstream_dependency_observation"]
    if dependency["all_versions_pinned"] or dependency["live_admission"] != "denied":
        raise ValueError("unpinned_dependency_policy_not_fail_closed")
    binding = source["hub_source_binding"]
    if binding["status"] == "pending_new_hub_catalog" and (binding["allowed_source_refs"] or binding["allowed_run_refs"]):
        raise ValueError("pending_catalog_contains_invented_refs")

    risks = threat["risks"]
    required_scenarios = {
        "checkpoint_rce_or_unsafe_deserialization",
        "dataset_or_plugin_code_execution",
        "ssrf_redirect_or_dns_rebinding",
        "archive_bomb_traversal_or_link_escape",
        "tenant_or_path_escape",
        "idor_or_existence_leak",
        "cancel_result_retry_race",
        "stale_ingress_after_revocation",
        "secret_or_data_egress_leak",
        "supply_chain_tampering",
        "resource_exhaustion",
        "unenforced_gpu_isolation",
    }
    if {risk["scenario"] for risk in risks} != required_scenarios:
        raise ValueError("threat_scope_invalid")
    for risk in risks:
        if risk["severity"] in {"critical", "high"} and risk["disposition"] not in {"deny", "mitigate", "conditional"}:
            raise ValueError(f"risk_disposition_invalid:{risk['id']}")
        if not risk["control_owner"] or not risk["verification_gate"]:
            raise ValueError(f"risk_owner_or_gate_missing:{risk['id']}")

    Draft202012Validator.check_schema(contract)
    open_contracts = _closed_object_schemas(contract)
    if open_contracts:
        raise ValueError(f"contract_object_not_closed:{open_contracts[0]}")
    required_contracts = {
        "capability_probe",
        "preflight_result",
        "puzzle_dataset_manifest",
        "checkpoint_manifest",
        "run_request",
        "run_status",
        "event_page",
        "cancel_request",
        "run_result",
        "evaluation_report",
    }
    if not required_contracts <= set(contract["$defs"]):
        raise ValueError("required_contract_missing")

    _validate_sudoku(profile)
    profile_states = {entry["id"]: entry["status"] for entry in profile["profiles"]}
    if profile_states["sudoku-bounded-smoke"] != "pending_explicit_approval_and_run_evidence":
        raise ValueError("sudoku_live_claim_invalid")
    if any(profile_states[item] != "deferred" for item in ("maze-plugin", "arc-plugin", "multi-gpu-or-large-training", "remote-llm-baseline")):
        raise ValueError("complex_profile_not_deferred")

    openapi = OPENAPI_PATH.read_text(encoding="utf-8")
    for endpoint in ("/api/hrm-experiments/capabilities", "/api/hrm-experiments/preflight", "/api/hrm-experiments/datasets", "/api/hrm-experiments/runs", "/api/hrm-experiments/checkpoints", "/api/hrm-experiments/evaluations", "/api/hrm-experiments/reports/{report_id}"):
        if endpoint not in openapi:
            raise ValueError(f"api_endpoint_missing:{endpoint}")
    lowered = openapi.lower()
    if "worker_url" in lowered or "server_path" in lowered:
        raise ValueError("api_leaks_internal_location")

    return {
        "schema": "ananta.hrm-research-gate.v1",
        "status": "passed",
        "item_count": len(actual_ids),
        "prepared_decisions": 31,
        "pending_promotion_decisions": 1,
        "threat_count": len(risks),
        "closed_contract_count": len(required_contracts),
        "sudoku_fixture": "valid",
        "live_runtime": "not_claimed",
    }


def main() -> int:
    print(json.dumps(validate_pack(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
