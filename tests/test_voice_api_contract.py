from unittest.mock import patch
from io import BytesIO

from agent.services.voice_provider import VoiceProviderError


def test_voice_transcribe_requires_file(client, admin_token):
    res = client.post("/v1/voice/transcribe", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 400
    payload = res.json["data"]["error"]
    assert payload["code"] == "validation.missing_file"


def test_voice_capabilities_degraded_when_runtime_unavailable(client, admin_token):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.health.side_effect = RuntimeError("down")
        res = client.get("/v1/voice/capabilities", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    data = res.json["data"]
    assert data["available"] is False
    assert data["health"]["status"] == "unavailable"


def test_provider_catalog_contains_voice_runtime_entry(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.config.providers.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.models.return_value = [{"id": "voxtral", "capabilities": ["transcription"]}]
        provider_factory.return_value.health.return_value = {"ok": True, "status": "ok"}
        res = client.get("/providers/catalog", headers=headers)
    assert res.status_code == 200
    providers = (res.json.get("data") or {}).get("providers") or []
    voice = next((item for item in providers if item.get("capabilities", {}).get("provider_type") == "local_voice_runtime"), None)
    assert voice is not None
    assert voice["available"] is True


def test_voice_goal_requires_explicit_approval(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = {"text": "create goal", "transcript": "create goal"}
        res = client.post(
            "/v1/voice/goal",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "create_tasks": "false"},
            content_type="multipart/form-data",
        )
    assert res.status_code == 403
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "policy_denied"


def test_voice_capabilities_blocked_when_policy_disabled(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.voice.get_exposure_policy_service") as policy_factory:
        policy_factory.return_value.evaluate_voice_access.return_value = type(
            "Decision",
            (),
            {
                "allowed": False,
                "reason": "voice_exposure_disabled",
                "auth_source": "user_jwt",
                "policy": {"emit_audit_events": False},
            },
        )()
        res = client.get("/v1/voice/capabilities", headers=headers)
    assert res.status_code == 403
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "policy_denied"


def test_voice_capabilities_privacy_stays_fail_closed(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.health.return_value = {"ok": True, "status": "ok"}
        provider_factory.return_value.models.return_value = [{"id": "voxtral"}]
        with patch("agent.routes.voice._store_audio_enabled", return_value=True):
            res = client.get("/v1/voice/capabilities", headers=headers)
    assert res.status_code == 200
    privacy = ((res.json.get("data") or {}).get("privacy") or {})
    assert privacy.get("store_audio_requested") is True
    assert privacy.get("store_audio_effective") is False
    assert privacy.get("raw_audio_persisted") is False


def test_voice_transcribe_propagates_provider_error_shape(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = VoiceProviderError(
            code="voice.runtime_unavailable",
            message="voice runtime unavailable",
            status_code=503,
            retriable=True,
        )
        res = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm")},
            content_type="multipart/form-data",
        )
    assert res.status_code == 503
    error = ((res.json.get("data") or {}).get("error") or {})
    assert error.get("code") == "voice.runtime_unavailable"
    assert error.get("retriable") is True


def test_voice_command_passes_parsed_context_to_provider(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = {
            "text": "create repo health goal",
            "transcript": "create repo health goal",
            "tool_intent": {"type": "voice_command", "confidence": 0.8},
        }
        res = client.post(
            "/v1/voice/command",
            headers=headers,
            data={
                "file": (BytesIO(b"audio"), "sample.webm"),
                "command_context": '{"scope":"release","priority":"high"}',
            },
            content_type="multipart/form-data",
        )
        _, kwargs = provider_factory.return_value.voice_command.call_args
    assert res.status_code == 200
    assert kwargs.get("context") == {"scope": "release", "priority": "high"}


def test_voice_goal_rejects_empty_transcript_even_if_approved(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = {"text": "", "transcript": ""}
        res = client.post(
            "/v1/voice/goal",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "approved": "true"},
            content_type="multipart/form-data",
        )
    assert res.status_code == 422
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "voice.empty_transcript"
