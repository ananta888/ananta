from __future__ import annotations

from worker.core.verification import build_verification_artifact


def test_verification_reports_passed_for_successful_test_result() -> None:
    artifact = build_verification_artifact(
        task_id="AW-T23",
        test_results=[
            {
                "command": "pytest -q",
                "exit_code": 0,
                "status": "passed",
                "stdout_ref": "out.log",
                "stderr_ref": "err.log",
            }
        ],
        patch_artifact={"patch_hash": "abc"},
    )
    assert artifact["schema"] == "verification_artifact.v1"
    assert artifact["status"] == "passed"
    assert any(check["check_id"] == "patch_artifact_presence" for check in artifact["checks"])
    assert "patch:abc" in artifact["evidence_refs"]


def test_verification_reports_failed_for_failing_test_result() -> None:
    artifact = build_verification_artifact(
        task_id="AW-T23",
        test_results=[{"command": "pytest -q", "exit_code": 1, "status": "failed", "stdout_ref": "out.log", "stderr_ref": "err.log"}],
    )
    assert artifact["status"] == "failed"
    assert artifact["checks"][0]["status"] == "failed"


def test_verification_skips_when_no_test_results() -> None:
    artifact = build_verification_artifact(task_id="AW-T23", test_results=[])
    assert artifact["status"] == "skipped"
    assert artifact["checks"][0]["check_id"] == "verification_skipped"
