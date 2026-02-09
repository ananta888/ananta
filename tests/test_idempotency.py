import pytest
from unittest.mock import patch, MagicMock
from agent.llm_integration import generate_text
from agent.config import settings

@pytest.fixture
def mock_post():
    with patch("agent.common.http.requests.Session.post") as mock:
        yield mock

def test_idempotency_key_consistency_on_retries(mock_post):
    """
    Testet, ob der Idempotency-Key über Retries hinweg gleich bleibt.
    """
    # Setup: Erster Aufruf schlägt mit 500 fehl, zweiter ist erfolgreich
    mock_response_fail = MagicMock()
    mock_response_fail.status_code = 500
    mock_response_fail.raise_for_status.side_effect = Exception("Transient Error")
    
    mock_response_success = MagicMock()
    mock_response_success.status_code = 200
    mock_response_success.json.return_value = {
        "choices": [{"message": {"content": "Erfolgreiche Antwort"}}]
    }
    
    mock_post.side_effect = [mock_response_fail, mock_response_success]
    
    # Einstellungen anpassen für schnellen Test
    with patch.object(settings, "retry_count", 1):
        with patch.object(settings, "retry_backoff", 0.1):
            # Wir nutzen OpenAI als Provider für diesen Test
            res = generate_text("Hallo", provider="openai")
            
    # Verifikation
    assert res == "Erfolgreiche Antwort"
    assert mock_post.call_count == 2
    
    # Prüfen, ob der Idempotency-Key in beiden Aufrufen identisch ist
    first_call_headers = mock_post.call_args_list[0][1]["headers"]
    second_call_headers = mock_post.call_args_list[1][1]["headers"]
    
    key1 = first_call_headers.get("Idempotency-Key")
    key2 = second_call_headers.get("Idempotency-Key")
    
    assert key1 is not None
    assert key2 is not None
    assert key1 == key2
    print(f"\nIdempotency-Key verifiziert: {key1}")

def test_idempotency_key_sent_to_provider(mock_post):
    """
    Testet, ob der Idempotency-Key korrekt im Header an den Provider gesendet wird.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Test Antwort"}}]
    }
    mock_post.return_value = mock_response
    
    generate_text("Test", provider="openai")
    
    assert mock_post.called
    headers = mock_post.call_args[1]["headers"]
    assert "Idempotency-Key" in headers
    assert len(headers["Idempotency-Key"]) > 0
