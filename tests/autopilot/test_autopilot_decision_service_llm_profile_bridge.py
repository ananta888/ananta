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


def test_build_proposal_snapshot_preserves_model_recovery_signal():
    svc = AutopilotDecisionService()
    signal = {
        "schema": "model_recovery_signal.v1",
        "state": "exhausted",
        "terminal": True,
        "reason_code": "model_fallback_exhausted",
        "terminal_reason": "schema_validation_failed",
        "attempt_count": 4,
        "attempted_profile_ids": ["phi", "gemma"],
        "fallback_decisions": [],
        "llm_calls": [],
        "strategy_failures": [],
    }

    snap = svc.build_proposal_snapshot(
        {
            "reason": "llm_required_but_unavailable",
            "metadata": {
                "model_recovery_signal": signal,
                "fallback_decisions": [{"reason": "candidate_chain_exhausted"}],
            },
        }
    )

    assert snap["model_recovery_signal"]["schema"] == signal["schema"]
    assert snap["model_recovery_signal"]["state"] == "exhausted"
    assert snap["model_recovery_signal"]["terminal_reason"] == "schema_validation_failed"
    assert snap["model_recovery_signal"]["attempted_profile_ids"] == ["phi", "gemma"]
    assert snap["fallback_decisions"] == [{"reason": "candidate_chain_exhausted"}]


def test_build_proposal_snapshot_preserves_rejected_terminal_signal_flag():
    snapshot = AutopilotDecisionService().build_proposal_snapshot(
        {
            "reason": "provider denied",
            "metadata": {
                "model_recovery_signal": {
                    "schema": "model_recovery_signal.v1",
                    "state": "exhausted",
                    "terminal": True,
                    "reason_code": "model_fallback_exhausted",
                    "terminal_reason": (
                        "provider_attempt_plan_sequence_denied"
                    ),
                    "fallback_decisions": [],
                    "llm_calls": [],
                }
            },
        }
    )

    assert snapshot["terminal_model_recovery_signal_seen"] is True
    assert "model_recovery_signal" not in snapshot
