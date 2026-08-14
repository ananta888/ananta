from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.run_caseflow_agent_collaboration_gate import (
    OPEN_RELEASE_BLOCKERS,
    REPORT_SCHEMA_PATH,
    SOURCE_MANIFEST_PATHS,
    VITEST_SPECS,
    GateEvidenceError,
    GateTestOutput,
    build_report,
    build_source_manifest,
    gate_commands,
    parse_playwright_report,
    parse_pytest_junit,
    parse_vitest_report,
    validate_report,
)


def _vitest(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "success": True,
        "numTotalTestSuites": 2,
        "numPassedTestSuites": 2,
        "numFailedTestSuites": 0,
        "numPendingTestSuites": 0,
        "numTotalTests": 4,
        "numPassedTests": 4,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "numTodoTests": 0,
    }
    value.update(updates)
    return value


def _playwright(*, result_status: str = "passed", test_status: str = "expected") -> dict:
    passed = result_status == "passed" and test_status == "expected"
    return {
        "suites": [
            {
                "title": "caseflow-agent-collaboration.spec.ts",
                "specs": [
                    {
                        "title": "keeps one graph",
                        "tests": [
                            {
                                "expectedStatus": "passed",
                                "status": test_status,
                                "results": [{"status": result_status, "duration": 123}],
                            }
                        ],
                    }
                ],
                "suites": [],
            }
        ],
        "errors": [],
        "stats": {
            "expected": 1 if passed else 0,
            "unexpected": 0 if passed else 1,
            "flaky": 0,
            "skipped": 0,
            "duration": 456,
            "startTime": "volatile and deliberately ignored",
        },
    }


def _junit(path: Path, *, tests: int = 3, failures: int = 0, errors: int = 0, skipped: int = 0) -> Path:
    path.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}" time="99.7" /></testsuites>',
        encoding="utf-8",
    )
    return path


def _source_root(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    root = tmp_path / "repository"
    first = root / "frontend-angular/a.ts"
    second = root / "agent/b.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("export const gate = true;\n", encoding="utf-8")
    second.write_text("GATE = True\n", encoding="utf-8")
    return root, ("frontend-angular/a.ts", "agent/b.py")


def _outputs(tmp_path: Path) -> tuple[GateTestOutput, GateTestOutput, GateTestOutput]:
    return (
        parse_vitest_report(_vitest()),
        parse_pytest_junit(_junit(tmp_path / "pytest.xml")),
        parse_playwright_report(_playwright()),
    )


def test_gate_report_is_schema_valid_stable_and_contains_no_synthetic_grounding(
    tmp_path: Path,
) -> None:
    root, paths = _source_root(tmp_path)
    outputs = _outputs(tmp_path)

    first = build_report(outputs, repository_root=root, source_paths=paths)
    second = build_report(outputs, repository_root=root, source_paths=paths)

    assert first == second
    assert first["status"] == "failed"
    assert first["source_ids"] == []
    assert first["run_ids"] == []
    assert first["source_ids_synthesized"] is False
    assert first["reason_codes"] == sorted(OPEN_RELEASE_BLOCKERS)
    schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(first)) == []
    serialized = json.dumps(first, sort_keys=True)
    for forbidden in (str(tmp_path), "duration", "timestamp", "startTime", "hostname", "pid"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "updates",
    [
        {"success": False, "numFailedTests": 1, "numPassedTests": 3},
        {"numPendingTests": 1, "numPassedTests": 3},
        {"numTodoTests": 1, "numPassedTests": 3},
        {"numTotalTests": 0, "numPassedTests": 0},
    ],
)
def test_vitest_parser_fails_closed_for_failure_skip_todo_or_empty(
    updates: dict[str, object],
) -> None:
    output = parse_vitest_report(_vitest(**updates))
    assert output.status == "failed"


@pytest.mark.parametrize(
    ("failures", "errors", "skipped"),
    [(1, 0, 0), (0, 1, 0), (0, 0, 1)],
)
def test_pytest_parser_fails_closed_for_failure_error_or_skip(
    tmp_path: Path,
    failures: int,
    errors: int,
    skipped: int,
) -> None:
    output = parse_pytest_junit(
        _junit(
            tmp_path / "pytest.xml",
            failures=failures,
            errors=errors,
            skipped=skipped,
        )
    )
    assert output.status == "failed"


def test_pytest_parser_rejects_missing_or_malformed_output(tmp_path: Path) -> None:
    with pytest.raises(GateEvidenceError, match="pytest_junit_invalid"):
        parse_pytest_junit(tmp_path / "missing.xml")
    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<testsuites>", encoding="utf-8")
    with pytest.raises(GateEvidenceError, match="pytest_junit_invalid"):
        parse_pytest_junit(malformed)


@pytest.mark.parametrize(
    ("result_status", "test_status"),
    [("failed", "unexpected"), ("timedOut", "unexpected"), ("skipped", "skipped")],
)
def test_playwright_parser_fails_closed_for_failure_timeout_or_skip(
    result_status: str,
    test_status: str,
) -> None:
    output = parse_playwright_report(
        _playwright(
            result_status=result_status,
            test_status=test_status,
        )
    )
    assert output.status == "failed"


def test_playwright_parser_rejects_missing_suite_shape() -> None:
    with pytest.raises(GateEvidenceError, match="playwright_suites_invalid"):
        parse_playwright_report({"errors": [], "stats": {}})


def test_source_manifest_is_explicit_hash_bound_and_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    root, paths = _source_root(tmp_path)
    first, first_hash = build_source_manifest(root, paths)
    second, second_hash = build_source_manifest(root, paths)
    assert first == second
    assert first_hash == second_hash

    with pytest.raises(GateEvidenceError, match="source_manifest_path_escape"):
        build_source_manifest(root, ("../outside",))
    with pytest.raises(GateEvidenceError, match="source_manifest_file_missing_or_unsafe"):
        build_source_manifest(root, ("missing.py",))
    with pytest.raises(GateEvidenceError, match="source_manifest_path_invalid"):
        build_source_manifest(root, (paths[0], paths[0]))


def test_command_allowlist_is_focused_serial_and_contains_new_runtime_fences(
    tmp_path: Path,
) -> None:
    commands = gate_commands(
        vitest_json=tmp_path / "vitest.json",
        pytest_xml=tmp_path / "pytest.xml",
        playwright_json=tmp_path / "playwright.json",
        playwright_results=tmp_path / "playwright-results",
    )

    assert [command[0] for command in commands] == [
        "angular-focused",
        "hub-focused",
        "studio-browser",
    ]
    angular = commands[0][1]
    assert "src/app/features/visual-process/vp-workflow-runner.service.spec.ts" in angular
    assert "--maxWorkers=1" in angular
    assert "--fileParallelism=false" in angular
    backend = commands[1][1]
    assert "tests/test_workflow_control_http_result.py" in backend
    assert "tests/test_temporal_runtime_contracts.py" in backend
    browser = commands[2][1]
    assert browser == [
        "npx",
        "playwright",
        "test",
        "--config",
        "playwright.caseflow-agent-collaboration.config.ts",
    ]


def test_source_manifest_uniquely_binds_every_focused_vitest_spec() -> None:
    assert len(SOURCE_MANIFEST_PATHS) == len(set(SOURCE_MANIFEST_PATHS))
    assert len(SOURCE_MANIFEST_PATHS) < 128
    assert {f"frontend-angular/{path}" for path in VITEST_SPECS}.issubset(SOURCE_MANIFEST_PATHS)
    assert "worker/temporal/workflows.py" in SOURCE_MANIFEST_PATHS
    assert "tests/test_temporal_runtime_contracts.py" in SOURCE_MANIFEST_PATHS
    assert "agent/services/workflow_control_service.py" in SOURCE_MANIFEST_PATHS
    assert "frontend-angular/src/app/features/visual-process/vp-definition-hash.ts" in SOURCE_MANIFEST_PATHS


def test_report_fails_when_a_required_output_is_failed_or_missing(tmp_path: Path) -> None:
    root, paths = _source_root(tmp_path)
    outputs = list(_outputs(tmp_path))
    outputs[2] = parse_playwright_report(_playwright(result_status="failed", test_status="unexpected"))
    failed = build_report(outputs, repository_root=root, source_paths=paths)
    assert failed["status"] == "failed"
    assert failed["reason_codes"] == sorted((*OPEN_RELEASE_BLOCKERS, "studio-browser_failed_or_incomplete"))

    incomplete = build_report(outputs[:2], repository_root=root, source_paths=paths)
    assert incomplete["status"] == "failed"
    assert "test_output_set_incomplete" in incomplete["reason_codes"]


def test_open_residual_scopes_keep_green_outputs_fail_closed(
    tmp_path: Path,
) -> None:
    root, paths = _source_root(tmp_path)

    report = build_report(
        _outputs(tmp_path),
        repository_root=root,
        source_paths=paths,
        reason_codes=OPEN_RELEASE_BLOCKERS,
    )

    assert OPEN_RELEASE_BLOCKERS == (
        "workflow_command_transition_outbox_required",
        "temporal_command_authority_verification_required",
        "workflow_terminal_trace_reconciliation_required",
    )
    assert report["status"] == "failed"
    assert report["reason_codes"] == [
        "temporal_command_authority_verification_required",
        "workflow_command_transition_outbox_required",
        "workflow_terminal_trace_reconciliation_required",
    ]
    assert all(output["status"] == "passed" for output in report["test_outputs"])


def test_schema_and_semantic_validator_reject_tampering(tmp_path: Path) -> None:
    root, paths = _source_root(tmp_path)
    report = build_report(_outputs(tmp_path), repository_root=root, source_paths=paths)

    extra = deepcopy(report)
    extra["timestamp"] = "forbidden"
    with pytest.raises(GateEvidenceError, match="gate_report_schema_invalid"):
        validate_report(extra)

    synthetic = deepcopy(report)
    synthetic["source_ids"] = ["not-an-authorized-source-identifier"]
    with pytest.raises(GateEvidenceError, match="gate_report_schema_invalid"):
        validate_report(synthetic)

    missing_blocker = deepcopy(report)
    missing_blocker["reason_codes"].remove("workflow_terminal_trace_reconciliation_required")
    with pytest.raises(GateEvidenceError, match="gate_report_schema_invalid"):
        validate_report(missing_blocker)

    stale = deepcopy(report)
    stale["source_manifest_sha256"] = "0" * 64
    with pytest.raises(GateEvidenceError, match="gate_report_manifest_hash_invalid"):
        validate_report(stale)

    forged = deepcopy(report)
    forged["test_outputs"][0]["passed"] = 3
    with pytest.raises(GateEvidenceError, match="gate_report_output_hash_invalid"):
        validate_report(forged)
