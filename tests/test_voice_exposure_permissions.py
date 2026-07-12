from __future__ import annotations

from unittest.mock import Mock, patch

from agent.services.exposure_policy_service import ExposurePolicyService


def test_voice_operations_have_separate_user_and_agent_permissions() -> None:
    service = ExposurePolicyService()
    cfg = {
        "exposure_policy": {
            "voice": {
                "enabled": True,
                "allow_agent_auth": True,
                "allow_user_auth": True,
                "user_operations": ["capabilities"],
                "agent_operations": ["transcribe"],
            }
        }
    }

    user_capabilities = service.evaluate_voice_access(
        cfg=cfg,
        is_agent_auth=False,
        is_user_auth=True,
        is_admin=False,
        operation="capabilities",
    )
    user_transcribe = service.evaluate_voice_access(
        cfg=cfg,
        is_agent_auth=False,
        is_user_auth=True,
        is_admin=False,
        operation="transcribe",
    )
    agent_transcribe = service.evaluate_voice_access(
        cfg=cfg,
        is_agent_auth=True,
        is_user_auth=False,
        is_admin=True,
        operation="transcribe",
    )

    assert user_capabilities.allowed is True
    assert user_transcribe.reason == "voice_user_operation_disabled"
    assert agent_transcribe.allowed is True


def test_voice_model_management_is_admin_only_and_distinct_from_status() -> None:
    service = ExposurePolicyService()

    denied = service.evaluate_voice_access(
        cfg={},
        is_agent_auth=False,
        is_user_auth=True,
        is_admin=False,
        operation="model_load",
    )
    allowed = service.evaluate_voice_access(
        cfg={},
        is_agent_auth=False,
        is_user_auth=True,
        is_admin=True,
        operation="model_load",
    )

    assert denied.reason == "voice_operation_admin_required"
    assert allowed.allowed is True
    assert "model_load" in allowed.policy["admin_only_operations"]


def test_voice_route_keeps_valid_user_identity_when_agent_auth_is_disabled(
    app,
    client,
    user_auth_header,
) -> None:
    app.config["AGENT_TOKEN"] = None
    provider = Mock()
    provider.health.return_value = {"ok": True, "status": "ready"}
    provider.models.return_value = []
    provider.capability_catalog.return_value = []

    with patch("agent.routes.voice.get_voice_provider_service", return_value=provider):
        response = client.get("/v1/voice/capabilities", headers=user_auth_header)

    assert response.status_code == 200
    assert response.get_json()["data"]["available"] is True
    assert provider.health.call_count == 1
