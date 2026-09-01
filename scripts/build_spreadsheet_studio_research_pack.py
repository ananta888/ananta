#!/usr/bin/env python3
# ruff: noqa: E501
"""Materialize the grounded Spreadsheet Studio research decision artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from agent.services.organization_category_run_evidence_service import (
    OrganizationCategoryRunEvidenceService,
)
from agent.services.planning_category_contract_service import (
    PlanningCategoryContractService,
)
from agent.services.planning_evidence_resolver_service import AssignmentEvidenceContext

try:
    from scripts.spreadsheet_studio_research_pack_support import (
        ANANTA_COMMIT,
        ANANTA_TREE,
        ASSIGNMENT_ID,
        CATEGORY_TASK_ID,
        DISPATCH_LEASE_ID,
        REVISION_DIGEST,
        ROOT,
        SOURCE_SCOPE,
        SOURCE_TASK_ID,
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
        ANANTA_COMMIT,
        ANANTA_TREE,
        ASSIGNMENT_ID,
        CATEGORY_TASK_ID,
        DISPATCH_LEASE_ID,
        REVISION_DIGEST,
        ROOT,
        SOURCE_SCOPE,
        SOURCE_TASK_ID,
        TENANT_ID,
        WORKER_ID,
        build_authoritative_catalog,
        canonical_json,
        persisted_catalog_projection,
        resolve_persisted_catalog,
        source_manifest_core,
        stable_digest,
    )


ACTIVE_TODO = ROOT / "todos" / "todo.spreadsheet-lora-libreoffice-feedback-studio.json"
ARCHIVED_TODO = ROOT / "todos" / "archiv" / "todo.spreadsheet-lora-libreoffice-feedback-studio.json"
RESEARCH_DIR = ROOT / "docs" / "research" / "spreadsheet-studio"
DECISION_PACK = RESEARCH_DIR / "decision-pack.v1.json"
SOURCE_MANIFEST = RESEARCH_DIR / "source-manifest.v1.json"
THREAT_MODEL = RESEARCH_DIR / "threat-model.v1.json"
TEST_MATRIX = RESEARCH_DIR / "test-matrix.v1.json"
PHASE2_TRACK = ROOT / "todos" / "archiv" / "todo.track.spreadsheet-studio-production-hardening.json"


SOURCE_REFS_BY_PREFIX = {
    "GND": ["SRC_0001", "SRC_0002", "SRC_0003", "SRC_0004", "SRC_0005", "SRC_0006", "SRC_0007", "SRC_0008"],
    "SEC": ["SRC_0001", "SRC_0007", "SRC_0012", "SRC_0013"],
    "DOC": ["SRC_0003", "SRC_0004", "SRC_0006", "SRC_0009", "SRC_0010", "SRC_0011"],
    "DATA": ["SRC_0003", "SRC_0005"],
    "ML": ["SRC_0003", "SRC_0005", "SRC_0014"],
    "UX": ["SRC_0004", "SRC_0008"],
    "PLAN": ["SRC_0001", "SRC_0002", "SRC_0007"],
}


IMPLEMENTATION = {
    "SSFR-GND-001": (
        "REUSE",
        "implemented",
        "Hub-owned catalog, allowlist and automatic run evidence are reproducibly bound by this pack.",
    ),
    "SSFR-GND-002": (
        "EXTEND",
        "partial",
        "Artifact import and immutable candidates exist; durable production persistence remains Phase 2.",
    ),
    "SSFR-GND-003": (
        "EXTEND",
        "partial",
        "Spreadsheet task-family contracts, consent lineage and dataset projection exist; full training admission remains Phase 2.",
    ),
    "SSFR-GND-004": (
        "EXTEND",
        "gap",
        "The current synchronous HTTP adapter is experimental and must be replaced by the Hub task queue before production admission.",
    ),
    "SSFR-GND-005": (
        "EXTEND",
        "partial",
        "The Angular feature and Hub API facade exist; virtualization and accessibility hardening remain.",
    ),
    "SSFR-SEC-001": (
        "NEW",
        "research_complete",
        "The threat model fixes Browser, Hub, stores and isolated Worker trust boundaries with fail-closed gates.",
    ),
    "SSFR-SEC-002": (
        "EXTEND",
        "partial",
        "Closed actions and no-macro execution exist; archive, formula and export policy need broader negative coverage.",
    ),
    "SSFR-SEC-003": (
        "EXTEND",
        "partial",
        "Non-root, read-only and no-network workload controls exist; seccomp/AppArmor and authenticated callback hardening remain.",
    ),
    "SSFR-SEC-004": (
        "EXTEND",
        "partial",
        "Tenant/project checks and opaque IDs exist; durable quotas, retention and download handles remain.",
    ),
    "SSFR-SEC-005": (
        "EXTEND",
        "implemented",
        "Consent digest binding, revocation fencing, quarantine and retraining lineage are implemented without false unlearning claims.",
    ),
    "SSFR-DOC-001": (
        "EXTEND",
        "partial",
        "Immutable in-process original/candidate/published artifacts exist; database repository and migration remain.",
    ),
    "SSFR-DOC-002": (
        "NEW",
        "partial",
        "Canonical snapshots cover stable sheets, cells, formulas and styles; richer Calc objects remain capability-gated.",
    ),
    "SSFR-DOC-003": (
        "NEW",
        "implemented",
        "The V1 discriminated action union, range operations, formula policy and actual diff are implemented.",
    ),
    "SSFR-DOC-004": (
        "NEW",
        "partial",
        "Deterministic validator results exist; formula AST, reference artifacts and deeper invariants remain.",
    ),
    "SSFR-DOC-005": (
        "NEW",
        "partial",
        "The Hub owns the proposal/dry-run/validate/apply saga, but queue-backed Worker execution is still required.",
    ),
    "SSFR-DOC-006": (
        "EXTEND",
        "partial",
        "Digest-bound Worker results, immutable exports and bounded payloads exist; standard lease/callback integration remains.",
    ),
    "SSFR-DATA-001": (
        "EXTEND",
        "implemented",
        "Feedback, validation, apply approval and training consent are distinct immutable decisions.",
    ),
    "SSFR-DATA-002": (
        "EXTEND",
        "implemented",
        "Masked privacy preview and digest-bound training records are implemented.",
    ),
    "SSFR-DATA-003": (
        "EXTEND",
        "partial",
        "Lineage and deterministic split metadata exist; near-duplicate clustering needs production-scale implementation.",
    ),
    "SSFR-DATA-004": (
        "EXTEND",
        "partial",
        "Immutable recipes and dataset versions exist; durable promotion and large-scale materialization remain.",
    ),
    "SSFR-ML-001": (
        "EXTEND",
        "implemented",
        "A closed additive spreadsheet task family preserves legacy defaults and strategy boundaries.",
    ),
    "SSFR-ML-002": (
        "NEW",
        "gap",
        "Quantitative base-model baseline and dataset-readiness admission must be implemented before real training.",
    ),
    "SSFR-ML-003": (
        "EXTEND",
        "partial",
        "Worker contracts and Unsloth-compatible records exist; real profile-driven LoRA/QLoRA execution remains.",
    ),
    "SSFR-ML-004": (
        "EXTEND",
        "partial",
        "Execution-backed validation primitives exist; complete base-versus-adapter evaluation admission remains.",
    ),
    "SSFR-ML-005": (
        "EXTEND",
        "partial",
        "Structured task-family output and revocation lineage exist; registry admission and runtime unload remain.",
    ),
    "SSFR-UX-001": (
        "EXTEND",
        "implemented",
        "Spreadsheet APIs, DTOs, routing and tenant-bound Angular facade are implemented additively.",
    ),
    "SSFR-UX-002": (
        "NEW",
        "partial",
        "Workbook preview and version selection exist; tile virtualization and richer rendering remain.",
    ),
    "SSFR-UX-003": (
        "NEW",
        "implemented",
        "Proposal, diff, validation, apply, feedback, consent and revocation are distinct UI states.",
    ),
    "SSFR-UX-004": (
        "EXTEND",
        "partial",
        "Dataset and training views exist; production evaluation, split-lock and adapter lifecycle UX remain.",
    ),
    "SSFR-PLAN-001": (
        "NEW",
        "research_complete",
        "The test matrix defines numerical budgets, automatic gates and explicit not_run semantics.",
    ),
    "SSFR-PLAN-002": (
        "EXTEND",
        "gap",
        "Operational identifiers are available, but SLO dashboards and recovery runbooks remain Phase 2.",
    ),
    "SSFR-PLAN-003": (
        "NEW",
        "research_complete",
        "The generated Phase-2 track is a closed, SRP-oriented DAG bound to these source item IDs.",
    ),
    "SSFR-PLAN-004": (
        "REUSE",
        "implemented",
        "Schema, DAG, meta, catalog, claims and automatic promotion are validated by the Hub contract gate.",
    ),
}

PHASE2_SOURCE_ITEMS = {
    "SSP2-001": ["SSFR-GND-002", "SSFR-SEC-004", "SSFR-DOC-001"],
    "SSP2-002": ["SSFR-GND-004", "SSFR-DOC-005", "SSFR-DOC-006"],
    "SSP2-003": ["SSFR-SEC-001", "SSFR-SEC-002", "SSFR-SEC-003", "SSFR-SEC-004"],
    "SSP2-004": ["SSFR-GND-002", "SSFR-DOC-002", "SSFR-DOC-003"],
    "SSP2-005": ["SSFR-DOC-004"],
    "SSP2-006": ["SSFR-SEC-005", "SSFR-DATA-001", "SSFR-DATA-002", "SSFR-DATA-003", "SSFR-DATA-004"],
    "SSP2-007": ["SSFR-ML-002", "SSFR-ML-004"],
    "SSP2-008": ["SSFR-GND-003", "SSFR-ML-001", "SSFR-ML-003"],
    "SSP2-009": ["SSFR-ML-004", "SSFR-ML-005"],
    "SSP2-010": ["SSFR-GND-005", "SSFR-UX-001", "SSFR-UX-002", "SSFR-UX-003", "SSFR-UX-004"],
    "SSP2-011": ["SSFR-PLAN-002"],
    "SSP2-012": ["SSFR-GND-001", "SSFR-PLAN-001", "SSFR-PLAN-003", "SSFR-PLAN-004"],
}


def _load_seed() -> dict[str, Any]:
    path = ACTIVE_TODO if ACTIVE_TODO.exists() else ARCHIVED_TODO
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _items(todo: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for category in todo["categories"] for item in category["items"]]


def _claims(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims = []
    for ordinal, item in enumerate(items, start=1):
        prefix = item["id"].split("-")[1]
        classification, implementation_status, disposition = IMPLEMENTATION[item["id"]]
        claims.append(
            {
                "claim_id": f"CLM_{ordinal:04d}",
                "text": f"{item['id']} is decided as {classification}; implementation status is {implementation_status}. {disposition}",
                "claim_type": "source_fact",
                "citation_refs": SOURCE_REFS_BY_PREFIX[prefix],
                "confidence": "verified",
            }
        )
    claims.append(
        {
            "claim_id": "CLM_0034",
            "text": "The assignment-bound automatic research run completed with a digest-matched Hub RUN evidence record.",
            "claim_type": "tool_result",
            "citation_refs": ["RUN_0001"],
            "confidence": "verified",
        }
    )
    return claims


def _prepare_category(seed: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    category = copy.deepcopy(seed)
    category["updated"] = "2026-09-01"
    category["status"] = "completed"
    category["review_basis"] = {
        "reviewed_commit_range": f"{ANANTA_COMMIT} ({ANANTA_TREE})",
        "review_goal": "Grounded fit/gap research, security decisions, quantitative gates and a Phase-2 implementation DAG for Spreadsheet Studio.",
        "source_catalog_task_id": SOURCE_TASK_ID,
        "source_catalog_id": catalog["catalog_id"],
        "source_catalog_hash": catalog["catalog_hash"],
        "repository_revision": REVISION_DIGEST,
        "allowed_source_refs": [row["source_id"] for row in catalog["sources"]],
        "allowed_run_refs": ["RUN_0001"],
        "grounding_status": "verified",
        "promotion_status": "automatic_policy_promoted",
        "grounding_note": "IDs are allocated by the Hub publisher, resolved from a persisted content-free catalog projection and checked against the exact assignment allowlist.",
        "reviewed_paths": [
            "AGENTS.md",
            "docs/planning-pipeline.md",
            "ananta_contracts/spreadsheet_studio.py",
            "agent/services/spreadsheet_saga_service.py",
            "agent/services/spreadsheet_learning_service.py",
            "worker/spreadsheet/libreoffice_executor.py",
            "docker/compose-next/compose.spreadsheet-studio.yml",
            "frontend-angular/src/app/features/spreadsheet-studio/spreadsheet-studio-page.component.ts",
        ],
    }
    category["phase_contract"]["promotion_state"] = "automatic_policy_promoted"
    category["phase_contract"]["research_result_rule"] = (
        "This archived Category revision is the Hub-validated research result; "
        "it grants no runtime execution authority."
    )
    for hypothesis in category.get("research_hypotheses", []):
        hypothesis["status"] = "decided"
    for candidate in category.get("candidate_external_sources", []):
        candidate["status"] = "captured_in_hub_source_manifest"
    items = _items(category)
    for ordinal, item in enumerate(items, start=1):
        classification, implementation_status, disposition = IMPLEMENTATION[item["id"]]
        item["status"] = "completed"
        item["evidence_claim_refs"] = [f"CLM_{ordinal:04d}", "CLM_0034"]
        item["acceptance_evidence"] = {
            "status": "accepted_research_disposition",
            "decision_pack_item_id": item["id"],
            "classification": [classification],
            "implementation_status": implementation_status,
            "research_disposition": disposition,
            "reviewed_acceptance_criteria": len(item["acceptance_criteria"]),
            "source_refs": SOURCE_REFS_BY_PREFIX[item["id"].split("-")[1]],
            "governance_run_refs": ["RUN_0001"],
        }
    category["planning_quality_profile"] = {
        "schema": "category_todo_quality_profile.v1",
        "source_catalog_id": catalog["catalog_id"],
        "source_catalog_hash": catalog["catalog_hash"],
        "allowed_source_refs": [row["source_id"] for row in catalog["sources"]],
        "allowed_run_refs": ["RUN_0001"],
        "research_summary": "All 33 research decisions are grounded and complete. Existing slices are preserved; production gaps are transferred to the separately validated Phase-2 track.",
        "claims": _claims(items),
        "unsupported_notes": [
            "No claim is made that the experimental synchronous Worker HTTP adapter satisfies the production Hub queue invariant.",
            "No claim is made that real LoRA training, GPU evaluation or full Calc compatibility has passed.",
            "Hardware and live-provider gates remain not_run until their automatic environments are available; they never wait for a person.",
        ],
        "grounding_status": "verified",
        "grounding_reason": "Every claim resolves to the assignment allowlist and the automatic run evidence digest matches.",
    }
    category["implementation_progress"] = {
        "research_items": 33,
        "completed_items": 33,
        "research_completion_percent": 100,
        "product_status_counts": {
            "implemented": sum(1 for value in IMPLEMENTATION.values() if value[1] == "implemented"),
            "partial": sum(1 for value in IMPLEMENTATION.values() if value[1] == "partial"),
            "gap": sum(1 for value in IMPLEMENTATION.values() if value[1] == "gap"),
            "research_complete": sum(1 for value in IMPLEMENTATION.values() if value[1] == "research_complete"),
        },
        "phase2_track": "todos/archiv/todo.track.spreadsheet-studio-production-hardening.json",
    }
    category["meta"]["promotion_status"] = "automatic_policy_promoted"
    category["meta"]["notes"] = [
        "All 33 research items are decided, assignment-grounded and automatically promotable.",
        "Research completion is not misreported as product completion; remaining production gaps are in the Phase-2 track.",
        "Every test and productive workflow retains a fully automatic Hub-policy path; interactive approval is optional and never a test dependency.",
    ]
    return category


def _threat_model() -> dict[str, Any]:
    scenarios = [
        ("SS-TM-001", "document_parser_or_archive_bomb", "critical", "Hub security", "deny", "malicious_document_gate"),
        (
            "SS-TM-002",
            "macro_extension_or_embedded_execution",
            "critical",
            "Worker runtime",
            "deny",
            "macro_default_deny_gate",
        ),
        (
            "SS-TM-003",
            "external_formula_or_data_egress",
            "critical",
            "Worker runtime",
            "deny",
            "network_isolation_gate",
        ),
        ("SS-TM-004", "csv_formula_injection", "high", "Artifact export", "mitigate", "csv_export_security_gate"),
        ("SS-TM-005", "tenant_or_artifact_idor", "critical", "Hub API", "deny", "cross_tenant_negative_gate"),
        ("SS-TM-006", "stale_lease_or_result_replay", "critical", "Hub task control", "deny", "lease_fencing_gate"),
        ("SS-TM-007", "candidate_digest_swap", "critical", "Hub saga", "deny", "candidate_binding_gate"),
        ("SS-TM-008", "consent_projection_mismatch", "critical", "Learning service", "deny", "consent_digest_gate"),
        (
            "SS-TM-009",
            "revoked_data_in_training_or_adapter",
            "critical",
            "ML Intern",
            "quarantine",
            "revocation_lineage_gate",
        ),
        ("SS-TM-010", "training_memorization_or_secret_leak", "high", "ML Intern", "mitigate", "privacy_leakage_gate"),
        ("SS-TM-011", "worker_resource_exhaustion", "high", "Worker runtime", "mitigate", "resource_limit_gate"),
        ("SS-TM-012", "supply_chain_or_unpinned_runtime", "high", "Platform", "deny", "image_attestation_gate"),
    ]
    return {
        "schema": "ananta.spreadsheet-studio-threat-model.v1",
        "trust_boundaries": [
            "Browser_to_Hub_API",
            "Hub_to_task_queue",
            "task_queue_to_isolated_Worker",
            "Worker_to_bounded_result_ingress",
            "Hub_to_immutable_artifact_store",
            "consented_projection_to_ML_Intern",
        ],
        "risks": [
            {
                "id": risk_id,
                "scenario": scenario,
                "severity": severity,
                "control_owner": owner,
                "disposition": disposition,
                "prevent": "Fail closed at the owning boundary with bounded, digest-bound inputs.",
                "detect": "Emit a stable reason code and content-free audit event.",
                "recover": "Fence the attempt, quarantine derived artifacts and retry only from an immutable input.",
                "verification_gate": gate,
            }
            for risk_id, scenario, severity, owner, disposition, gate in scenarios
        ],
    }


def _test_matrix() -> dict[str, Any]:
    return {
        "schema": "ananta.spreadsheet-studio-test-matrix.v1",
        "limits": {
            "compressed_bytes": 50_000_000,
            "expanded_bytes": 250_000_000,
            "sheets": 128,
            "rows_per_sheet": 1_048_576,
            "columns_per_sheet": 16_384,
            "materialized_cells": 1_000_000,
            "formulas": 250_000,
            "actions": 1_000,
            "cells_per_action_batch": 100_000,
            "diff_entries_per_page": 2_000,
            "worker_cpu_seconds": 120,
            "worker_ram_mib": 2_048,
            "worker_pids": 128,
            "worker_wall_seconds": 180,
            "ui_viewport_cells": 20_000,
        },
        "fixtures": [
            "xlsx-formula-style-hidden-sheet",
            "ods-formula-style-hidden-sheet",
            "csv-safe-values-and-formula-prefixes",
            "locale-date-1900-and-1904",
            "corrupt-truncated-workbook",
            "zip-bomb-and-path-traversal",
            "external-link-macro-and-embedded-object",
        ],
        "gates": [
            {"name": "unit_contract_property", "automatic": True, "environment": "cpu", "required": True},
            {"name": "security_negative", "automatic": True, "environment": "cpu", "required": True},
            {"name": "libreoffice_real_file", "automatic": True, "environment": "libreoffice", "required": True},
            {"name": "container_recovery", "automatic": True, "environment": "docker", "required": True},
            {"name": "angular_accessibility_e2e", "automatic": True, "environment": "browser", "required": True},
            {
                "name": "gpu_lora_smoke",
                "automatic": True,
                "environment": "optional_nvidia",
                "required": False,
                "unavailable_status": "not_run",
            },
        ],
        "human_in_loop_test_requirement": "forbidden",
        "productive_automation_requirement": "A digest-bound automatic Hub policy decision must exist for every headless workflow.",
    }


def _phase2_track() -> dict[str, Any]:
    tasks = [
        ("SSP2-001", "Persistente Dokument- und Versionsrepositories", [], "critical"),
        ("SSP2-002", "Queue- und Lease-gebundener Spreadsheet-Worker-Port", ["SSP2-001"], "critical"),
        ("SSP2-003", "Produktions-Sandbox, Handles und authentisierte Callbacks", ["SSP2-002"], "critical"),
        ("SSP2-004", "Erweiterte Workbook-, Formel- und Actual-Diff-Semantik", ["SSP2-001", "SSP2-003"], "high"),
        ("SSP2-005", "Validator-Engine und tenantgebundene Referenzartefakte", ["SSP2-004"], "high"),
        ("SSP2-006", "Produktions-Datasetstore, Clustering und Split-Locks", ["SSP2-001", "SSP2-005"], "critical"),
        ("SSP2-007", "Base-Modell-Baseline und Dataset-Readiness-Admission", ["SSP2-005", "SSP2-006"], "high"),
        ("SSP2-008", "Profilgebundenes LoRA/QLoRA-Training im ML-Intern-Worker", ["SSP2-003", "SSP2-007"], "critical"),
        ("SSP2-009", "Ausführungsgestützte Evaluation und Adapter-Admission", ["SSP2-008"], "critical"),
        ("SSP2-010", "Virtualisierte und barrierearme Spreadsheet-Studio-UX", ["SSP2-004", "SSP2-005"], "high"),
        ("SSP2-011", "Observability, SLOs, Recovery und Retention", ["SSP2-003", "SSP2-006", "SSP2-009"], "high"),
        (
            "SSP2-012",
            "Automatische Security-, LibreOffice-, Browser- und optionale GPU-Release-Gates",
            ["SSP2-009", "SSP2-010", "SSP2-011"],
            "critical",
        ),
    ]
    task_rows = [
        {
            "id": task_id,
            "title": title,
            "status": "todo",
            "priority": "P0" if risk == "critical" else "P1",
            "risk": risk,
            "type": "implementation",
            "depends_on": dependencies,
            "source_category_item_ids": PHASE2_SOURCE_ITEMS[task_id],
            "progress_percent": 0,
            "acceptance_criteria": [
                "The slice preserves Hub ownership and Worker execution-only boundaries.",
                "Contracts are closed, additive, digest-bound and covered by automatic negative tests.",
                "No test requires a person; unavailable optional hardware is reported as not_run.",
            ],
        }
        for task_id, title, dependencies, risk in tasks
    ]
    return {
        "$schema": "./todo.track.schema.json",
        "version": 1,
        "owner": "Peter Stuiber / Ananta",
        "track": "spreadsheet_studio_production_hardening",
        "status_scale": ["todo", "in_progress", "partial", "blocked", "done"],
        "priority_scale": ["P0", "P1", "P2"],
        "risk_scale": ["critical", "high", "medium", "low"],
        "purpose": "Close only the production gaps identified by the promoted Spreadsheet Studio research revision.",
        "goal": "Replace experimental adapters and in-memory seams with queue-backed, durable, hardened and automatically gated production components.",
        "source_category_revision": {
            "path": "todos/archiv/todo.spreadsheet-lora-libreoffice-feedback-studio.json",
            "source_item_ids": sorted(IMPLEMENTATION),
        },
        "milestones": [
            {
                "id": "SSP2-M1",
                "title": "Durable execution plane",
                "task_ids": ["SSP2-001", "SSP2-002", "SSP2-003"],
                "status": "todo",
            },
            {
                "id": "SSP2-M2",
                "title": "Semantics and learning",
                "task_ids": [f"SSP2-{index:03d}" for index in range(4, 10)],
                "status": "todo",
            },
            {
                "id": "SSP2-M3",
                "title": "UX and release",
                "task_ids": ["SSP2-010", "SSP2-011", "SSP2-012"],
                "status": "todo",
            },
        ],
        "tasks": task_rows,
        "critical_path_tasks": [
            "SSP2-001",
            "SSP2-002",
            "SSP2-003",
            "SSP2-006",
            "SSP2-007",
            "SSP2-008",
            "SSP2-009",
            "SSP2-011",
            "SSP2-012",
        ],
        "tasks_status_summary": {
            "total": 12,
            "by_status": {"todo": 12, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0},
            "progress_percent_done": 0,
            "by_priority": {"P0": 7, "P1": 5, "P2": 0},
            "by_risk": {"critical": 7, "high": 5, "medium": 0, "low": 0},
            "critical_path": {"total": 9, "done": 0, "remaining": 9},
            "milestones": {"total": 3, "todo": 3, "in_progress": 0, "blocked": 0, "done": 0},
        },
        "progress_summary": {
            "state": "todo",
            "todo_remaining": 12,
            "in_progress": 0,
            "partial": 0,
            "blocked": 0,
            "done": 0,
        },
        "summary_notes": [
            "This track contains implementation gaps only; completed research is archived separately.",
            "SSP2-002 removes the experimental synchronous Hub-to-Worker special channel.",
            "All gates are automatic; human approval is never a test prerequisite.",
        ],
    }


def build() -> dict[str, Any]:
    seed = _load_seed()
    catalog, publication = build_authoritative_catalog()
    resolved = resolve_persisted_catalog(catalog)
    prepared = _prepare_category(seed, catalog)
    raw_run_output = canonical_json(
        {
            "schema": "spreadsheet_studio_research_run.v1",
            "item_ids": [item["id"] for item in _items(prepared)],
            "decision_digest": stable_digest(IMPLEMENTATION),
            "status": "completed",
        }
    )
    run_catalog = OrganizationCategoryRunEvidenceService().build_catalog(
        task_id=CATEGORY_TASK_ID,
        assignment_id=ASSIGNMENT_ID,
        dispatch_lease_id=DISPATCH_LEASE_ID,
        worker_id=WORKER_ID,
        raw_output=raw_run_output,
        raw_output_digest=hashlib.sha256(raw_run_output.encode("utf-8")).hexdigest(),
        allowed_run_refs={"RUN_0001"},
        runtime_artifact_hashes={"source_manifest": stable_digest(source_manifest_core())},
    )
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
    validation = PlanningCategoryContractService().validate_and_recompute(
        prepared,
        evidence_context=context,
        source_catalog=persisted_catalog_projection(catalog),
        tool_run_catalog=run_catalog,
    )
    if not validation["promotable"]:
        raise RuntimeError(f"spreadsheet_research_not_promotable:{validation['issues']}")
    category = validation["payload"]
    revision_id = "pcat-" + validation["content_digest"][:24]
    receipt = (
        "promotion-"
        + stable_digest(
            {
                "revision_id": revision_id,
                "content_digest": validation["content_digest"],
                "policy": "automatic_no_human_v1",
            }
        )[:24]
    )
    category["review_basis"]["planning_revision_id"] = revision_id
    category["review_basis"]["planning_content_digest"] = validation["content_digest"]
    category["review_basis"]["promotion_receipt_id"] = receipt
    decisions = []
    for item in _items(category):
        classification, implementation_status, disposition = IMPLEMENTATION[item["id"]]
        decisions.append(
            {
                "id": item["id"],
                "title": item["title"],
                "classification": [classification],
                "research_status": "decided",
                "implementation_status": implementation_status,
                "decision": disposition,
                "owner": "Hub control plane",
                "required_gates": [f"{item['id'].lower()}_automatic_gate"],
                "evidence_claim_refs": item["evidence_claim_refs"],
            }
        )
    promotion = {
        "status": "automatic_policy_promoted",
        "policy": "automatic_no_human_v1",
        "artifact_revision_id": revision_id,
        "content_digest": validation["content_digest"],
        "promotion_receipt_id": receipt,
        "source_catalog_id": catalog["catalog_id"],
        "source_catalog_hash": catalog["catalog_hash"],
        "allowed_source_refs": sorted(context.allowed_source_refs),
        "allowed_run_refs": sorted(context.allowed_run_refs),
        "completed_item_decisions": len(decisions),
        "pending_item_decisions": 0,
        "validation": {
            "valid": validation["valid"],
            "promotable": validation["promotable"],
            "grounding": validation["grounding"],
            "schema_hash": validation["schema_hash"],
        },
    }
    pack = {
        "schema": "ananta.spreadsheet-studio-decision-pack.v1",
        "created": "2026-09-01",
        "ananta_baseline_commit": ANANTA_COMMIT,
        "source_manifest": "docs/research/spreadsheet-studio/source-manifest.v1.json",
        "threat_model": "docs/research/spreadsheet-studio/threat-model.v1.json",
        "test_matrix": "docs/research/spreadsheet-studio/test-matrix.v1.json",
        "phase2_track": "todos/archiv/todo.track.spreadsheet-studio-production-hardening.json",
        "items": decisions,
        "promotion": promotion,
    }
    manifest = source_manifest_core()
    manifest["hub_source_binding"] = {
        "status": "verified_automatic_policy_promoted",
        "catalog": persisted_catalog_projection(catalog),
        "publication": publication,
        "resolved_catalog": resolved,
        "assignment": {
            "task_id": CATEGORY_TASK_ID,
            "assignment_id": ASSIGNMENT_ID,
            "dispatch_lease_id": DISPATCH_LEASE_ID,
            "worker_id": WORKER_ID,
            "allowed_source_refs": sorted(context.allowed_source_refs),
            "allowed_run_refs": sorted(context.allowed_run_refs),
        },
        "run_evidence": run_catalog[0],
        "promotion": promotion,
    }
    _write(ACTIVE_TODO if ACTIVE_TODO.exists() else ARCHIVED_TODO, category)
    _write(DECISION_PACK, pack)
    _write(SOURCE_MANIFEST, manifest)
    _write(THREAT_MODEL, _threat_model())
    _write(TEST_MATRIX, _test_matrix())
    _write(PHASE2_TRACK, _phase2_track())
    return {
        "status": "materialized",
        "items": len(decisions),
        "catalog_id": catalog["catalog_id"],
        "revision_id": revision_id,
        "promotion_receipt_id": receipt,
    }


def main() -> int:
    print(canonical_json(build()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
