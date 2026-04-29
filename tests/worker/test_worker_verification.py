from __future__ import annotations

from worker.core.verification import (
    build_verification_artifact,
    validate_worker_schema_or_degraded,
    validate_worker_schema_payload,
)


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


def test_schema_validation_passes_for_valid_worker_request() -> None:
    validate_worker_schema_payload(
        schema_name="worker_execution_request.v1",
        payload={
            "schema": "worker_execution_request.v1",
            "task_id": "AW-T11",
            "goal_id": "G1",
            "trace_id": "tr-11",
            "capability_id": "worker.patch.propose",
            "mode": "patch_propose",
            "context_envelope_ref": {
                "context_bundle_id": "ctx-11",
                "context_hash": "ctxhash-11",
                "retrieval_refs": [{"source_id": "repo", "path": "README.md", "reason": "scope"}],
            },
            "policy_decision_ref": {"decision_id": "p-11", "decision": "allow", "policy_version": "v1"},
            "workspace_constraints_ref": {"constraint_id": "wc-11"},
            "requested_outputs": ["patch_artifact"],
        },
    )


def test_schema_validation_returns_degraded_payload_on_invalid_input() -> None:
    ok, degraded = validate_worker_schema_or_degraded(
        schema_name="worker_execution_request.v1",
        payload={
            "schema": "worker_execution_request.v1",
            "task_id": "AW-T11",
        },
        direction="ingress",
    )
    assert ok is False
    assert (degraded or {}).get("state") == "schema_invalid"
    assert (degraded or {}).get("machine_reason") == "schema_invalid"
