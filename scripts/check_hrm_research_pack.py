#!/usr/bin/env python3
"""Deterministic gate for the passive HRM research decision pack."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "todos" / "archiv" / "todo.hrm-experiment-reasoning-workbench.json"
PACK_PATH = ROOT / "docs" / "research" / "hrm" / "decision-pack.v1.json"
SOURCE_PATH = ROOT / "docs" / "research" / "hrm" / "source-manifest.v1.json"
THREAT_PATH = ROOT / "docs" / "research" / "hrm" / "threat-model.v1.json"
PROFILE_PATH = ROOT / "docs" / "research" / "hrm" / "feasibility-profiles.v1.json"
CONTRACT_PATH = ROOT / "schemas" / "hrm-experiments" / "contracts.v1.json"
OPENAPI_PATH = ROOT / "docs" / "contracts" / "hrm-experiments.openapi.yaml"

SHA256 = re.compile(r"^[a-f0-9]{64}$")
ALLOWED_DECISIONS = {"REUSE", "EXTEND", "NEW", "REJECT"}
EXPECTED_CATALOG_ID = "catalog-6c38177316f67dfd"
EXPECTED_CATALOG_HASH = "6c38177316f67dfdcdba3c5426397aa90ced87f6648951dd1a3c4e8533251ca5"
EXPECTED_REPOSITORY_REVISION = "ddd471a4dc8ce63da4c4308e927b6a524985a93ca97f3aa5ce9ee2b11b8975dd"
EXPECTED_SOURCE_REFS = tuple(f"SRC_{index:04d}" for index in range(1, 8))
EXPECTED_CITED_SOURCE_REFS = ("SRC_0003",)
EXPECTED_RUN_REFS = ("RUN_0001",)
EXPECTED_SOURCE_CONTENT_HASH = "b5aba5f983e74fdb1af0a57f351120c00e9f217db5da6a06e8b44ca4a3d801f5"
EXPECTED_SOURCE_PROVENANCE_DIGEST = "11a264d54b818068479dff5b3e009c9910dfb5b02994fd0c070a57b69d7a738f"
EXPECTED_RUN_ID = "category-run-b55dda8fe5241e753bbf648037b91bc4"
EXPECTED_RUN_BINDING_DIGEST = "8a50ad091670a2c3144c4c3dc7b5ebc55bac1b2a44444cccf0519d0a77754b7b"
EXPECTED_RUN_EVIDENCE_DIGEST = "1dfd00989096aabdad1641c9ce4f06a96457c5b9187b473e6d6ae1b83866bb4f"
EXPECTED_REVISION_ID = "pcat-ca9b68c8fbc9e84ca3a7b1e2"
EXPECTED_REVISION_DIGEST = "6e512f797ba43d322b17e5068ef70e7b00d66fd0628f1217f18219d66dc6df59"
EXPECTED_APPROVAL_ID = "30650f79-3389-439a-9b01-28dd42ca0ac9"
EXPECTED_PROMOTION_RECEIPT_ID = "98939c6e-ab88-4de1-b405-77a6a66204d4"


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
    decisions_by_id = {item["id"]: item for item in pack["items"]}
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
    promotion = pack["promotion"]
    if promotion["status"] != "completed_hub_grounded_and_promoted":
        raise ValueError("promotion_status_invalid")
    if promotion["completed_item_decisions"] != 32 or promotion["pending_item_decisions"] != 0:
        raise ValueError("completed_decision_count_invalid")
    if any(item["research_status"] != "decided" for item in pack["items"]):
        raise ValueError("promotion_boundary_invalid")

    if todo["status"] != "completed" or todo["implementation_progress"]["completed_items"] != 32:
        raise ValueError("archived_todo_completion_invalid")
    if todo["meta"]["by_status"] != {"completed": 32, "partial": 0, "open": 0}:
        raise ValueError("archived_todo_summary_invalid")
    for item in todo_items:
        if item["status"] != "completed":
            raise ValueError(f"archived_todo_item_open:{item['id']}")
        evidence = item.get("acceptance_evidence")
        decision = decisions_by_id[item["id"]]
        if not isinstance(evidence, dict) or evidence.get("status") != "accepted_research_disposition":
            raise ValueError(f"acceptance_evidence_missing:{item['id']}")
        if evidence.get("decision_pack_item_id") != item["id"]:
            raise ValueError(f"acceptance_decision_binding_invalid:{item['id']}")
        if evidence.get("classification") != decision["classification"] or evidence.get("research_disposition") != decision["decision"]:
            raise ValueError(f"acceptance_disposition_mismatch:{item['id']}")
        if evidence.get("reviewed_acceptance_criteria") != len(item["acceptance_criteria"]):
            raise ValueError(f"acceptance_criteria_count_mismatch:{item['id']}")
        if tuple(evidence.get("source_refs", ())) != EXPECTED_CITED_SOURCE_REFS:
            raise ValueError(f"acceptance_source_binding_invalid:{item['id']}")
        if tuple(evidence.get("governance_run_refs", ())) != EXPECTED_RUN_REFS:
            raise ValueError(f"acceptance_run_binding_invalid:{item['id']}")
        if evidence.get("hub_category_revision_id") != EXPECTED_REVISION_ID:
            raise ValueError(f"acceptance_revision_binding_invalid:{item['id']}")

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
    if binding["status"] != "verified_promoted":
        raise ValueError("hub_source_binding_status_invalid")
    if binding["source_catalog_id"] != EXPECTED_CATALOG_ID or binding["source_catalog_hash"] != EXPECTED_CATALOG_HASH:
        raise ValueError("hub_catalog_binding_invalid")
    if binding["repository_revision"] != EXPECTED_REPOSITORY_REVISION:
        raise ValueError("hub_repository_revision_invalid")
    if tuple(binding["allowed_source_refs"]) != EXPECTED_SOURCE_REFS:
        raise ValueError("hub_source_allowlist_invalid")
    if tuple(binding["cited_source_refs"]) != EXPECTED_CITED_SOURCE_REFS:
        raise ValueError("hub_cited_source_refs_invalid")
    if tuple(binding["allowed_run_refs"]) != EXPECTED_RUN_REFS:
        raise ValueError("hub_run_allowlist_invalid")
    source_record = binding["source_records"][0]
    if source_record["source_id"] != "SRC_0003" or source_record["record_id"] != "docs/research/hrm/decision-pack.v1.json":
        raise ValueError("hub_source_record_invalid")
    if source_record["content_hash"] != EXPECTED_SOURCE_CONTENT_HASH or source_record["provenance_digest"] != EXPECTED_SOURCE_PROVENANCE_DIGEST:
        raise ValueError("hub_source_record_digest_invalid")
    run_evidence = binding["run_evidence"]
    if run_evidence["source_id"] != "RUN_0001" or run_evidence["run_id"] != EXPECTED_RUN_ID or run_evidence["exit_code"] != 0:
        raise ValueError("hub_run_evidence_invalid")
    if run_evidence["binding_digest"] != EXPECTED_RUN_BINDING_DIGEST or run_evidence["evidence_digest"] != EXPECTED_RUN_EVIDENCE_DIGEST:
        raise ValueError("hub_run_evidence_digest_invalid")
    planning_revision = binding["planning_revision"]
    if planning_revision["id"] != EXPECTED_REVISION_ID or planning_revision["content_digest"] != EXPECTED_REVISION_DIGEST or planning_revision["status"] != "promoted":
        raise ValueError("hub_planning_revision_invalid")
    governance = binding["governance"]
    if governance["approval_request_id"] != EXPECTED_APPROVAL_ID or governance["promotion_receipt_id"] != EXPECTED_PROMOTION_RECEIPT_ID:
        raise ValueError("hub_promotion_governance_invalid")
    if promotion["source_catalog_id"] != binding["source_catalog_id"] or promotion["source_catalog_hash"] != binding["source_catalog_hash"]:
        raise ValueError("pack_catalog_binding_mismatch")
    if tuple(promotion["allowed_source_refs"]) != EXPECTED_SOURCE_REFS or tuple(promotion["allowed_run_refs"]) != EXPECTED_RUN_REFS:
        raise ValueError("pack_allowlist_binding_mismatch")
    if promotion["artifact_revision_id"] != EXPECTED_REVISION_ID or promotion["content_digest"] != EXPECTED_REVISION_DIGEST:
        raise ValueError("pack_revision_binding_mismatch")

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
        "archived_todo": True,
        "item_count": len(actual_ids),
        "prepared_decisions": 32,
        "pending_promotion_decisions": 0,
        "hub_grounding": "verified_promoted",
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
