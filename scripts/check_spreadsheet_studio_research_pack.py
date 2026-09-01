#!/usr/bin/env python3
"""Offline deterministic gate for the Spreadsheet Studio research pack."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.services.organization_category_run_evidence_service import (
    OrganizationCategoryRunEvidenceService,
)
from agent.services.planning_category_contract_service import PlanningCategoryContractService
from agent.services.planning_evidence_resolver_service import AssignmentEvidenceContext

try:
    from scripts.spreadsheet_studio_research_pack_support import (
        ASSIGNMENT_ID,
        CATEGORY_TASK_ID,
        DISPATCH_LEASE_ID,
        ROOT,
        SOURCE_SCOPE,
        TENANT_ID,
        WORKER_ID,
        build_authoritative_catalog,
        canonical_json,
        persisted_catalog_projection,
        resolve_persisted_catalog,
        source_manifest_core,
        stable_digest,
    )
except ModuleNotFoundError:
    from spreadsheet_studio_research_pack_support import (
        ASSIGNMENT_ID,
        CATEGORY_TASK_ID,
        DISPATCH_LEASE_ID,
        ROOT,
        SOURCE_SCOPE,
        TENANT_ID,
        WORKER_ID,
        build_authoritative_catalog,
        canonical_json,
        persisted_catalog_projection,
        resolve_persisted_catalog,
        source_manifest_core,
        stable_digest,
    )


TODO_PATH = ROOT / "todos" / "archiv" / "todo.spreadsheet-lora-libreoffice-feedback-studio.json"
PACK_PATH = ROOT / "docs" / "research" / "spreadsheet-studio" / "decision-pack.v1.json"
SOURCE_PATH = ROOT / "docs" / "research" / "spreadsheet-studio" / "source-manifest.v1.json"
THREAT_PATH = ROOT / "docs" / "research" / "spreadsheet-studio" / "threat-model.v1.json"
TEST_MATRIX_PATH = ROOT / "docs" / "research" / "spreadsheet-studio" / "test-matrix.v1.json"
TRACK_PATH = ROOT / "todos" / "todo.track.spreadsheet-studio-production-hardening.json"
TODO_SCHEMA_PATH = ROOT / "todos" / "todo.schema.json"
TRACK_SCHEMA_PATH = ROOT / "todos" / "todo.track.schema.json"


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


def _schema_validate(payload: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = _load(schema_path)
    issues = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda issue: list(issue.path),
    )
    if issues:
        path = "/".join(map(str, issues[0].path)) or "$"
        raise ValueError(f"{label}_schema_invalid:{path}:{issues[0].message}")


def _assert_track_dag(track: dict[str, Any]) -> None:
    tasks = track["tasks"]
    ids = {task["id"] for task in tasks}
    if len(ids) != len(tasks):
        raise ValueError("phase2_task_id_duplicate")
    incoming = {task_id: 0 for task_id in ids}
    outgoing = {task_id: [] for task_id in ids}
    for task in tasks:
        for dependency in task.get("depends_on", []):
            if dependency not in ids:
                raise ValueError(f"phase2_dependency_unknown:{dependency}")
            incoming[task["id"]] += 1
            outgoing[dependency].append(task["id"])
    queue = sorted(task_id for task_id, count in incoming.items() if count == 0)
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for child in sorted(outgoing[current]):
            incoming[child] -= 1
            if incoming[child] == 0:
                queue.append(child)
    if visited != len(tasks):
        raise ValueError("phase2_dependency_cycle")


def validate_pack() -> dict[str, Any]:  # noqa: C901
    todo = _load(TODO_PATH)
    pack = _load(PACK_PATH)
    source_manifest = _load(SOURCE_PATH)
    threat = _load(THREAT_PATH)
    test_matrix = _load(TEST_MATRIX_PATH)
    track = _load(TRACK_PATH)
    _schema_validate(todo, TODO_SCHEMA_PATH, "category")
    _schema_validate(track, TRACK_SCHEMA_PATH, "track")

    items = [item for category in todo["categories"] for item in category["items"]]
    item_ids = [item["id"] for item in items]
    if len(items) != 33 or len(set(item_ids)) != 33:
        raise ValueError("research_item_scope_invalid")
    if todo["status"] != "completed" or any(item["status"] != "completed" for item in items):
        raise ValueError("archived_research_not_completed")
    if todo["meta"]["by_status"] != {"completed": 33, "open": 0, "partial": 0}:
        raise ValueError("archived_research_summary_invalid")
    if todo["meta"]["recommended_order"] != item_ids:
        # The canonical DAG order is authoritative even when category grouping differs.
        if set(todo["meta"]["recommended_order"]) != set(item_ids):
            raise ValueError("research_recommended_order_scope_invalid")

    decisions = pack["items"]
    decisions_by_id = {decision["id"]: decision for decision in decisions}
    if set(decisions_by_id) != set(item_ids) or len(decisions) != 33:
        raise ValueError("decision_pack_scope_invalid")
    allowed_classifications = {"REUSE", "EXTEND", "NEW", "REJECT"}
    for item in items:
        decision = decisions_by_id[item["id"]]
        evidence = item.get("acceptance_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"research_evidence_missing:{item['id']}")
        if decision["research_status"] != "decided":
            raise ValueError(f"research_decision_open:{item['id']}")
        if not set(decision["classification"]) <= allowed_classifications:
            raise ValueError(f"research_classification_invalid:{item['id']}")
        if evidence["classification"] != decision["classification"]:
            raise ValueError(f"research_classification_mismatch:{item['id']}")
        if evidence["research_disposition"] != decision["decision"]:
            raise ValueError(f"research_disposition_mismatch:{item['id']}")
        if evidence["reviewed_acceptance_criteria"] != len(item["acceptance_criteria"]):
            raise ValueError(f"acceptance_criteria_not_reviewed:{item['id']}")
        if item["evidence_claim_refs"] != decision["evidence_claim_refs"]:
            raise ValueError(f"claim_binding_mismatch:{item['id']}")

    expected_manifest = source_manifest_core()
    for key, value in expected_manifest.items():
        if source_manifest.get(key) != value:
            raise ValueError(f"source_manifest_core_mismatch:{key}")
    catalog, publication = build_authoritative_catalog()
    binding = source_manifest["hub_source_binding"]
    if binding["catalog"] != persisted_catalog_projection(catalog):
        raise ValueError("persisted_catalog_mismatch")
    if binding["publication"] != publication:
        raise ValueError("catalog_publication_mismatch")
    if binding["resolved_catalog"] != resolve_persisted_catalog(catalog):
        raise ValueError("catalog_authority_resolution_mismatch")

    raw_run_output = canonical_json(
        {
            "schema": "spreadsheet_studio_research_run.v1",
            "item_ids": item_ids,
            "decision_digest": stable_digest(
                {
                    decision["id"]: (
                        decision["classification"][0],
                        decision["implementation_status"],
                        decision["decision"],
                    )
                    for decision in decisions
                }
            ),
            "status": "completed",
        }
    )
    expected_run = OrganizationCategoryRunEvidenceService().build_catalog(
        task_id=CATEGORY_TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        dispatch_lease_id=DISPATCH_LEASE_ID,
        worker_id=WORKER_ID,
        raw_output=raw_run_output,
        raw_output_digest=hashlib.sha256(raw_run_output.encode("utf-8")).hexdigest(),
        allowed_run_refs={"RUN_0001"},
        runtime_artifact_hashes={"source_manifest": stable_digest(source_manifest_core())},
    )
    if binding["run_evidence"] != expected_run[0]:
        raise ValueError("run_evidence_mismatch")

    context = AssignmentEvidenceContext(
        task_id=CATEGORY_TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        dispatch_lease_id=DISPATCH_LEASE_ID,
        tenant_id=TENANT_ID,
        scope=SOURCE_SCOPE,
        source_catalog_id=catalog["catalog_id"],
        source_catalog_hash=catalog["catalog_hash"],
        allowed_source_refs=frozenset(row["source_id"] for row in catalog["sources"]),
        allowed_run_refs=frozenset({"RUN_0001"}),
        artifact_hashes={},
    )
    candidate = copy.deepcopy(todo)
    for field in ("planning_revision_id", "planning_content_digest", "promotion_receipt_id"):
        candidate["review_basis"].pop(field, None)
    result = PlanningCategoryContractService().validate_and_recompute(
        candidate,
        evidence_context=context,
        source_catalog=persisted_catalog_projection(catalog),
        tool_run_catalog=expected_run,
    )
    if not result["promotable"] or result["grounding"].get("status") != "verified":
        raise ValueError(f"category_not_promotable:{result['issues']}")
    promotion = pack["promotion"]
    if promotion["status"] != "automatic_policy_promoted" or promotion["policy"] != "automatic_no_human_v1":
        raise ValueError("automatic_promotion_policy_invalid")
    if promotion["content_digest"] != result["content_digest"]:
        raise ValueError("category_revision_digest_mismatch")
    if todo["review_basis"]["planning_content_digest"] != result["content_digest"]:
        raise ValueError("todo_revision_digest_mismatch")
    if binding["promotion"] != promotion:
        raise ValueError("source_binding_promotion_mismatch")

    required_risks = {
        "document_parser_or_archive_bomb",
        "macro_extension_or_embedded_execution",
        "external_formula_or_data_egress",
        "csv_formula_injection",
        "tenant_or_artifact_idor",
        "stale_lease_or_result_replay",
        "candidate_digest_swap",
        "consent_projection_mismatch",
        "revoked_data_in_training_or_adapter",
        "training_memorization_or_secret_leak",
        "worker_resource_exhaustion",
        "supply_chain_or_unpinned_runtime",
    }
    if {risk["scenario"] for risk in threat["risks"]} != required_risks:
        raise ValueError("threat_model_scope_invalid")
    if any(not risk["control_owner"] or not risk["verification_gate"] for risk in threat["risks"]):
        raise ValueError("threat_control_or_gate_missing")
    if test_matrix["human_in_loop_test_requirement"] != "forbidden":
        raise ValueError("human_in_loop_test_dependency_detected")
    if any(gate["automatic"] is not True for gate in test_matrix["gates"]):
        raise ValueError("non_automatic_test_gate_detected")
    if min(test_matrix["limits"].values()) <= 0:
        raise ValueError("quantitative_limit_invalid")

    _assert_track_dag(track)
    if len(track["tasks"]) != 12 or any(task["status"] != "todo" for task in track["tasks"]):
        raise ValueError("phase2_track_status_invalid")
    covered_source_items: set[str] = set()
    for task in track["tasks"]:
        task_source_items = set(task["source_category_item_ids"])
        if not task_source_items or not task_source_items <= set(item_ids):
            raise ValueError(f"phase2_source_category_binding_invalid:{task['id']}")
        covered_source_items.update(task_source_items)
    if covered_source_items != set(item_ids):
        raise ValueError("phase2_source_category_coverage_incomplete")
    if track["tasks_status_summary"]["by_status"]["blocked"] != 0:
        raise ValueError("phase2_track_unexpected_blocked_task")

    return {
        "schema": "ananta.spreadsheet-studio-research-gate.v1",
        "status": "passed",
        "archived_todo": True,
        "research_items": 33,
        "verified_claims": result["grounding"]["verified_claim_count"],
        "source_refs": len(context.allowed_source_refs),
        "run_refs": len(context.allowed_run_refs),
        "threats": len(threat["risks"]),
        "phase2_tasks": len(track["tasks"]),
        "promotion": "automatic_policy_promoted",
        "human_in_loop_tests": "forbidden",
    }


def main() -> int:
    print(canonical_json(validate_pack()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
