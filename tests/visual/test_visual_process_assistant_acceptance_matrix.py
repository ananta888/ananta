from __future__ import annotations

import json

from scripts.generate_visual_process_assistant_acceptance_matrix import (
    OUTPUT,
    build_matrix,
    main,
)


def test_missing_suite_is_reported_fail_closed_instead_of_crashing() -> None:
    functional = {
        "schema": "ananta.visual-process-assistant-gate-report.v1",
        "release_allowed": False,
        "suites": [],
    }
    performance = {
        "schema": "ananta.visual-process-assistant-performance-report.v1",
        "release_allowed": False,
        "status": "blocked",
        "environment": {},
        "gates": [],
    }

    matrix = build_matrix(functional, performance)

    assert matrix["status"] == "blocked"
    assert matrix["release_allowed"] is False
    assert "required_gate_evidence_missing" in matrix["reason_codes"]


def test_tampered_release_flag_blocks_rollout_policy_criterion() -> None:
    functional = json.loads(open("artifacts/test-gates/visual-process-assistant.json", encoding="utf-8").read())
    performance = json.loads(
        open(
            "artifacts/test-gates/visual-process-assistant-performance.json",
            encoding="utf-8",
        ).read()
    )
    functional["release_allowed"] = False
    functional["status"] = "blocked"

    matrix = build_matrix(functional, performance)
    qa003 = next(item for item in matrix["tasks"] if item["task_id"] == "VPA-QA-003")
    release_policy = next(item for item in qa003["criteria"] if item["criterion_id"] == "VPA-QA-003-AC5")

    assert matrix["release_allowed"] is False
    assert release_policy["status"] == "blocked"
    assert release_policy["reason_code"] == "release_policy_not_fail_closed"


def test_committed_acceptance_matrix_is_deterministic_and_fully_automatic() -> None:
    functional = json.loads(open("artifacts/test-gates/visual-process-assistant.json", encoding="utf-8").read())
    performance = json.loads(
        open(
            "artifacts/test-gates/visual-process-assistant-performance.json",
            encoding="utf-8",
        ).read()
    )

    matrix = build_matrix(functional, performance)
    tasks = {item["task_id"]: item for item in matrix["tasks"]}

    assert matrix["release_allowed"] is True
    assert matrix["status"] == "passed"
    assert tasks["VPA-QA-001"]["status"] == "passed"
    assert tasks["VPA-QA-002"]["status"] == "passed"
    assert tasks["VPA-QA-003"]["status"] == "passed"
    assert all(item["status"] == "passed" for item in tasks["VPA-QA-003"]["criteria"])
    assert matrix["reason_codes"] == []
    assert main(["--check"]) == 0
    assert OUTPUT.is_file()
