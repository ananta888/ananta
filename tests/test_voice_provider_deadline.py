from __future__ import annotations

import pytest
from flask import Flask

from agent.services import app_runtime_service
from agent.services.voice_provider import VoiceProviderService


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    history: list = []
    url = "http://voice-runtime:8090/v1/audio/transcriptions"
    _payload = b'{"text":"ok","model":"vosk","duration_ms":10}'

    @classmethod
    def iter_content(cls, chunk_size: int):
        del chunk_size
        yield cls._payload

    @staticmethod
    def close() -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response] | None = None) -> None:
        self.trust_env = True
        self.calls: list[dict] = []
        self._responses = list(responses or [_Response()])

    def request(self, method: str, endpoint: str, **kwargs):
        self.calls.append({"method": method, "endpoint": endpoint, **kwargs})
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        response.url = endpoint
        return response


def _service(session: _Session | None = None, *, resolver=None) -> VoiceProviderService:
    return VoiceProviderService(
        session=session or _Session(),
        resolver=resolver or (lambda _host, _port: ("172.19.0.8",)),
    )


def test_voice_provider_disables_environment_proxies_on_injected_session() -> None:
    session = _Session()

    _service(session)

    assert session.trust_env is False


def test_base_app_config_keeps_the_voice_service_token_in_hub_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_runtime_service.settings,
        "voice_internal_service_token",
        "hub-to-voice-runtime-token",
    )

    app_config = app_runtime_service.build_base_app_config("hub")

    assert app_config["VOICE_INTERNAL_SERVICE_TOKEN"] == "hub-to-voice-runtime-token"
    assert "VOICE_INTERNAL_SERVICE_TOKEN" not in app_config.get("AGENT_CONFIG", {})


@pytest.mark.parametrize(
    "addresses",
    [
        ("8.8.8.8",),
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("172.19.0.8", "8.8.8.8"),
    ],
)
def test_voice_provider_rejects_public_loopback_link_local_and_mixed_dns_before_send(
    addresses: tuple[str, ...],
) -> None:
    session = _Session()
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_RUNTIME_ALLOWED_ORIGINS="http://voice-runtime:8090",
        VOICE_INTERNAL_SERVICE_TOKEN="internal-token",
    )

    with app.app_context(), pytest.raises(Exception) as error:
        _service(
            session,
            resolver=lambda _host, _port: addresses,
        ).transcribe(content=b"raw audio must not leave", filename="audio.wav")

    assert getattr(error.value, "code", None) == "voice.runtime_address_forbidden"
    assert session.calls == []


def test_voice_provider_pins_one_private_dns_answer_without_rebinding() -> None:
    session = _Session()
    resolutions = 0

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal resolutions
        resolutions += 1
        return ("172.19.0.9", "172.19.0.8")

    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_RUNTIME_ALLOWED_ORIGINS="http://voice-runtime:8090",
        VOICE_INTERNAL_SERVICE_TOKEN="internal-token",
    )

    with app.app_context():
        _service(session, resolver=resolver).transcribe(
            content=b"audio",
            filename="audio.wav",
        )

    assert resolutions == 1
    assert session.calls[0]["endpoint"].startswith("http://172.19.0.8:8090/")
    assert session.calls[0]["headers"]["Host"] == "voice-runtime:8090"


def test_voice_provider_bounds_connect_and_read_timeout_by_hub_deadline() -> None:
    session = _Session()
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_INTERNAL_SERVICE_TOKEN="internal-token",
        AGENT_CONFIG={"voice_runtime": {"timeout_sec": 120}},
    )

    with app.app_context():
        result = _service(session).transcribe(
            content=b"audio",
            filename="audio.wav",
            request_id="voice-deadline-test",
            deadline_seconds=0.25,
        )

    captured = session.calls[0]
    assert result["text"] == "ok"
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "http://172.19.0.8:8090/v1/audio/transcriptions"
    assert captured["timeout"] == (0.25, 0.25)
    assert captured["headers"]["X-Ananta-Deadline-Seconds"] == "0.25"
    assert captured["headers"]["X-Request-ID"] == "voice-deadline-test"
    assert captured["headers"]["Host"] == "voice-runtime:8090"
    assert captured["headers"]["X-Ananta-Internal-Token"] == "internal-token"
    assert captured["files"]["file"][1] == b"audio"
    assert captured["allow_redirects"] is False
    assert captured["stream"] is True


def test_voice_command_propagates_hub_deadline_and_request_id() -> None:
    session = _Session()
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_INTERNAL_SERVICE_TOKEN="internal-token",
        AGENT_CONFIG={"voice_runtime": {"timeout_sec": 120}},
    )

    with app.app_context():
        _service(session).voice_command(
            content=b"audio",
            filename="audio.wav",
            context={"scope": "goal"},
            request_id="voice-command-deadline-test",
            deadline_seconds=0.3,
        )

    captured = session.calls[0]
    assert captured["timeout"] == (0.3, 0.3)
    assert captured["headers"]["X-Ananta-Deadline-Seconds"] == "0.3"
    assert captured["headers"]["X-Request-ID"] == "voice-command-deadline-test"


def test_voice_provider_propagates_same_deadline_and_request_id_to_stream_calls() -> None:
    session = _Session()
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_INTERNAL_SERVICE_TOKEN="internal-token",
        AGENT_CONFIG={"voice_runtime": {"timeout_sec": 120}},
    )

    with app.app_context():
        service = _service(session)
        service.push_stream_chunk(
            runtime_session_id="runtime-stream-1",
            chunk_sequence=0,
            content=b"audio",
            request_id="voice-stream-deadline-test",
            deadline_seconds=0.4,
        )
        service.finalize_stream(
            runtime_session_id="runtime-stream-1",
            request_id="voice-stream-deadline-test",
            deadline_seconds=0.2,
        )

    assert [item["timeout"] for item in session.calls] == [(0.4, 0.4), (0.2, 0.2)]
    assert all(item["headers"]["X-Request-ID"] == "voice-stream-deadline-test" for item in session.calls)
    assert [item["headers"]["X-Ananta-Deadline-Seconds"] for item in session.calls] == ["0.4", "0.2"]
    assert all(item["allow_redirects"] is False and item["stream"] is True for item in session.calls)


def test_voice_provider_forwards_exact_stream_audio_duration_budget() -> None:
    session = _Session()
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_INTERNAL_SERVICE_TOKEN="internal-token",
        AGENT_CONFIG={"voice_runtime": {"timeout_sec": 120}},
    )

    with app.app_context():
        _service(session).create_stream(
            filename="audio.webm",
            language="de",
            media_type="audio/webm",
            deadline_seconds=0.5,
            max_audio_seconds=0.125,
            requested_session_id=f"vs_{'A' * 32}",
            request_id="voice-stream-budget-test",
        )

    captured = session.calls[0]
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "http://172.19.0.8:8090/v1/audio/streams"
    assert captured["json"]["max_audio_seconds"] == 0.125
    assert captured["json"]["requested_session_id"] == f"vs_{'A' * 32}"
    assert captured["json"]["media_type"] == "audio/webm"
    assert captured["headers"]["X-Request-ID"] == "voice-stream-budget-test"


def test_voice_provider_rejects_unallowlisted_origin_before_sending() -> None:
    session = _Session()
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://public.example:8080",
        VOICE_RUNTIME_ALLOWED_ORIGINS="http://voice-runtime:8090",
    )

    with app.app_context(), pytest.raises(Exception) as error:
        _service(session).transcribe(content=b"audio", filename="audio.wav")

    assert getattr(error.value, "code", None) == "voice.runtime_origin_forbidden"
    assert session.calls == []


def test_voice_provider_rejects_redirect_and_bounded_oversized_response() -> None:
    class _Redirect(_Response):
        status_code = 307

    class _Oversized(_Response):
        _payload = b"{" + b"x" * 2048 + b"}"

    session = _Session([_Redirect(), _Oversized()])
    service = _service(session)
    app = Flask(__name__)
    app.config.update(
        VOICE_RUNTIME_URL="http://voice-runtime:8090",
        VOICE_RUNTIME_ALLOWED_ORIGINS="http://voice-runtime:8090",
        VOICE_RUNTIME_MAX_RESPONSE_BYTES=1024,
    )

    with app.app_context(), pytest.raises(Exception) as redirect_error:
        service.transcribe(content=b"audio", filename="audio.wav")
    assert getattr(redirect_error.value, "code", None) == "voice.redirect_forbidden"

    with app.app_context(), pytest.raises(Exception) as size_error:
        service.transcribe(content=b"audio", filename="audio.wav")
    assert getattr(size_error.value, "code", None) == "voice.response_too_large"
