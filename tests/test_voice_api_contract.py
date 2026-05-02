from unittest.mock import patch


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
