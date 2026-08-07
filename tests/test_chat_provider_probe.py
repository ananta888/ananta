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


def test_openai_probe_uses_server_side_default_credential(monkeypatch):
    from agent.services import chat_provider_probe as probe_module

    monkeypatch.setattr(probe_module.settings, "openai_url", "https://api.openai.com/v1/chat/completions")
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}),
        patch(
            "urllib.request.urlopen",
            return_value=_Response(b'{"data":[{"id":"gpt-4o-mini"}]}'),
        ) as urlopen,
    ):
        result = ChatProviderProbe().probe(
            {
                "chat_backend": "openai",
                "chat_backend_api_base": "https://api.openai.com:443/v1/",
                "chat_backend_model": "gpt-4o-mini",
            }
        )

    request = urlopen.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer test-openai-key"
    assert request.full_url == "https://api.openai.com/v1/models"
    assert result.ok is True
    assert result.model_found is True


def test_openai_probe_rejects_foreign_endpoint_before_resolving_or_sending_credential(monkeypatch):
    from agent.services import chat_provider_probe as probe_module

    monkeypatch.setattr(probe_module.settings, "openai_url", "https://api.openai.com/v1/chat/completions")
    for api_base in (
        "https://api.openai.com.attacker.invalid/v1",
        "https://api.openai.com:444/v1",
    ):
        for credential_ref in ("", "env://OPENAI_API_KEY"):
            with (
                patch.dict("os.environ", {"OPENAI_API_KEY": "must-not-leave"}),
                patch("urllib.request.urlopen") as urlopen,
            ):
                result = ChatProviderProbe().probe(
                    {
                        "chat_backend": "openai",
                        "chat_backend_api_base": api_base,
                        "chat_backend_model": "gpt-4o-mini",
                        "chat_backend_credential_ref": credential_ref,
                    }
                )

            assert result.error_code == "openai_endpoint_credential_mismatch"
            urlopen.assert_not_called()


def test_openai_probe_rejects_unimplemented_custom_credential_reference_before_io(monkeypatch):
    from agent.services import chat_provider_probe as probe_module

    monkeypatch.setattr(probe_module.settings, "openai_url", "https://api.openai.com/v1/chat/completions")
    with patch("urllib.request.urlopen") as urlopen:
        result = ChatProviderProbe().probe(
            {
                "chat_backend": "openai",
                "chat_backend_api_base": "https://api.openai.com/v1",
                "chat_backend_credential_ref": "env://OTHER_API_KEY",
            }
        )

    assert result.error_code == "unsupported_credential_reference"
    urlopen.assert_not_called()


def test_openai_probe_accepts_normalized_server_configured_endpoint(monkeypatch):
    from agent.services import chat_provider_probe as probe_module

    monkeypatch.setattr(probe_module.settings, "openai_url", "https://gateway.example.test:8443/v1/chat/completions")
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "server-key"}),
        patch("urllib.request.urlopen", return_value=_Response(b'{"data":[]}')) as urlopen,
    ):
        result = ChatProviderProbe().probe(
            {
                "chat_backend": "openai",
            }
        )

    assert result.ok is True
    assert urlopen.call_args.args[0].full_url == "https://gateway.example.test:8443/v1/models"


def test_openai_probe_rejects_http_even_when_server_configured(monkeypatch):
    from agent.services import chat_provider_probe as probe_module

    monkeypatch.setattr(probe_module.settings, "openai_url", "http://gateway.example.test/v1/chat/completions")
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "must-not-leave"}),
        patch("urllib.request.urlopen") as urlopen,
    ):
        result = ChatProviderProbe().probe({"chat_backend": "openai"})

    assert result.error_code == "invalid_endpoint"
    urlopen.assert_not_called()


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
    with patch("urllib.request.urlopen") as urlopen:
        result = ChatProviderProbe().probe(
            {
                "chat_backend": "lmstudio",
                "chat_backend_api_base": "https://api.openai.com/v1",
                "chat_backend_credential_ref": "env://OPENAI_API_KEY",
            }
        )
    assert result.error_code == "unsupported_credential_reference"
    urlopen.assert_not_called()
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
