import io
import socket
import urllib.error
from unittest.mock import patch

from agent.services.chat_provider_probe import ChatProviderProbe


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.payload


def test_discovers_models_and_checks_requested_model():
    with patch("urllib.request.urlopen", return_value=_Response(b'{"data":[{"id":"m2"},{"id":"m1"}]}')):
        result = ChatProviderProbe().probe(
            {"chat_backend": "lmstudio", "chat_backend_api_base": "http://localhost:1234", "chat_backend_model": "m1"}
        )
    assert result.ok is True
    assert result.models == ("m1", "m2")
    assert result.model_found is True


def test_probe_has_stable_safe_error_codes_and_never_resolves_secret_in_browser_path():
    assert (
        ChatProviderProbe().probe({"chat_backend": "unknown", "chat_backend_api_base": "http://x"}).error_code
        == "unsupported_provider"
    )
    assert (
        ChatProviderProbe()
        .probe(
            {
                "chat_backend": "lmstudio",
                "chat_backend_api_base": "http://x",
                "chat_backend_credential_ref": "secret://x",
            }
        )
        .error_code
        == "unsupported_credential_reference"
    )
    with patch("urllib.request.urlopen", side_effect=socket.timeout()):
        assert (
            ChatProviderProbe()
            .probe({"chat_backend": "lmstudio", "chat_backend_api_base": "http://localhost:1"})
            .error_code
            == "provider_timeout"
        )
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("http://x", 401, "no", {}, io.BytesIO())):
        assert (
            ChatProviderProbe()
            .probe({"chat_backend": "lmstudio", "chat_backend_api_base": "http://localhost:1"})
            .error_code
            == "auth_failed"
        )
