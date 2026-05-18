from agent.services.task_artifact_completion_gate_service import TaskArtifactCompletionGateService


def test_gate_decide_completed_on_valid_manifest():
    svc = TaskArtifactCompletionGateService()
    status, decision = svc.decide(
        task_id="t1",
        collection_result={
            "manifest_valid": True,
            "artifacts": [{"artifact_id": "a1", "relative_path": "app.py", "_exists": True, "required": True}],
            "errors": [],
            "warnings": [],
            "synthesized": False,
            "manifest_id": "m1",
        },
    )
    assert status in {"completed", "needs_review"}
    assert decision is not None


def test_gate_event_details_contains_core_fields():
    svc = TaskArtifactCompletionGateService()

    class _D:
        decision = "completed"
        reason_codes = ["artifact_manifest_verified"]
        advisory_parse_status = None
        artifact_ids = ["a1"]
        manifest_id = "m1"

    details = svc.event_details(decision=_D(), extra={"x": 1})
    assert details["completion_decision"] == "completed"
    assert details["artifact_ids"] == ["a1"]
    assert details["x"] == 1
