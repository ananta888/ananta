from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_visual_process_assistant_gates import (
    FUNCTIONAL_EVIDENCE_INPUT,
    FUNCTIONAL_OUTPUT,
    PERFORMANCE_EVIDENCE_INPUT,
    PERFORMANCE_OUTPUT,
    build_functional_report,
    build_performance_report,
    canonical_bytes,
    functional_source_hashes,
    functional_source_revision,
    main,
)
from scripts.run_visual_process_assistant_functional_gate import (
    _executed_test_count,
)


def _functional_evidence() -> dict:
    suite_ids = {
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
    return {
        "schema": "ananta.visual-process-assistant-functional-evidence.v1",
        "source_revision": functional_source_revision(),
        "source_hashes": functional_source_hashes(),
        "results": {
            suite_id: {
                "status": "passed",
                "test_count": 1,
                "evidence_paths": [f"artifacts/test-gates/evidence/{suite_id}.json"],
            }
            for suite_id in suite_ids
        },
    }


def _performance_evidence() -> dict:
    return {
        "schema": "ananta.visual-process-assistant-performance-evidence.v1",
        "source_revision": "revision-under-test",
        "environment": {
            "browser": "Chromium stable",
            "build": "production-angular",
            "hardware_class": "local-reference-8-core-32-gib",
            "warmup_iterations": 10,
            "repetitions": 100,
        },
        "results": {
            "hover_reference_graph": {
                "measurements": {
                    "steps": 500,
                    "edges": 1000,
                    "repetitions": 100,
                    "delay_ms": 350,
                    "p50_ms": 10,
                    "p95_ms": 100,
                    "retrieval_requests": 0,
                    "llm_requests": 0,
                },
                "evidence_paths": ["artifacts/test-gates/evidence/hover.json"],
            },
            "codecompass_warm_retrieval": {
                "measurements": {
                    "p50_ms": 1000,
                    "p95_ms": 2000,
                    "hard_timeout_ms": 5000,
                    "repetitions": 100,
                    "released_source_count": 0,
                    "search_candidate_count": 1,
                    "rejected_count": 1,
                    "ungrounded_fixture_release_blocked": True,
                },
                "evidence_paths": ["artifacts/test-gates/evidence/retrieval.json"],
            },
            "context_budgets": {
                "measurements": {
                    "selected_ranges": 4,
                    "selected_lines_per_range": 80,
                    "selected_prompt_tokens": 4096,
                    "selected_evidence_items": 4,
                    "conversation_ranges": 8,
                    "conversation_lines_per_range": 120,
                    "conversation_prompt_tokens": 12000,
                    "conversation_evidence_items": 12,
                    "rejected_overflow_count": 1,
                    "selected_discarded_reason_counts": {"evidence_item_budget_exceeded": 1},
                    "conversation_discarded_reason_counts": {"range_budget_exceeded": 1},
                    "token_budget_rejection_count": 1,
                    "oversized_prompt_blocked": True,
                },
                "evidence_paths": ["artifacts/test-gates/evidence/context.json"],
            },
            "frontend_focus_stability": {
                "measurements": {
                    "focus_transitions": 1000,
                    "p50_ms": 0.1,
                    "p95_ms": 0.5,
                    "heap_growth_mib": 20,
                    "hover_subscriptions_per_editor": 1,
                    "conversation_subscriptions_per_editor": 1,
                    "active_hover_timers_after_stabilization": 0,
                    "active_conversation_requests_after_completion": 0,
                    "editor_instances": 1,
                },
                "evidence_paths": ["artifacts/test-gates/evidence/frontend.json"],
            },
        },
    }


def test_functional_runner_counts_vitest_tests_without_counting_test_files() -> None:
    output = """
 Test Files  1 passed (1)
      Tests  3 passed (3)
"""

    assert _executed_test_count(output) == 3


def test_committed_reports_are_deterministic_and_runtime_grounding_stays_fail_closed() -> None:
    functional_evidence = json.loads(FUNCTIONAL_EVIDENCE_INPUT.read_text(encoding="utf-8"))
    functional = build_functional_report(functional_evidence)
    performance_evidence = json.loads(PERFORMANCE_EVIDENCE_INPUT.read_text(encoding="utf-8"))
    performance = build_performance_report(performance_evidence)

    assert functional["status"] == "passed"
    assert performance["status"] == "passed"
    assert functional["release_allowed"] is True
    assert performance["release_allowed"] is True
    functional_statuses = {item["suite_id"]: item["status"] for item in functional["suites"]}
    assert functional_statuses == {
        "contract_parity": "passed",
        "hub_worker_codecompass_integration": "passed",
        "registry_backend_acceptance": "passed",
        "registry_frontend_acceptance": "passed",
        "grounded_source_authority_positive": "passed",
        "editor_patch_e2e": "passed",
        "editor_isolation_e2e": "passed",
        "assistant_security": "passed",
        "feature_flag_rollback": "passed",
    }
    assert functional["reason_codes"] == []
    authority = next(
        item for item in functional["suites"] if item["suite_id"] == "grounded_source_authority_positive"
    )
    assert authority["authority_scope"] == "isolated_hub_preauthorized_test_policy"
    assert authority["production_grounding_released"] is False
    assert functional["policy"]["runtime_source_authority_required"] is True
    performance_statuses = {item["gate_id"]: item["status"] for item in performance["gates"]}
    assert performance_statuses == {
        "hover_reference_graph": "passed",
        "codecompass_warm_retrieval": "passed",
        "context_budgets": "passed",
        "frontend_focus_stability": "passed",
    }
    integration = next(
        item for item in functional["suites"] if item["suite_id"] == "hub_worker_codecompass_integration"
    )
    assert integration["implementation_status"] == "available"
    assert integration["reproduce"][-1] == ("tests/integration/visual_process_assistant/test_hub_worker_matrix.py")
    assert FUNCTIONAL_OUTPUT.read_bytes() == canonical_bytes(functional)
    assert PERFORMANCE_OUTPUT.read_bytes() == canonical_bytes(performance)


def test_default_generator_uses_measured_evidence_and_detects_report_drift(
    tmp_path: Path,
) -> None:
    functional_output = tmp_path / "functional.json"
    performance_output = tmp_path / "performance.json"
    arguments = [
        "--functional-output",
        str(functional_output),
        "--performance-output",
        str(performance_output),
    ]

    assert main(arguments) == 0
    generated = json.loads(performance_output.read_text(encoding="utf-8"))
    assert generated["source_revision"].startswith("worktree-sha256:")
    assert generated["status"] == "passed"
    performance_output.write_text("{}\n", encoding="utf-8")
    assert main([*arguments, "--check"]) == 1


def test_generator_rejects_stale_performance_source_revision(tmp_path: Path) -> None:
    evidence = json.loads(PERFORMANCE_EVIDENCE_INPUT.read_text(encoding="utf-8"))
    evidence["source_revision"] = "worktree-sha256:" + "0" * 64
    stale = tmp_path / "stale-evidence.json"
    stale.write_bytes(canonical_bytes(evidence))

    assert (
        main(
            [
                "--performance-evidence",
                str(stale),
                "--functional-output",
                str(tmp_path / "functional.json"),
                "--performance-output",
                str(tmp_path / "performance.json"),
            ]
        )
        == 2
    )


@pytest.mark.parametrize("mutation", ["revision", "source_hash"])
def test_generator_rejects_stale_functional_source_projection(
    tmp_path: Path,
    mutation: str,
) -> None:
    evidence = _functional_evidence()
    if mutation == "revision":
        evidence["source_revision"] = "worktree-sha256:" + "0" * 64
    else:
        first_path = next(iter(evidence["source_hashes"]))
        evidence["source_hashes"][first_path] = "0" * 64
    stale = tmp_path / "stale-functional-evidence.json"
    stale.write_bytes(canonical_bytes(evidence))

    assert (
        main(
            [
                "--functional-evidence",
                str(stale),
                "--performance-evidence",
                str(PERFORMANCE_EVIDENCE_INPUT),
                "--functional-output",
                str(tmp_path / "functional.json"),
                "--performance-output",
                str(tmp_path / "performance.json"),
            ]
        )
        == 2
    )


def test_complete_revision_bound_functional_evidence_allows_release() -> None:
    report = build_functional_report(_functional_evidence())

    assert report["status"] == "passed"
    assert report["release_allowed"] is True
    assert report["reason_codes"] == []
    assert all(item["status"] == "passed" for item in report["suites"])


def test_missing_functional_suite_remains_release_blocking() -> None:
    evidence = _functional_evidence()
    del evidence["results"]["assistant_security"]

    report = build_functional_report(evidence)

    assert report["status"] == "blocked"
    assert "assistant_security:required_gate_evidence_missing" in report["reason_codes"]


def test_performance_thresholds_are_inclusive_and_environment_is_recorded() -> None:
    evidence = _performance_evidence()

    report = build_performance_report(evidence)

    assert report["status"] == "passed"
    assert report["release_allowed"] is True
    assert report["environment"] == evidence["environment"]
    assert all(item["status"] == "passed" for item in report["gates"])


def test_performance_budget_excess_blocks_release() -> None:
    evidence = _performance_evidence()
    evidence["results"]["hover_reference_graph"]["measurements"]["p95_ms"] = 100.001

    report = build_performance_report(evidence)

    assert report["status"] == "blocked"
    assert "hover_reference_graph:performance_budget_exceeded" in report["reason_codes"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["environment"].pop("browser"),
        lambda value: value["environment"].update({"build": "/absolute/build"}),
        lambda value: value["environment"].update({"repetitions": 99}),
    ],
)
def test_performance_environment_rejects_incomplete_or_host_specific_values(mutation) -> None:
    evidence = _performance_evidence()
    mutation(evidence)

    with pytest.raises(
        ValueError,
        match="performance_(environment_incomplete|environment_invalid|build_invalid)",
    ):
        build_performance_report(evidence)


@pytest.mark.parametrize(
    ("gate_id", "key", "value"),
    [
        ("codecompass_warm_retrieval", "ungrounded_fixture_release_blocked", False),
        ("codecompass_warm_retrieval", "released_source_count", 1),
        ("context_budgets", "selected_discarded_reason_counts", {}),
        ("context_budgets", "rejected_overflow_count", 0),
    ],
)
def test_performance_report_rejects_incomplete_operational_proof(
    gate_id: str,
    key: str,
    value,
) -> None:
    evidence = _performance_evidence()
    evidence["results"][gate_id]["measurements"][key] = value

    report = build_performance_report(evidence)

    assert report["status"] == "blocked"
    assert f"{gate_id}:performance_budget_exceeded" in report["reason_codes"]


def test_gate_reports_do_not_contain_volatile_identity_fields() -> None:
    encoded = (canonical_bytes(build_functional_report()) + canonical_bytes(build_performance_report())).decode("utf-8")

    assert "generated_at" not in encoded
    assert "timestamp" not in encoded
    assert str(Path(__file__).resolve().parents[1]) not in encoded
    json.loads(canonical_bytes(build_functional_report()))
