#!/usr/bin/env python3
"""Render the QA001–QA003 acceptance matrix from revision-bound gate reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONAL_INPUT = ROOT / "artifacts/test-gates/visual-process-assistant.json"
PERFORMANCE_INPUT = ROOT / "artifacts/test-gates/visual-process-assistant-performance.json"
OUTPUT = ROOT / "artifacts/test-gates/visual-process-assistant-acceptance-matrix.json"
SOURCE_PROJECTION = (
    "todos/archiv/todo.visual-process-contextual-node-configuration-ai-snake-codecompass.json",
    "docs/architecture/visual-process-assistant.md",
    "scripts/generate_visual_process_assistant_acceptance_matrix.py",
    "artifacts/test-gates/visual-process-assistant.json",
    "artifacts/test-gates/visual-process-assistant-performance.json",
)
REQUIRED_FUNCTIONAL_SUITE_IDS = frozenset(
    {
        "contract_parity",
        "hub_worker_codecompass_integration",
        "registry_backend_acceptance",
        "registry_frontend_acceptance",
        "grounded_source_authority_positive",
        "editor_patch_e2e",
        "editor_isolation_e2e",
        "assistant_security",
        "feature_flag_rollback",
    }
)
REQUIRED_PERFORMANCE_GATE_IDS = frozenset(
    {
        "hover_reference_graph",
        "codecompass_warm_retrieval",
        "context_budgets",
        "frontend_focus_stability",
    }
)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
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


def source_revision() -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_PROJECTION:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return f"worktree-sha256:{digest.hexdigest()}"


def _load(path: Path, schema: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise ValueError("visual_process_acceptance_matrix_input_invalid")
    return payload


def _criterion(
    criterion_id: str,
    statement: str,
    *,
    passed: bool,
    evidence_paths: Sequence[str],
    reason_code: str = "",
) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "statement": statement,
        "status": "passed" if passed else "blocked",
        "evidence_paths": sorted(set(evidence_paths)),
        "reason_code": "" if passed else reason_code,
    }


def _suite_map(functional: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["suite_id"]): item for item in functional.get("suites") or [] if isinstance(item, Mapping)}


def _gate_map(performance: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["gate_id"]): item for item in performance.get("gates") or [] if isinstance(item, Mapping)}


def _passed(item: Mapping[str, Any]) -> bool:
    return item.get("status") == "passed"


def _release_report_consistent(
    report: Mapping[str, Any],
    items: Mapping[str, Mapping[str, Any]],
    required_ids: frozenset[str],
) -> bool:
    """Reject missing gates and release flags that disagree with their evidence."""

    if not required_ids.issubset(items):
        return False
    expected_release = all(_passed(items[item_id]) for item_id in required_ids)
    release_flag = report.get("release_allowed")
    expected_status = "passed" if expected_release else "blocked"
    return (
        isinstance(release_flag, bool) and release_flag is expected_release and report.get("status") == expected_status
    )


def _required_item(items: Mapping[str, Mapping[str, Any]], item_id: str) -> Mapping[str, Any]:
    """Represent a missing upstream probe as explicit blocking evidence."""

    return items.get(
        item_id,
        {
            "status": "not_run",
            "reason_code": "required_gate_evidence_missing",
            "evidence_paths": [],
        },
    )


def build_matrix(
    functional: Mapping[str, Any],
    performance: Mapping[str, Any],
) -> dict[str, Any]:
    suites = _suite_map(functional)
    gates = _gate_map(performance)
    contract = _required_item(suites, "contract_parity")
    registry_backend = _required_item(suites, "registry_backend_acceptance")
    registry_frontend = _required_item(suites, "registry_frontend_acceptance")
    integration = _required_item(suites, "hub_worker_codecompass_integration")
    authority = _required_item(suites, "grounded_source_authority_positive")
    patch = _required_item(suites, "editor_patch_e2e")
    isolation = _required_item(suites, "editor_isolation_e2e")
    security = _required_item(suites, "assistant_security")
    rollback = _required_item(suites, "feature_flag_rollback")

    qa001_criteria = [
        _criterion(
            "VPA-QA-001-AC1",
            "Python-/TypeScript-Contracts für Graph, NodeDefinitions, Context, "
            "HelpResponse und WorkflowPatch sind paritätisch; die Registry-Migration "
            "ist backend- und frontendseitig ausführbar.",
            passed=(_passed(contract) and _passed(registry_backend) and _passed(registry_frontend)),
            evidence_paths=[
                *(contract.get("evidence_paths") or []),
                *(registry_backend.get("evidence_paths") or []),
                *(registry_frontend.get("evidence_paths") or []),
            ],
            reason_code=str(
                contract.get("reason_code")
                or registry_backend.get("reason_code")
                or registry_frontend.get("reason_code")
                or "contract_parity_missing"
            ),
        ),
        _criterion(
            "VPA-QA-001-AC2",
            "Hub-Task, Worker, CodeCompass, Prompt-Snapshot und HelpResponse "
            "laufen ohne Retrieval-Fake; eine isolierte Hub-Test-Policy prueft den positiven "
            "Authority-Pfad, waehrend Produktions-Grounding weiterhin Laufzeitautoritaet verlangt.",
            passed=(
                _passed(integration)
                and _passed(authority)
                and authority.get("authority_scope") == "isolated_hub_preauthorized_test_policy"
                and authority.get("production_grounding_released") is False
            ),
            evidence_paths=[
                *(integration.get("evidence_paths") or []),
                *(authority.get("evidence_paths") or []),
            ],
            reason_code=str(
                authority.get("reason_code")
                or integration.get("reason_code")
                or "authoritative_source_evidence_unavailable"
            ),
        ),
        _criterion(
            "VPA-QA-001-AC3",
            "Der Patch-E2E deckt Konfiguration, Frage, Evidence-Darstellung, Bestätigung, Undo/Redo und CAS-Save ab.",
            passed=_passed(patch),
            evidence_paths=patch.get("evidence_paths") or [],
            reason_code=str(patch.get("reason_code") or "editor_patch_e2e_missing"),
        ),
        _criterion(
            "VPA-QA-001-AC4",
            "Zwei Editorinstanzen bleiben isoliert und die AI-Snake-Prozessansicht ist read-only.",
            passed=_passed(isolation),
            evidence_paths=isolation.get("evidence_paths") or [],
            reason_code=str(isolation.get("reason_code") or "editor_isolation_e2e_missing"),
        ),
        _criterion(
            "VPA-QA-001-AC5",
            "Source-, Secret-, Injection-, stale-, Tenant-, Konflikt- und Bestätigungsgates sind fail-closed.",
            passed=_passed(security),
            evidence_paths=security.get("evidence_paths") or [],
            reason_code=str(security.get("reason_code") or "assistant_security_missing"),
        ),
    ]

    hover = _required_item(gates, "hover_reference_graph")
    retrieval = _required_item(gates, "codecompass_warm_retrieval")
    context = _required_item(gates, "context_budgets")
    frontend = _required_item(gates, "frontend_focus_stability")
    environment = dict(performance.get("environment") or {})
    environment_complete = all(
        environment.get(key) not in {None, ""}
        for key in (
            "browser",
            "build",
            "hardware_class",
            "warmup_iterations",
            "repetitions",
        )
    )
    qa002_criteria = [
        _criterion(
            "VPA-QA-002-AC1",
            "Hover und Topologie halten das 500/1000-Referenzbudget ohne Retrieval oder LLM ein.",
            passed=_passed(hover),
            evidence_paths=hover.get("evidence_paths") or [],
            reason_code=str(hover.get("reason_code") or "hover_budget_missing"),
        ),
        _criterion(
            "VPA-QA-002-AC2",
            "Warmer CodeCompass-Retrieval findet reale Kandidaten, bleibt ohne "
            "Authority fail-closed und hält Latenz/Timeout ein.",
            passed=_passed(retrieval),
            evidence_paths=retrieval.get("evidence_paths") or [],
            reason_code=str(retrieval.get("reason_code") or "retrieval_budget_missing"),
        ),
        _criterion(
            "VPA-QA-002-AC3",
            "Selected- und Conversation-Kontexte erzwingen Range-, Zeilen-, "
            "Evidence- und Tokenbudgets deterministisch.",
            passed=_passed(context),
            evidence_paths=context.get("evidence_paths") or [],
            reason_code=str(context.get("reason_code") or "context_budget_missing"),
        ),
        _criterion(
            "VPA-QA-002-AC4",
            "1000 echte Browser-Fokuswechsel halten Heap- und Subscription-Grenzen ein.",
            passed=_passed(frontend),
            evidence_paths=frontend.get("evidence_paths") or [],
            reason_code=str(frontend.get("reason_code") or "frontend_budget_missing"),
        ),
        _criterion(
            "VPA-QA-002-AC5",
            "Der Benchmarkreport enthält Browser, Build, Hardware, Warmup, Wiederholungen, p50/p95 und Pass/Fail.",
            passed=performance.get("status") == "passed" and environment_complete,
            evidence_paths=[
                "artifacts/test-gates/visual-process-assistant-performance.json",
                "artifacts/test-gates/visual-process-assistant-frontend-performance-evidence.json",
            ],
            reason_code="performance_environment_incomplete",
        ),
    ]

    functional_allowed = functional.get("release_allowed") is True
    performance_allowed = performance.get("release_allowed") is True
    functional_report_consistent = _release_report_consistent(
        functional,
        suites,
        REQUIRED_FUNCTIONAL_SUITE_IDS,
    )
    performance_report_consistent = _release_report_consistent(
        performance,
        gates,
        REQUIRED_PERFORMANCE_GATE_IDS,
    )
    release_allowed = (
        functional_report_consistent
        and performance_report_consistent
        and functional_allowed
        and performance_allowed
        and _passed(rollback)
    )
    rollout_policy_correct = (
        functional_report_consistent
        and performance_report_consistent
        and release_allowed == (functional_allowed and performance_allowed and _passed(rollback))
    )
    qa003_criteria = [
        _criterion(
            "VPA-QA-003-AC1",
            "Registry, Hover, Chat und Patch besitzen getrennte, standardmäßig deaktivierte Flags.",
            passed=_passed(rollback),
            evidence_paths=rollback.get("evidence_paths") or [],
            reason_code=str(rollback.get("reason_code") or "feature_flag_gate_missing"),
        ),
        _criterion(
            "VPA-QA-003-AC2",
            "Legacy-Graphen überstehen Öffnen, Bearbeiten, Speichern sowie Migration ohne Datenverlust.",
            passed=_passed(rollback),
            evidence_paths=rollback.get("evidence_paths") or [],
            reason_code=str(rollback.get("reason_code") or "legacy_migration_gate_missing"),
        ),
        _criterion(
            "VPA-QA-003-AC3",
            "Die Architektur dokumentiert Grenzen, Registry, Versionen, Context, "
            "Evidence, Budgets, Patches und Diagnose.",
            passed=_passed(rollback),
            evidence_paths=[
                "docs/architecture/visual-process-assistant.md",
                *(rollback.get("evidence_paths") or []),
            ],
            reason_code=str(rollback.get("reason_code") or "architecture_runbook_gate_missing"),
        ),
        _criterion(
            "VPA-QA-003-AC4",
            "Rollback mit vier deaktivierten Flags erhält Editor, Graph, Runtime-Overlay und Read-only-Ansicht.",
            passed=_passed(rollback),
            evidence_paths=rollback.get("evidence_paths") or [],
            reason_code=str(rollback.get("reason_code") or "rollback_gate_missing"),
        ),
        _criterion(
            "VPA-QA-003-AC5",
            "Rollout ist ausschließlich bei vollständigen Functional-, Security-, E2E- und Performance-Gates erlaubt.",
            passed=rollout_policy_correct,
            evidence_paths=[
                "artifacts/test-gates/visual-process-assistant.json",
                "artifacts/test-gates/visual-process-assistant-performance.json",
            ],
            reason_code="release_policy_not_fail_closed",
        ),
    ]

    def task(task_id: str, criteria: list[dict[str, Any]], *, dependencies_ok: bool = True):
        passed = dependencies_ok and all(item["status"] == "passed" for item in criteria)
        return {
            "task_id": task_id,
            "status": "passed" if passed else "blocked",
            "criteria": criteria,
        }

    qa001 = task("VPA-QA-001", qa001_criteria)
    qa002 = task("VPA-QA-002", qa002_criteria)
    # QA003 proves that rollout and rollback policy is fail-closed. A blocked
    # QA001 is therefore evidence that AC5 works, not a failure of QA003.
    qa003 = task("VPA-QA-003", qa003_criteria)
    return {
        "schema": "ananta.visual-process-assistant-acceptance-matrix.v1",
        "source_revision": source_revision(),
        "status": "passed" if release_allowed else "blocked",
        "release_allowed": release_allowed,
        "gate_source_revisions": {
            "functional": functional.get("source_revision"),
            "performance": performance.get("source_revision"),
        },
        "tasks": [qa001, qa002, qa003],
        "reason_codes": sorted(
            item["reason_code"]
            for task_item in (qa001, qa002, qa003)
            for item in task_item["criteria"]
            if item["status"] != "passed"
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--functional", type=Path, default=FUNCTIONAL_INPUT)
    parser.add_argument("--performance", type=Path, default=PERFORMANCE_INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        functional = _load(
            arguments.functional,
            "ananta.visual-process-assistant-functional-gate.v1",
        )
        performance = _load(
            arguments.performance,
            "ananta.visual-process-assistant-performance-gate.v1",
        )
        encoded = _canonical_bytes(build_matrix(functional, performance))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if arguments.check:
        if not arguments.output.is_file() or arguments.output.read_bytes() != encoded:
            print("visual_process_assistant_acceptance_matrix_drift", file=sys.stderr)
            return 1
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
