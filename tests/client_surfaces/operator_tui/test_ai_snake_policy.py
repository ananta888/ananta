from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_policy import apply_policy_to_payload, evaluate_policy


def test_worker_request_keeps_notes_metadata_only_without_release() -> None:
    payload, decision = apply_policy_to_payload(
        {
            "observation_summary": {"notes_active": True, "facts": ["x"]},
            "notes_context": {"raw": "secret text"},
        },
        boundary="worker_request",
        notes_released=False,
    )
    assert decision.allowed is True
    assert decision.reason_code == "notes_metadata_only"
    assert "notes_context" not in payload
    assert payload["observation_summary"]["notes_active"] is True


def test_external_provider_is_denied() -> None:
    decision = evaluate_policy(
        boundary="external_provider",
        notes_released=True,
        selected_artifact_allowed=True,
        external_provider=True,
    )
    assert decision.allowed is False
    assert decision.reason_code == "external_provider_denied"


def test_artifact_denied_blocks_worker_payload() -> None:
    payload, decision = apply_policy_to_payload(
        {"quick_prediction": {"predicted_intent": "artifact_explain"}},
        boundary="worker_request",
        notes_released=True,
        selected_artifact_allowed=False,
    )
    assert decision.allowed is False
    assert payload["blocked"] is True
