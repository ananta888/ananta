from agent.routes.tasks.autopilot_tick_engine import _ensure_llm_profile_snapshot


def test_ensure_llm_profile_snapshot_adds_synthetic_entry_when_missing():
    snapshot = {"backend": "orchestrator"}
    out = _ensure_llm_profile_snapshot(
        snapshot=snapshot,
        strategy_id="tool_calling_llm",
        model_meta={"runtime_provider": "ollama", "selected_model": "ananta-default:latest"},
    )
    cli = out.get("cli_result") or {}
    prof = list(cli.get("llm_call_profile") or [])
    assert prof
    first = prof[0]
    assert first.get("source") == "orchestrator_synthetic"
    assert first.get("estimated") is True
    assert first.get("provider") == "ollama"
    assert first.get("model") == "ananta-default:latest"


def test_ensure_llm_profile_snapshot_preserves_existing_profile():
    existing = {
        "cli_result": {
            "llm_call_profile": [
                {"source": "model_invocation_service", "estimated": False, "success": True}
            ]
        }
    }
    out = _ensure_llm_profile_snapshot(snapshot=existing, strategy_id="x", model_meta=None)
    prof = list((out.get("cli_result") or {}).get("llm_call_profile") or [])
    assert len(prof) == 1
    assert prof[0]["source"] == "model_invocation_service"
    assert prof[0]["estimated"] is False
