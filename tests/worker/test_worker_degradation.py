from __future__ import annotations

from worker.core.degraded import build_degraded_state


def test_degraded_states_cover_major_failure_modes() -> None:
    states = {
        "unavailable_model": "unavailable_model",
        "unavailable_external_tool": "unavailable_external_tool",
        "missing_git_repo": "missing_git_repo",
        "denied_policy": "policy_denied",
        "missing_approval": "hub_approval_token_missing",
        "schema_invalid": "schema_invalid",
        "budget_exhausted": "budget_exhausted",
        "unsafe_command": "unsafe_command",
    }
    for state, expected_reason in states.items():
        payload = build_degraded_state(state=state, machine_reason=state, details={"source": "test"})
        assert payload["status"] == "degraded"
        assert payload["state"] == state
        assert payload["machine_reason"] == expected_reason


def test_unknown_degraded_state_falls_back_to_known_state() -> None:
    payload = build_degraded_state(state="unknown", machine_reason="x")
    assert payload["state"] == "unavailable_external_tool"
