import pytest
from agent.models import ApiErrorResponseContract

def test_auth_error_format(client):
    """Prüft das Format von Authentifizierungsfehlern (401)."""
    response = client.get("/assistant/read-model", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401
    assert response.is_json

    # Validierung gegen das Contract-Modell
    error_data = ApiErrorResponseContract(**response.json)
    assert error_data.status == "error"
    assert "unauthorized" in error_data.message.lower() or "token" in error_data.message.lower()

def test_policy_blocking_error_format(client, user_auth_header):
    """Prüft das Format von Policy-Blockierungen (403)."""
    # OpenAI-Endpunkt erfordert Admin für User-JWTs standardmäßig
    response = client.get("/v1/models", headers=user_auth_header)
    assert response.status_code == 403
    assert response.is_json

    error_data = ApiErrorResponseContract(**response.json)
    assert error_data.status == "error"
    assert "forbidden" in error_data.message.lower()
    assert "data" in response.json
    assert "details" in response.json["data"]

def test_not_found_error_format(client, auth_header):
    """Prüft das Format von 404 Fehlern."""
    # Ziel: Ein Endpunkt, der sicher ein JSON-404 wirft
    response = client.get("/goals/non-existent-goal-id-123456789", headers=auth_header)
    assert response.status_code == 404
    if response.is_json:
        error_data = ApiErrorResponseContract(**response.json)
        assert error_data.status == "error"
        assert "not_found" in error_data.message.lower()

def test_validation_error_format(client, auth_header):
    """Prüft das Format von Validierungsfehlern (400/422)."""
    # Ungültige Daten an einen Endpunkt senden
    response = client.post("/goals", json={"title": ""}, headers=auth_header)
    assert response.status_code in (400, 422)
    assert response.is_json

    error_data = ApiErrorResponseContract(**response.json)
    assert error_data.status == "error"
