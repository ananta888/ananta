from agent.services.autopilot_decision_service import AutopilotDecisionService


def test_build_proposal_snapshot_uses_top_level_metadata_llm_profile():
    svc = AutopilotDecisionService()
    data = {
        "reason": "ok",
        "command": "echo ok",
        "metadata": {
            "llm_call_profile": [
                {"source": "model_invocation_service", "estimated": False, "success": True}
            ]
        },
    }

    snap = svc.build_proposal_snapshot(data)
    profile = ((snap.get("cli_result") or {}).get("llm_call_profile")) or []
    assert len(profile) == 1
    assert profile[0]["source"] == "model_invocation_service"
    assert profile[0]["estimated"] is False


def test_build_proposal_snapshot_uses_wrapped_proposal_metadata_llm_profile():
    svc = AutopilotDecisionService()
    data = {
        "proposal": {
            "command": "echo ok",
            "reason": "wrapped",
            "metadata": {
                "llm_call_profile": [
                    {"source": "llm_integration", "estimated": False, "success": True}
                ]
            },
        }
    }

    snap = svc.build_proposal_snapshot(data)
    profile = ((snap.get("cli_result") or {}).get("llm_call_profile")) or []
    assert len(profile) == 1
    assert profile[0]["source"] == "llm_integration"
    assert profile[0]["estimated"] is False
