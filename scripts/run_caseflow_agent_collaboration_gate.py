#!/usr/bin/env python3
"""Run the deterministic, fail-closed CAC-013 collaboration release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend-angular"
REPORT_SCHEMA_PATH = ROOT / "schemas/testing/caseflow_agent_collaboration_gate.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/caseflow-agent-collaboration-canvas.json"
REPORT_SCHEMA = "ananta.caseflow_agent_collaboration_gate.v1"
OPEN_RELEASE_BLOCKERS: tuple[str, ...] = (
    "workflow_command_transition_outbox_required",
    "temporal_command_authority_verification_required",
    "workflow_terminal_trace_reconciliation_required",
)

VITEST_SPECS: tuple[str, ...] = (
    "src/app/features/caseflow/agent-canvas/caseflow-agent-binding-catalog.service.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-canvas.mapper.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-edge-activity.mapper.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-graph.commands.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-canvas.component.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-edge-inspector.component.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-neighborhood.selector.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-node-inspector.component.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-node-runtime-inspector.component.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-node-runtime.mapper.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-preset.commands.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-runtime-session.facade.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-agent-runtime.mapper.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-edge-trace-api.service.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-edge-trace-list.component.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-edge-trace.validator.spec.ts",
    "src/app/features/caseflow/agent-canvas/caseflow-role-catalog.spec.ts",
    "src/app/features/caseflow/scenario/caseflow-navigation.spec.ts",
    "src/app/features/caseflow/scenario/caseflow-studio-selection.facade.spec.ts",
    "src/app/features/caseflow/scenario/caseflow-studio-workspace.facade.spec.ts",
    "src/app/features/caseflow/scenario/caseflow-studio.component.spec.ts",
    "src/app/features/caseflow/scenario/caseflow-studio.guard.spec.ts",
    "src/app/features/visual-process/visual-process-editor.component.spec.ts",
    "src/app/features/visual-process/visual-process-api.service.spec.ts",
    "src/app/features/visual-process/vp-editor-state.facade.spec.ts",
    "src/app/features/visual-process/vp-runtime-overlay.mapper.spec.ts",
    "src/app/features/visual-process/vp-workflow-runner.service.spec.ts",
)

PYTEST_SPECS: tuple[str, ...] = (
    "tests/services/test_caseflow_agent_collaboration_trace_projection_service.py",
    "tests/test_caseflow_agent_collaboration_trace_api.py",
    "tests/test_caseflow_workflow_runtime_contract_api.py",
    "tests/test_workflow_configured_bridge_reconciler.py",
    "tests/test_workflow_control_http_result.py",
    "tests/test_native_graph_task_adapters.py",
    "tests/test_native_graph_production_bridge.py",
    "tests/test_langgraph_workflow_control_bridge.py",
    "tests/test_temporal_runtime_contracts.py",
    "tests/test_workflow_control_dispatch_intents.py",
    "tests/test_visual_process_bpmn_workflow.py::test_graph_to_workflow_request_requires_policy_scope",
    "tests/test_caseflow_agent_collaboration_gate.py",
)

# This allowlist binds the report to the focused gate, every explicitly named
# Vitest source, and the production seams exercised by them. It deliberately
# avoids a volatile whole-worktree digest and the report's own circular hash.
SOURCE_MANIFEST_PRODUCTION_PATHS: tuple[str, ...] = (
    "agent/routes/visual_process.py",
    "agent/routes/workflow_control_security.py",
    "agent/services/caseflow_agent_collaboration_trace_projection_service.py",
    "agent/services/local_workflow_backend.py",
    "agent/services/native_graph_task_queue_adapter.py",
    "agent/services/native_graph_control_bridge.py",
    "agent/services/native_graph_models.py",
    "agent/services/native_graph_orchestration_service.py",
    "agent/services/langgraph_workflow_control_bridge.py",
    "agent/services/temporal_workflow_backend.py",
    "agent/services/workflow_backend_durable_run_adapter.py",
    "agent/services/workflow_configured_bridge_reconciler.py",
    "agent/services/workflow_control_bindings.py",
    "agent/services/workflow_control_composition.py",
    "agent/services/workflow_control_command_verification.py",
    "agent/services/workflow_control_command_receipts.py",
    "agent/services/workflow_control_command_receipt_persistence.py",
    "agent/services/workflow_control_dispatch_intents.py",
    "agent/services/workflow_control_dispatch_persistence.py",
    "agent/services/workflow_control_dispatch_service.py",
    "agent/services/workflow_control_persistence.py",
    "agent/services/workflow_control_production_composition.py",
    "agent/services/workflow_control_runtime_registry_composition.py",
    "agent/services/workflow_control_service.py",
    "agent/services/workflow_runtime_status_projection.py",
    "agent/services/workflow_runtime/commands.py",
    "agent/services/workflow_runtime/ports.py",
    "agent/services/workflow_runtime_bridge_registry.py",
    "agent/visual_process/definition_snapshot_contract.py",
    "agent/visual_process/blueprint_mapper.py",
    "worker/temporal/workflows.py",
    "agent/db_models/workflow_runtime.py",
    "agent/db_models/__init__.py",
    "migrations/versions/c7e9a1b3d5f7_add_workflow_command_observation_pending.py",
    "frontend-angular/playwright.caseflow-agent-collaboration.config.ts",
    "frontend-angular/tests/caseflow-agent-collaboration.spec.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-canvas.component.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-canvas.mapper.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-edge-activity.mapper.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-edge-inspector.component.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-canvas.component.scss",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-edge-inspector.component.scss",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-graph.commands.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-neighborhood.selector.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-node-inspector.component.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-node-runtime.mapper.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-edge-trace-api.service.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-edge-trace.validator.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-node-runtime-inspector.component.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-node-runtime-inspector.component.scss",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-runtime-session.facade.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-runtime.mapper.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-binding-catalog.service.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-agent-preset.commands.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-edge-trace-list.component.ts",
    "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-role-catalog.ts",
    "frontend-angular/src/app/features/caseflow/scenario/caseflow-navigation.ts",
    "frontend-angular/src/app/features/caseflow/scenario/caseflow-studio-selection.facade.ts",
    "frontend-angular/src/app/features/caseflow/scenario/caseflow-studio-workspace.facade.ts",
    "frontend-angular/src/app/features/caseflow/scenario/caseflow-studio.component.ts",
    "frontend-angular/src/app/features/caseflow/scenario/caseflow-studio.guard.ts",
    "frontend-angular/src/app/features/visual-process/vp-editor-state.facade.ts",
    "frontend-angular/src/app/features/visual-process/vp-definition-hash.ts",
    "frontend-angular/src/app/features/visual-process/vp-runtime-overlay.mapper.ts",
    "frontend-angular/src/app/features/visual-process/vp-workflow-runner.service.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.html",
    "frontend-angular/src/app/features/visual-process/visual-process-editor.component.ts",
    "frontend-angular/src/app/features/visual-process/visual-process-api.service.ts",
    "schemas/testing/caseflow_agent_collaboration_gate.v1.json",
    "scripts/run_caseflow_agent_collaboration_gate.py",
    "tests/services/test_caseflow_agent_collaboration_trace_projection_service.py",
    "tests/test_caseflow_agent_collaboration_trace_api.py",
    "tests/test_caseflow_agent_collaboration_gate.py",
    "tests/test_caseflow_workflow_runtime_contract_api.py",
    "tests/test_workflow_configured_bridge_reconciler.py",
    "tests/test_workflow_control_http_result.py",
    "tests/test_workflow_control_dispatch_intents.py",
    "tests/test_workflow_control_composition.py",
    "tests/test_workflow_control_persistence.py",
    "tests/test_native_graph_task_adapters.py",
    "tests/test_native_graph_production_bridge.py",
    "tests/test_langgraph_workflow_control_bridge.py",
    "tests/test_temporal_runtime_contracts.py",
    "tests/test_visual_process_bpmn_workflow.py",
)
SOURCE_MANIFEST_PATHS: tuple[str, ...] = tuple(
    dict.fromkeys((*SOURCE_MANIFEST_PRODUCTION_PATHS, *(f"frontend-angular/{path}" for path in VITEST_SPECS)))
)


class GateEvidenceError(ValueError):
    """Raised when a required test output or source binding is incomplete."""


@dataclass(frozen=True)
class GateTestOutput:
    suite_id: str
    format: str
    output_ref: str
    status: str
    tests: int
    passed: int
    failed: int
    skipped: int
    normalized_sha256: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise GateEvidenceError("test_output_missing") from exc
    if not content or len(content) > 10_000_000:
        raise GateEvidenceError("test_output_size_invalid")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateEvidenceError("test_output_json_invalid") from exc
    if not isinstance(value, dict):
        raise GateEvidenceError("test_output_object_required")
    return value


def _count(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GateEvidenceError(f"{name}_invalid")
    return value


def _test_output(
    *,
    suite_id: str,
    output_format: str,
    output_ref: str,
    tests: int,
    passed: int,
    failed: int,
    skipped: int,
    clean: bool,
) -> GateTestOutput:
    status = "passed" if clean else "failed"
    normalized = {
        "suite_id": suite_id,
        "format": output_format,
        "status": status,
        "tests": tests,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }
    return GateTestOutput(
        **normalized,
        output_ref=output_ref,
        normalized_sha256=_canonical_sha256(normalized),
    )


def parse_vitest_report(value: Mapping[str, Any]) -> GateTestOutput:
    suites = _count(value.get("numTotalTestSuites"), "vitest_total_suites")
    passed_suites = _count(value.get("numPassedTestSuites"), "vitest_passed_suites")
    failed_suites = _count(value.get("numFailedTestSuites"), "vitest_failed_suites")
    pending_suites = _count(value.get("numPendingTestSuites"), "vitest_pending_suites")
    tests = _count(value.get("numTotalTests"), "vitest_total")
    passed = _count(value.get("numPassedTests"), "vitest_passed")
    failed = _count(value.get("numFailedTests"), "vitest_failed")
    skipped = _count(value.get("numPendingTests"), "vitest_pending")
    todo = _count(value.get("numTodoTests", 0), "vitest_todo")
    skipped += todo
    clean = (
        value.get("success") is True
        and suites > 0
        and passed_suites == suites
        and failed_suites == 0
        and pending_suites == 0
        and tests > 0
        and passed == tests
        and failed == 0
        and skipped == 0
    )
    return _test_output(
        suite_id="angular-focused",
        output_format="vitest-json",
        output_ref="ephemeral/vitest.json",
        tests=tests,
        passed=passed,
        failed=failed,
        skipped=skipped,
        clean=clean,
    )


def parse_pytest_junit(path: Path) -> GateTestOutput:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise GateEvidenceError("pytest_junit_invalid") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise GateEvidenceError("pytest_junit_suites_missing")
    counts = {
        name: sum(_count(_xml_integer(suite.attrib.get(name)), f"pytest_{name}") for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    failed = counts["failures"] + counts["errors"]
    passed = max(0, counts["tests"] - failed - counts["skipped"])
    return _test_output(
        suite_id="hub-focused",
        output_format="pytest-junit",
        output_ref="ephemeral/pytest.xml",
        tests=counts["tests"],
        passed=passed,
        failed=failed,
        skipped=counts["skipped"],
        clean=counts["tests"] > 0 and failed == 0 and counts["skipped"] == 0,
    )


def _xml_integer(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal():
        raise GateEvidenceError("pytest_count_invalid")
    return int(value)


def parse_playwright_report(value: Mapping[str, Any]) -> GateTestOutput:
    suites = value.get("suites")
    if not isinstance(suites, list):
        raise GateEvidenceError("playwright_suites_invalid")
    tests: list[Mapping[str, Any]] = []
    malformed = False

    def collect(raw_suites: Sequence[object]) -> None:
        nonlocal malformed
        for raw_suite in raw_suites:
            if not isinstance(raw_suite, Mapping):
                malformed = True
                continue
            nested = raw_suite.get("suites", [])
            specs = raw_suite.get("specs", [])
            if not isinstance(nested, list) or not isinstance(specs, list):
                malformed = True
                continue
            collect(nested)
            for raw_spec in specs:
                if not isinstance(raw_spec, Mapping) or not isinstance(raw_spec.get("tests"), list):
                    malformed = True
                    continue
                for raw_test in raw_spec["tests"]:
                    if isinstance(raw_test, Mapping):
                        tests.append(raw_test)
                    else:
                        malformed = True

    collect(suites)
    passed = failed = skipped = 0
    for test in tests:
        expected = test.get("expectedStatus")
        status = test.get("status")
        results = test.get("results")
        if expected == "skipped" or status == "skipped":
            skipped += 1
            continue
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], Mapping):
            malformed = True
            failed += 1
            continue
        result_status = results[0].get("status")
        if expected == "passed" and status == "expected" and result_status == "passed":
            passed += 1
        else:
            failed += 1

    errors = value.get("errors")
    stats = value.get("stats")
    if not isinstance(errors, list) or not isinstance(stats, Mapping):
        malformed = True
    elif errors:
        malformed = True
    expected_count = _optional_count(stats.get("expected")) if isinstance(stats, Mapping) else None
    unexpected_count = _optional_count(stats.get("unexpected")) if isinstance(stats, Mapping) else None
    flaky_count = _optional_count(stats.get("flaky")) if isinstance(stats, Mapping) else None
    stats_skipped = _optional_count(stats.get("skipped")) if isinstance(stats, Mapping) else None
    if None in (expected_count, unexpected_count, flaky_count, stats_skipped):
        malformed = True
    clean = (
        not malformed
        and len(tests) > 0
        and passed == len(tests)
        and failed == 0
        and skipped == 0
        and expected_count == passed
        and unexpected_count == 0
        and flaky_count == 0
        and stats_skipped == 0
    )
    return _test_output(
        suite_id="studio-browser",
        output_format="playwright-json",
        output_ref="ephemeral/playwright.json",
        tests=len(tests),
        passed=passed,
        failed=failed,
        skipped=skipped,
        clean=clean,
    )


def _optional_count(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def build_source_manifest(
    repository_root: Path = ROOT,
    paths: Sequence[str] = SOURCE_MANIFEST_PATHS,
) -> tuple[list[dict[str, str]], str]:
    root = repository_root.resolve()
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for relative in paths:
        if relative in seen or not relative or relative.startswith("/") or "\\" in relative:
            raise GateEvidenceError("source_manifest_path_invalid")
        seen.add(relative)
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise GateEvidenceError("source_manifest_path_escape") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise GateEvidenceError("source_manifest_file_missing_or_unsafe")
        content = candidate.read_bytes()
        if len(content) > 5_000_000:
            raise GateEvidenceError("source_manifest_file_oversized")
        entries.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    if not entries:
        raise GateEvidenceError("source_manifest_empty")
    return entries, _canonical_sha256(entries)


def build_report(
    outputs: Sequence[GateTestOutput],
    *,
    repository_root: Path = ROOT,
    source_paths: Sequence[str] = SOURCE_MANIFEST_PATHS,
    reason_codes: Sequence[str] = (),
) -> dict[str, Any]:
    manifest, manifest_sha256 = build_source_manifest(repository_root, source_paths)
    reasons = set(OPEN_RELEASE_BLOCKERS)
    reasons.update(reason_codes)
    expected_suites = ("angular-focused", "hub-focused", "studio-browser")
    supplied: dict[str, GateTestOutput] = {}
    for output in outputs:
        if output.suite_id in supplied or output.suite_id not in expected_suites:
            reasons.add("test_output_set_incomplete")
            continue
        supplied[output.suite_id] = output
    if tuple(output.suite_id for output in outputs) != expected_suites:
        reasons.add("test_output_set_incomplete")
    output_contracts = {
        "angular-focused": ("vitest-json", "ephemeral/vitest.json"),
        "hub-focused": ("pytest-junit", "ephemeral/pytest.xml"),
        "studio-browser": ("playwright-json", "ephemeral/playwright.json"),
    }
    ordered_outputs = []
    for suite_id in expected_suites:
        output = supplied.get(suite_id)
        if output is None:
            output_format, output_ref = output_contracts[suite_id]
            output = _test_output(
                suite_id=suite_id,
                output_format=output_format,
                output_ref=output_ref,
                tests=0,
                passed=0,
                failed=0,
                skipped=0,
                clean=False,
            )
        ordered_outputs.append(output)
    for output in ordered_outputs:
        if output.status != "passed":
            reasons.add(f"{output.suite_id}_failed_or_incomplete")
    status = "passed" if not reasons else "failed"
    report = {
        "schema": REPORT_SCHEMA,
        "gate_id": "CAC-013",
        "status": status,
        "fail_closed": True,
        "source_manifest": manifest,
        "source_manifest_sha256": manifest_sha256,
        "source_ids": [],
        "run_ids": [],
        "source_ids_synthesized": False,
        "test_outputs": [asdict(output) for output in ordered_outputs],
        "reason_codes": sorted(reasons),
    }
    validate_report(report)
    return report


def validate_report(report: Mapping[str, Any]) -> None:
    schema = _read_json(REPORT_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(report), key=lambda item: list(item.path))
    if errors:
        raise GateEvidenceError(f"gate_report_schema_invalid:{errors[0].message}")
    outputs = report["test_outputs"]
    reasons = report["reason_codes"]
    expected_status = "passed" if not reasons and all(output["status"] == "passed" for output in outputs) else "failed"
    if report["status"] != expected_status:
        raise GateEvidenceError("gate_report_status_inconsistent")
    if report["source_manifest_sha256"] != _canonical_sha256(report["source_manifest"]):
        raise GateEvidenceError("gate_report_manifest_hash_invalid")
    for output in outputs:
        normalized = {
            key: output[key] for key in ("suite_id", "format", "status", "tests", "passed", "failed", "skipped")
        }
        if output["normalized_sha256"] != _canonical_sha256(normalized):
            raise GateEvidenceError("gate_report_output_hash_invalid")
        if output["passed"] + output["failed"] + output["skipped"] != output["tests"]:
            raise GateEvidenceError("gate_report_output_counts_inconsistent")
        if output["status"] == "passed" and (
            output["tests"] < 1
            or output["passed"] != output["tests"]
            or output["failed"] != 0
            or output["skipped"] != 0
        ):
            raise GateEvidenceError("gate_report_output_status_inconsistent")
        if (
            output["status"] == "failed"
            and output["tests"] > 0
            and (output["passed"] == output["tests"] and output["failed"] == 0 and output["skipped"] == 0)
        ):
            raise GateEvidenceError("gate_report_output_status_inconsistent")


def _run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> str | None:
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return "command_timeout"
    except OSError:
        return "command_unavailable"
    return None if result.returncode == 0 else "command_failed"


def run_gate(*, output_path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update({"PYTHONHASHSEED": "0", "NO_COLOR": "1"})
    outputs: list[GateTestOutput] = []
    reasons: set[str] = set()
    _, source_manifest_before = build_source_manifest()
    with tempfile.TemporaryDirectory(prefix="ananta-caseflow-collaboration-") as directory:
        temporary = Path(directory)
        vitest_json = temporary / "vitest.json"
        pytest_xml = temporary / "pytest.xml"
        playwright_json = temporary / "playwright.json"
        playwright_results = temporary / "playwright-results"

        commands = gate_commands(
            vitest_json=vitest_json,
            pytest_xml=pytest_xml,
            playwright_json=playwright_json,
            playwright_results=playwright_results,
        )
        for suite_id, argv, cwd, timeout_seconds, overrides in commands:
            command_environment = {**environment, **overrides}
            failure = _run_command(
                argv,
                cwd=cwd,
                environment=command_environment,
                timeout_seconds=timeout_seconds,
            )
            if failure:
                reasons.add(f"{suite_id}_{failure}")

        parsers = (
            ("angular-focused", lambda: parse_vitest_report(_read_json(vitest_json))),
            ("hub-focused", lambda: parse_pytest_junit(pytest_xml)),
            ("studio-browser", lambda: parse_playwright_report(_read_json(playwright_json))),
        )
        for suite_id, parser in parsers:
            try:
                outputs.append(parser())
            except GateEvidenceError as exc:
                reasons.add(f"{suite_id}_{exc}")

    _, source_manifest_after = build_source_manifest()
    if source_manifest_after != source_manifest_before:
        reasons.add("source_manifest_changed_during_gate")
    report = build_report(outputs, reason_codes=tuple(sorted(reasons)))
    _atomic_write_json(output_path, report)
    return report


def gate_commands(
    *,
    vitest_json: Path,
    pytest_xml: Path,
    playwright_json: Path,
    playwright_results: Path,
) -> tuple[tuple[str, list[str], Path, int, dict[str, str]], ...]:
    """Build the fixed, shell-free command allowlist for this gate."""

    return (
        (
            "angular-focused",
            [
                "npm",
                "run",
                "test:unit",
                "--",
                *VITEST_SPECS,
                "--reporter=json",
                f"--outputFile={vitest_json}",
                "--maxWorkers=1",
                "--minWorkers=1",
                "--fileParallelism=false",
            ],
            FRONTEND,
            900,
            {},
        ),
        (
            "hub-focused",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *PYTEST_SPECS,
                f"--junitxml={pytest_xml}",
            ],
            ROOT,
            600,
            {},
        ),
        (
            "studio-browser",
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "playwright.caseflow-agent-collaboration.config.ts",
            ],
            FRONTEND,
            900,
            {
                "CASEFLOW_COLLABORATION_PLAYWRIGHT_JSON": str(playwright_json),
                "CASEFLOW_COLLABORATION_E2E_RESULTS_DIR": str(playwright_results),
            },
        ),
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        report = run_gate(output_path=arguments.output)
    except (GateEvidenceError, OSError, ValueError) as exc:
        print(f"caseflow_agent_collaboration_gate_error:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
