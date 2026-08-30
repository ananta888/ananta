#!/usr/bin/env python3
"""Build fail-closed Visual Process Assistant release-gate reports.

The generator never infers a successful test run from source-file presence.
Only explicit, revision-bound evidence can turn a required gate green.  With no
evidence input it emits deterministic blocking reports, which are safe to keep
as the initial rollout state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONAL_OUTPUT = ROOT / "artifacts/test-gates/visual-process-assistant.json"
PERFORMANCE_OUTPUT = ROOT / "artifacts/test-gates/visual-process-assistant-performance.json"
FUNCTIONAL_EVIDENCE_INPUT = ROOT / "artifacts/test-gates/visual-process-assistant-functional-evidence.json"
PERFORMANCE_EVIDENCE_INPUT = ROOT / "artifacts/test-gates/visual-process-assistant-performance-evidence.json"

FUNCTIONAL_EVIDENCE_SCHEMA = "ananta.visual-process-assistant-functional-evidence.v1"
PERFORMANCE_EVIDENCE_SCHEMA = "ananta.visual-process-assistant-performance-evidence.v1"
FRONTEND_PERFORMANCE_EVIDENCE_SCHEMA = "ananta.visual-process-assistant-frontend-performance-evidence.v1"
FRONTEND_PERFORMANCE_EVIDENCE_INPUT = (
    ROOT / "artifacts/test-gates/visual-process-assistant-frontend-performance-evidence.json"
)
BACKEND_PERFORMANCE_SOURCE_PROJECTION = (
    "agent/services/visual_process_assistant_service.py",
    "agent/services/visual_process_context_service.py",
    "agent/services/visual_process_location_service.py",
    "worker/retrieval/codecompass_channel_providers.py",
    "worker/retrieval/codecompass_retriever.py",
    "tests/benchmarks/visual_process_assistant/test_operational_budgets.py",
    "tests/test_visual_process_assistant_operations.py",
    "scripts/generate_visual_process_assistant_gates.py",
    "scripts/run_visual_process_assistant_performance_gate.py",
)
FRONTEND_PERFORMANCE_SOURCE_PROJECTION = (
    "frontend-angular/package.json",
    "frontend-angular/playwright.vpa-performance.config.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-api.service.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-canvas.component.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.html",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-api.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-bridge.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-bubble.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-context.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-canvas-interaction.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-editor-config.ts",
    "frontend-angular/src/app/features/visual-process/vp-editor-state.facade.ts",
    "frontend-angular/src/app/features/visual-process/vp-node-palette.component.ts",
    "frontend-angular/tests/visual-process-assistant-performance.spec.ts",
)
PERFORMANCE_SOURCE_PROJECTION = (
    *BACKEND_PERFORMANCE_SOURCE_PROJECTION,
    *FRONTEND_PERFORMANCE_SOURCE_PROJECTION,
)
FUNCTIONAL_SOURCE_PROJECTION = (
    "agent/config.py",
    "agent/db_models/visual_process.py",
    "agent/db_models/visual_process_assistant.py",
    "agent/routes/visual_process.py",
    "agent/routes/visual_process_assistant.py",
    "agent/services/codecompass_editor_context_contract.py",
    "agent/services/retrieval_source_contract.py",
    "agent/services/source_catalog_authority_service.py",
    "agent/services/visual_process_assistant_service.py",
    "agent/services/visual_process_context_service.py",
    "agent/services/visual_process_definition_service.py",
    "agent/services/visual_process_location_service.py",
    "agent/services/visual_process_patch_approval_policy.py",
    "agent/services/visual_process_patch_service.py",
    "agent/visual_process/node_definitions.py",
    "agent/visual_process/models.py",
    "agent/visual_process/step_adapters.py",
    "agent/visual_process/task_kind_registry.py",
    "agent/visual_process/validator.py",
    "ananta_contracts/retrieval.py",
    "ananta_contracts/visual_process_assistant.py",
    "worker/retrieval/codecompass_channel_providers.py",
    "worker/retrieval/codecompass_retriever.py",
    "worker/visual_process_assistant/evidence_gate.py",
    "worker/visual_process_assistant/handlers.py",
    "docs/architecture/visual-process-assistant.md",
    "frontend-angular/src/app/components/ai-snake-process-panel.component.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-api.service.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-canvas.component.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.html",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.scss",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-api.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-bridge.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-bubble.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-assistant-context.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-canvas-interaction.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-editor-config.ts",
    "frontend-angular/src/app/features/visual-process/vp-editor-context.models.ts",
    "frontend-angular/src/app/features/visual-process/vp-editor-state.facade.ts",
    "frontend-angular/src/app/features/visual-process/vp-node-definition-registry.service.ts",
    "frontend-angular/src/app/features/visual-process/vp-node-definitions.generated.ts",
    "frontend-angular/src/app/features/visual-process/vp-node-field-renderer.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-node-palette.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-resource-option-provider.ts",
    "frontend-angular/src/app/features/visual-process/vp-step-inspector.component.html",
    "frontend-angular/src/app/features/visual-process/vp-step-inspector.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-training-node-extension.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-workflow-patch-preview.component.ts",
    "frontend-angular/src/app/features/visual-process/vp-workflow-runner.service.ts",
    "frontend-angular/playwright.vpa-functional.config.ts",
    "migrations/versions/a5b6c7d8e9f0_add_visual_process_definition_revision.py",
    "migrations/versions/b6c7d8e9f0a1_add_visual_process_assistant_control_plane.py",
    "schemas/source/source_catalog.v2.json",
    "schemas/source/source_ref.v2.json",
    "schemas/visual_process/editor_context.v1.json",
    "schemas/visual_process/help_response.v1.json",
    "schemas/visual_process/node_definition.v1.json",
    "schemas/visual_process/workflow_patch.v1.json",
    "schemas/worker/codecompass_snapshot_manifest.v1.json",
    "scripts/generate_visual_process_assistant_baseline.py",
    "scripts/generate_codecompass_e2e_gate.py",
    "scripts/generate_visual_process_assistant_gates.py",
    "scripts/run_visual_process_assistant_functional_gate.py",
    "scripts/visual_process_test_authority.py",
    "tests/test_visual_process_assistant_baseline.py",
    "artifacts/domain/visual-process-assistant-baseline.json",
    "tests/test_visual_process_assistant_contracts.py",
    "tests/test_visual_process_definition_contract.py",
    "tests/test_visual_process_registry_migration_acceptance.py",
    "frontend-angular/src/app/features/visual-process/vp-registry-migration-acceptance.spec.ts",
    "tests/test_visual_process_assistant_service.py",
    "tests/integration/visual_process_assistant/test_hub_worker_matrix.py",
    "tests/test_codecompass_e2e_acceptance_gate.py",
    "tests/security/visual_process_assistant/test_security_gates.py",
    "tests/security/visual_process_assistant/test_patch_approval_policy.py",
    "tests/integration/visual_process_assistant/test_rollback.py",
    # Missing expected suites are part of the projection using an explicit
    # sentinel. Creating either file therefore invalidates older evidence.
    "frontend-angular/tests/visual-process-assistant-patch.spec.ts",
    "frontend-angular/tests/visual-process-assistant-isolation.spec.ts",
    "artifacts/test-gates/codecompass-e2e.json",
)

FUNCTIONAL_SUITES: tuple[dict[str, Any], ...] = (
    {
        "suite_id": "contract_parity",
        "purpose": "Python-/TypeScript-Parität für Graph, NodeDefinition, Context, HelpResponse und WorkflowPatch",
        "reproduce": [
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/test_visual_process_assistant_contracts.py",
            "tests/test_visual_process_definition_contract.py",
            "tests/test_visual_process_assistant_baseline.py",
        ],
        "implementation_paths": [
            "tests/test_visual_process_assistant_contracts.py",
            "tests/test_visual_process_definition_contract.py",
            "tests/test_visual_process_assistant_baseline.py",
        ],
    },
    {
        "suite_id": "hub_worker_codecompass_integration",
        "purpose": (
            "Reale Hub-Tasks über Worker, CodeCompass, Prompt-Snapshot und "
            "HelpResponse als persistierte Outcome-Matrix ohne Retrieval-Fake"
        ),
        "reproduce": [
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/test_visual_process_assistant_service.py",
            "tests/integration/visual_process_assistant/test_hub_worker_matrix.py",
        ],
        "implementation_paths": [
            "tests/test_visual_process_assistant_service.py",
            "tests/integration/visual_process_assistant/test_hub_worker_matrix.py",
        ],
        "required_source_markers": ["JsonlSymbolProvider", "CodeCompassRetriever"],
        "forbidden_source_markers": ["class _Retriever"],
        "missing_reason_code": "authoritative_source_evidence_unavailable",
    },
    {
        "suite_id": "registry_backend_acceptance",
        "purpose": (
            "Parametrische Registry-, Adapter-, Legacy-Alias- und Zwei-Client-CAS-Matrix für alle migrierten Node-Kinds"
        ),
        "reproduce": [
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/test_visual_process_registry_migration_acceptance.py",
        ],
        "implementation_paths": [
            "tests/test_visual_process_registry_migration_acceptance.py",
        ],
    },
    {
        "suite_id": "registry_frontend_acceptance",
        "purpose": (
            "Angular-Registry-Migration mit EditorCommand, Option-Isolation, "
            "kanonischen Pfaden und konfliktfestem Undo/Redo"
        ),
        "reproduce": [
            "npx",
            "vitest",
            "run",
            "src/app/features/visual-process/vp-registry-migration-acceptance.spec.ts",
        ],
        "working_directory": "frontend-angular",
        "implementation_paths": [
            "frontend-angular/src/app/features/visual-process/vp-registry-migration-acceptance.spec.ts",
        ],
    },
    {
        "suite_id": "grounded_source_authority_positive",
        "purpose": (
            "Positiver CodeCompass-Authority-Pfad mit expliziter, Hub-vorautorisierter "
            "Test-Policy-Identität; keine Produktions-Grounding-Freigabe"
        ),
        "reproduce": [
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/test_codecompass_e2e_acceptance_gate.py",
            "-k",
            "positive_authority_mode_releases_only_hub_preauthorized_test_ids",
        ],
        "implementation_paths": [
            "scripts/generate_codecompass_e2e_gate.py",
            "scripts/visual_process_test_authority.py",
            "tests/test_codecompass_e2e_acceptance_gate.py",
        ],
        "evidence_mode": "positive_source_authority",
        "authority_scope": "isolated_hub_preauthorized_test_policy",
        "production_grounding_released": False,
    },
    {
        "suite_id": "editor_patch_e2e",
        "purpose": "Node konfigurieren, belegte Frage, Evidence, Patch-Bestätigung, Undo/Redo und CAS-Speichern",
        "reproduce": [
            "npx",
            "playwright",
            "test",
            "--config=playwright.vpa-functional.config.ts",
            "tests/visual-process-assistant-patch.spec.ts",
        ],
        "working_directory": "frontend-angular",
        "implementation_paths": ["frontend-angular/tests/visual-process-assistant-patch.spec.ts"],
    },
    {
        "suite_id": "editor_isolation_e2e",
        "purpose": "Zwei isolierte Editorinstanzen und Read-only-AI-Snake ohne Mutationsaktionen",
        "reproduce": [
            "npx",
            "playwright",
            "test",
            "--config=playwright.vpa-functional.config.ts",
            "tests/visual-process-assistant-isolation.spec.ts",
        ],
        "working_directory": "frontend-angular",
        "implementation_paths": ["frontend-angular/tests/visual-process-assistant-isolation.spec.ts"],
    },
    {
        "suite_id": "assistant_security",
        "purpose": (
            "Fail-closed Source-, Secret-, Injection-, Revision-, Tenant- und Patch-Governance "
            "einschliesslich default-off Hub-Auto-Approval"
        ),
        "reproduce": ["python", "-m", "pytest", "-q", "tests/security/visual_process_assistant"],
        "implementation_paths": [
            "tests/security/visual_process_assistant/test_security_gates.py",
            "tests/security/visual_process_assistant/test_patch_approval_policy.py",
        ],
    },
    {
        "suite_id": "feature_flag_rollback",
        "purpose": (
            "Deaktivierte Feature-Flags und Auto-Approval-Policy erhalten Legacy-Editor, "
            "Graphen, Runtime-Overlay und Read-only-Ansicht"
        ),
        "reproduce": ["python", "-m", "pytest", "-q", "tests/integration/visual_process_assistant/test_rollback.py"],
        "implementation_paths": ["tests/integration/visual_process_assistant/test_rollback.py"],
    },
)

PERFORMANCE_GATES: tuple[dict[str, Any], ...] = (
    {
        "gate_id": "hover_reference_graph",
        "thresholds": {
            "steps": 500,
            "edges": 1000,
            "repetitions": 100,
            "delay_ms": 350,
            "p95_ms_max": 100.0,
            "retrieval_requests_max": 0,
            "llm_requests_max": 0,
        },
    },
    {
        "gate_id": "codecompass_warm_retrieval",
        "thresholds": {
            "repetitions": 100,
            "p95_ms_max": 2000.0,
            "hard_timeout_ms_max": 5000.0,
            "released_source_count_max": 0,
        },
    },
    {
        "gate_id": "context_budgets",
        "thresholds": {
            "selected_ranges_max": 4,
            "selected_lines_per_range_max": 80,
            "selected_prompt_tokens_max": 4096,
            "selected_evidence_items_max": 4,
            "conversation_ranges_max": 8,
            "conversation_lines_per_range_max": 120,
            "conversation_prompt_tokens_max": 12000,
            "conversation_evidence_items_max": 12,
        },
    },
    {
        "gate_id": "frontend_focus_stability",
        "thresholds": {
            "focus_transitions": 1000,
            "heap_growth_mib_max": 20.0,
            "hover_subscriptions_per_editor_max": 1,
            "conversation_subscriptions_per_editor_max": 1,
            "active_hover_timers_after_stabilization_max": 0,
            "active_conversation_requests_after_completion_max": 0,
        },
    },
)


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def performance_source_revision() -> str:
    """Hash the complete source projection covered by backend probes."""

    digest = hashlib.sha256()
    for relative in PERFORMANCE_SOURCE_PROJECTION:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return f"worktree-sha256:{digest.hexdigest()}"


def functional_source_hashes() -> dict[str, str]:
    """Return an exact, auditable projection including expected missing suites."""

    hashes: dict[str, str] = {}
    for relative in FUNCTIONAL_SOURCE_PROJECTION:
        source = ROOT / relative
        hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else "missing"
    return hashes


def functional_source_revision() -> str:
    digest = hashlib.sha256()
    for relative, source_hash in functional_source_hashes().items():
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_hash.encode("ascii"))
        digest.update(b"\0")
    return f"worktree-sha256:{digest.hexdigest()}"


def _load(path: Path | None, expected_schema: str) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != expected_schema:
        raise ValueError("visual_process_assistant_gate_evidence_schema_invalid")
    revision = str(payload.get("source_revision") or "").strip()
    if not revision or Path(revision).is_absolute():
        raise ValueError("visual_process_assistant_gate_source_revision_invalid")
    if expected_schema == PERFORMANCE_EVIDENCE_SCHEMA:
        if revision != performance_source_revision():
            raise ValueError("visual_process_assistant_gate_source_revision_stale")
    elif expected_schema == FUNCTIONAL_EVIDENCE_SCHEMA:
        expected_hashes = functional_source_hashes()
        if payload.get("source_hashes") != expected_hashes:
            raise ValueError("visual_process_assistant_functional_source_projection_stale")
        if revision != functional_source_revision():
            raise ValueError("visual_process_assistant_gate_source_revision_stale")
    return payload


def _safe_evidence_paths(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("visual_process_assistant_gate_evidence_paths_required")
    result: list[str] = []
    for value in raw:
        path = str(value or "").replace("\\", "/").strip()
        if not path or Path(path).is_absolute() or path.startswith("../"):
            raise ValueError("visual_process_assistant_gate_evidence_path_invalid")
        result.append(path)
    return sorted(set(result))


def build_functional_report(evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    results = dict(evidence.get("results") or {}) if evidence else {}
    known_ids = {str(item["suite_id"]) for item in FUNCTIONAL_SUITES}
    unknown = set(results) - known_ids
    if unknown:
        raise ValueError(f"visual_process_assistant_unknown_functional_suite:{sorted(unknown)[0]}")

    suites: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    for spec in FUNCTIONAL_SUITES:
        suite_id = str(spec["suite_id"])
        implementation_paths = [str(item) for item in spec.get("implementation_paths") or []]
        required_markers = [str(item) for item in spec.get("required_source_markers") or []]
        forbidden_markers = [str(item) for item in spec.get("forbidden_source_markers") or []]
        source_text = "\n".join(
            (ROOT / path).read_text(encoding="utf-8") for path in implementation_paths if (ROOT / path).is_file()
        )
        implementation_available = (
            bool(implementation_paths)
            and all((ROOT / path).is_file() for path in implementation_paths)
            and all(marker in source_text for marker in required_markers)
            and all(marker not in source_text for marker in forbidden_markers)
        )
        raw = results.get(suite_id)
        if not isinstance(raw, Mapping):
            status = "not_run"
            test_count = 0
            paths: list[str] = []
            reason = str(spec.get("missing_reason_code") or "required_gate_evidence_missing")
        else:
            status = str(raw.get("status") or "")
            if status not in {"passed", "failed"}:
                raise ValueError(f"visual_process_assistant_functional_status_invalid:{suite_id}")
            test_count = int(raw.get("test_count") or 0)
            if test_count <= 0:
                raise ValueError(f"visual_process_assistant_functional_test_count_invalid:{suite_id}")
            paths = _safe_evidence_paths(raw.get("evidence_paths"))
            reason = "" if status == "passed" else str(raw.get("reason_code") or "test_failure")
        if status != "passed":
            reason_codes.append(f"{suite_id}:{reason}")
        suites.append(
            {
                **spec,
                "implementation_status": "available" if implementation_available else "not_probed",
                "status": status,
                "test_count": test_count,
                "evidence_paths": paths,
                "reason_code": reason,
            }
        )

    release_allowed = all(item["status"] == "passed" for item in suites)
    return {
        "schema": "ananta.visual-process-assistant-functional-gate.v1",
        "gate_id": "visual-process-assistant",
        "source_revision": str(evidence.get("source_revision")) if evidence else None,
        "status": "passed" if release_allowed else "blocked",
        "release_allowed": release_allowed,
        "reason_codes": sorted(reason_codes),
        "suites": suites,
        "policy": {
            "all_required": True,
            "missing_evidence_blocks_release": True,
            "source_grounding_fail_closed": True,
            "functional_release_is_not_grounding_evidence": True,
            "runtime_source_authority_required": True,
        },
    }


def _number(raw: Mapping[str, Any], key: str, gate_id: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"visual_process_assistant_performance_metric_invalid:{gate_id}:{key}")
    value = float(value)
    if value < 0:
        raise ValueError(f"visual_process_assistant_performance_metric_invalid:{gate_id}:{key}")
    return value


def _performance_passes(gate_id: str, measurements: Mapping[str, Any], thresholds: Mapping[str, Any]) -> bool:
    if gate_id == "hover_reference_graph":
        return (
            _number(measurements, "steps", gate_id) >= thresholds["steps"]
            and _number(measurements, "edges", gate_id) >= thresholds["edges"]
            and _number(measurements, "repetitions", gate_id) >= thresholds["repetitions"]
            and _number(measurements, "delay_ms", gate_id) >= thresholds["delay_ms"]
            and _number(measurements, "p95_ms", gate_id) <= thresholds["p95_ms_max"]
            and _number(measurements, "retrieval_requests", gate_id) <= thresholds["retrieval_requests_max"]
            and _number(measurements, "llm_requests", gate_id) <= thresholds["llm_requests_max"]
        )
    if gate_id == "codecompass_warm_retrieval":
        return (
            _number(measurements, "repetitions", gate_id) >= thresholds["repetitions"]
            and _number(measurements, "p95_ms", gate_id) <= thresholds["p95_ms_max"]
            and _number(measurements, "hard_timeout_ms", gate_id) <= thresholds["hard_timeout_ms_max"]
            and _number(measurements, "released_source_count", gate_id) <= thresholds["released_source_count_max"]
            and _number(measurements, "rejected_count", gate_id) > 0
            and _number(measurements, "search_candidate_count", gate_id) >= 1
            and measurements.get("ungrounded_fixture_release_blocked") is True
        )
    if gate_id == "context_budgets":
        return (
            all(_number(measurements, key.removesuffix("_max"), gate_id) <= limit for key, limit in thresholds.items())
            and int(_number(measurements, "rejected_overflow_count", gate_id)) > 0
            and bool(measurements.get("selected_discarded_reason_counts"))
            and bool(measurements.get("conversation_discarded_reason_counts"))
            and _number(measurements, "token_budget_rejection_count", gate_id) > 0
            and measurements.get("oversized_prompt_blocked") is True
        )
    if gate_id == "frontend_focus_stability":
        return (
            _number(measurements, "focus_transitions", gate_id) >= thresholds["focus_transitions"]
            and _number(measurements, "p50_ms", gate_id) >= 0
            and _number(measurements, "p95_ms", gate_id) >= 0
            and _number(measurements, "heap_growth_mib", gate_id) <= thresholds["heap_growth_mib_max"]
            and _number(measurements, "hover_subscriptions_per_editor", gate_id)
            <= thresholds["hover_subscriptions_per_editor_max"]
            and _number(measurements, "conversation_subscriptions_per_editor", gate_id)
            <= thresholds["conversation_subscriptions_per_editor_max"]
            and _number(
                measurements,
                "active_hover_timers_after_stabilization",
                gate_id,
            )
            <= thresholds["active_hover_timers_after_stabilization_max"]
            and _number(
                measurements,
                "active_conversation_requests_after_completion",
                gate_id,
            )
            <= thresholds["active_conversation_requests_after_completion_max"]
            and _number(measurements, "editor_instances", gate_id) == 1
        )
    raise ValueError(f"visual_process_assistant_performance_gate_unknown:{gate_id}")


def build_performance_report(evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw_results = dict(evidence.get("results") or {}) if evidence else {}
    known_ids = {str(item["gate_id"]) for item in PERFORMANCE_GATES}
    unknown = set(raw_results) - known_ids
    if unknown:
        raise ValueError(f"visual_process_assistant_unknown_performance_gate:{sorted(unknown)[0]}")

    environment = dict(evidence.get("environment") or {}) if evidence else {}
    if evidence:
        required_environment = ("browser", "build", "hardware_class", "warmup_iterations", "repetitions")
        if any(environment.get(key) in (None, "") for key in required_environment):
            raise ValueError("visual_process_assistant_performance_environment_incomplete")
        if Path(str(environment["build"])).is_absolute():
            raise ValueError("visual_process_assistant_performance_build_invalid")
        for key, minimum in (("warmup_iterations", 1), ("repetitions", 100)):
            value = environment.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"visual_process_assistant_performance_environment_invalid:{key}")

    gates: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    for spec in PERFORMANCE_GATES:
        gate_id = str(spec["gate_id"])
        raw = raw_results.get(gate_id)
        if not isinstance(raw, Mapping):
            status = "not_run"
            measurements: dict[str, Any] = {}
            paths: list[str] = []
            reason = "required_gate_evidence_missing"
        else:
            measurements = dict(raw.get("measurements") or {})
            # p50 and p95 are mandatory for timed benchmarks. Context budgets
            # are deterministic caps and therefore have no latency summary.
            if gate_id != "context_budgets":
                _number(measurements, "p50_ms", gate_id)
            paths = _safe_evidence_paths(raw.get("evidence_paths"))
            status = "passed" if _performance_passes(gate_id, measurements, spec["thresholds"]) else "failed"
            reason = "" if status == "passed" else "performance_budget_exceeded"
        if status != "passed":
            reason_codes.append(f"{gate_id}:{reason}")
        gates.append(
            {
                **spec,
                "status": status,
                "measurements": measurements,
                "evidence_paths": paths,
                "reason_code": reason,
            }
        )
    release_allowed = all(item["status"] == "passed" for item in gates)
    return {
        "schema": "ananta.visual-process-assistant-performance-gate.v1",
        "gate_id": "visual-process-assistant-performance",
        "source_revision": str(evidence.get("source_revision")) if evidence else None,
        "status": "passed" if release_allowed else "blocked",
        "release_allowed": release_allowed,
        "environment": environment,
        "reason_codes": sorted(reason_codes),
        "gates": gates,
        "policy": {"all_required": True, "missing_evidence_blocks_release": True},
    }


def _write(path: Path, payload: Mapping[str, Any]) -> bytes:
    encoded = canonical_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return encoded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--functional-evidence",
        type=Path,
        default=(FUNCTIONAL_EVIDENCE_INPUT if FUNCTIONAL_EVIDENCE_INPUT.is_file() else None),
    )
    parser.add_argument(
        "--performance-evidence",
        type=Path,
        default=(PERFORMANCE_EVIDENCE_INPUT if PERFORMANCE_EVIDENCE_INPUT.is_file() else None),
    )
    parser.add_argument("--functional-output", type=Path, default=FUNCTIONAL_OUTPUT)
    parser.add_argument("--performance-output", type=Path, default=PERFORMANCE_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        functional = build_functional_report(_load(arguments.functional_evidence, FUNCTIONAL_EVIDENCE_SCHEMA))
        performance = build_performance_report(_load(arguments.performance_evidence, PERFORMANCE_EVIDENCE_SCHEMA))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    outputs = (
        (arguments.functional_output, canonical_bytes(functional)),
        (arguments.performance_output, canonical_bytes(performance)),
    )
    if arguments.check:
        drift = [str(path) for path, encoded in outputs if not path.is_file() or path.read_bytes() != encoded]
        if drift:
            print("visual_process_assistant_gate_report_drift:" + ",".join(drift), file=sys.stderr)
            return 1
    else:
        for path, encoded in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
