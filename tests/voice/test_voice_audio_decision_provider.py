from __future__ import annotations

import http.client
import http.server
import io
import json
import logging
import socket
import threading
import time
import wave

import pytest

from agent.services.audio_decision_hub_gate import (
    AudioDecisionKind,
    HubAction,
    PolicyVerdict,
    classify_field,
    gate_audio_decision,
)
from voice_runtime.backends.audio_decision import (
    API_VERSION,
    AudioDecisionConfig,
    AudioDecisionConfigurationError,
    AudioDecisionProvider,
    DecisionOutcome,
    build_audio_decision_provider,
    parse_decision_response,
)
from voice_runtime.execution_control import BackendCancellationToken

KEY = "test-decision-key-0123456789"
SECRET_TRANSCRIPT = "open the kitchen window please"


def _wav(duration_ms: int = 1000, *, rate: int = 16_000, amplitude: int = 800) -> bytes:
    frames = duration_ms * rate // 1000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        sample = amplitude.to_bytes(2, "little", signed=True)
        output.writeframes((sample + (-amplitude).to_bytes(2, "little", signed=True)) * (frames // 2))
    return buffer.getvalue()


def _config(**overrides) -> AudioDecisionConfig:
    values = {"enabled": True, "url": "http://127.0.0.1:8081", "api_key": KEY, "timeout_ms": 3000}
    values.update(overrides)
    return AudioDecisionConfig(**values)


def _result(fields: dict, *, profile: str = "home-control-en", fallback: dict | None = None, **extra) -> dict:
    payload = {
        "object": "audio.decision",
        "api_version": API_VERSION,
        "model": "base/v51864/l6/f1",
        "profile": {"id": profile, "version": "0.3.0", "status": "beta"},
        "language": "en",
        "no_speech_prob": 0.01,
        "decision": {name: item.get("value") for name, item in fields.items()},
        "fields": fields,
        "fallback": fallback or {"used": False, "system2_required": False},
        "usage": {"audio_ms": 1000, "n_encode": 1, "n_encode_total": 1},
        "timings": {"total_ms": 44.0},
    }
    payload.update(extra)
    return payload


def _field(status: str, value=None, *, calibrated: bool = False, confidence=None, reasons=(), **extra) -> dict:
    item = {
        "status": status,
        "value": value,
        "type": "enum",
        "probability": 0.97 if status == "ok" else 0.4,
        "coverage": 0.6,
        "calibrated": calibrated,
        "confidence": confidence,
        "reasons": list(reasons),
    }
    item.update(extra)
    return item


def _error(code: str, message: str = "x") -> dict:
    return {"object": "error", "api_version": API_VERSION, "error": {"code": code, "message": message}}


class FakeTransport:
    def __init__(
        self,
        status: int | None = 200,
        payload=None,
        *,
        raw: bytes | None = None,
        exc: Exception | None = None,
    ):
        self.status = status
        self.body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
        self.exc = exc
        self.calls: list[dict] = []

    def request(self, method, url, *, body, headers, timeout_s):
        self.calls.append(
            {"method": method, "url": url, "body": body, "headers": dict(headers), "timeout_s": timeout_s}
        )
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


def _request_json(call: dict) -> dict:
    body: bytes = call["body"]
    marker = b'name="request"\r\n\r\n'
    start = body.index(marker) + len(marker)
    return json.loads(body[start : body.index(b"\r\n", start)])


ALLOWED = ("home-control-en", "speech-commands-en", "intent-semantic-experimental")


def _decide(transport: FakeTransport, *, profile: str = "home-control-en", **kwargs):
    provider = AudioDecisionProvider(_config(profiles=ALLOWED), transport=transport)
    return provider.decide(filename="clip.wav", content=kwargs.pop("content", _wav()), profile=profile, **kwargs)


def _allow(_proposal):
    return PolicyVerdict.ALLOW


# --- configuration / feature off -------------------------------------------------------------


def test_feature_is_off_by_default_and_reads_nothing(tmp_path):
    missing = tmp_path / "does-not-exist"
    config = AudioDecisionConfig.from_env(
        {"VOICE_AUDIO_DECISION_API_KEY_FILE": str(missing), "VOICE_AUDIO_DECISION_URL": "ftp://nope"}
    )
    assert config.enabled is False
    assert build_audio_decision_provider(config) is None


def test_feature_off_voice_runtime_starts_without_contacting_the_service(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("VOICE_AUDIO_DECISION_"):
            monkeypatch.delenv(name)
    contacted: list[object] = []

    def forbidden_connect(*args, **kwargs):
        contacted.append(args)
        raise AssertionError("audio decision service must not be contacted when the feature is off")

    monkeypatch.setattr(socket, "create_connection", forbidden_connect)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden_connect)

    from voice_runtime.app import create_app
    from voice_runtime.config import VoiceRuntimeConfig

    app = create_app(VoiceRuntimeConfig())
    assert app.config["voice_runtime_audio_decision"] is None
    assert contacted == []


def test_enabled_requires_api_key_and_local_or_https_url(tmp_path):
    base = {"VOICE_AUDIO_DECISION_ENABLED": "true", "VOICE_AUDIO_DECISION_API_KEY": KEY}
    with pytest.raises(AudioDecisionConfigurationError):
        AudioDecisionConfig.from_env({"VOICE_AUDIO_DECISION_ENABLED": "true"})
    for name, value in (
        ("VOICE_AUDIO_DECISION_URL", "http://example.com:8081"),
        ("VOICE_AUDIO_DECISION_URL", "http://user:pw@127.0.0.1:8081"),
        ("VOICE_AUDIO_DECISION_TIMEOUT_MS", "10"),
        ("VOICE_AUDIO_DECISION_TIMEOUT_MS", "abc"),
        ("VOICE_AUDIO_DECISION_PROFILES", "Bad Id"),
        ("VOICE_AUDIO_DECISION_API_KEY", "short"),
    ):
        with pytest.raises(AudioDecisionConfigurationError):
            AudioDecisionConfig.from_env({**base, name: value})
    config = AudioDecisionConfig.from_env(
        {
            "VOICE_AUDIO_DECISION_ENABLED": "true",
            "VOICE_AUDIO_DECISION_API_KEY": KEY,
            "VOICE_AUDIO_DECISION_URL": "http://audio-decision:8081",
            "VOICE_AUDIO_DECISION_TIMEOUT_MS": "1500",
            "VOICE_AUDIO_DECISION_PROFILES": "speech-commands-en, home-control-en",
        }
    )
    assert config.profiles == ("speech-commands-en", "home-control-en")
    assert config.timeout_ms == 1500
    assert KEY not in repr(config)


def test_api_key_is_read_from_secret_store_file_only(tmp_path):
    secret = tmp_path / "secrets" / "audio-decision-key"
    secret.parent.mkdir()
    secret.write_text(KEY + "\n", encoding="utf-8")
    env = {"VOICE_AUDIO_DECISION_ENABLED": "1", "VOICE_AUDIO_DECISION_API_KEY_FILE": str(secret)}
    config = AudioDecisionConfig.from_env(env, secret_roots=(secret.parent,))
    assert config.api_key == KEY
    with pytest.raises(AudioDecisionConfigurationError):
        AudioDecisionConfig.from_env(env, secret_roots=(tmp_path / "elsewhere",))


# --- result mapping for every outcome ----------------------------------------------------------


def test_ok_calibrated_is_a_proposal_with_provenance():
    transport = FakeTransport(200, _result({"command": _field("ok", "lights on", calibrated=True, confidence=0.93)}))
    outcome = _decide(transport)
    assert outcome.ok and outcome.value("command") == "lights on"
    assert outcome.provenance.as_dict() == {
        "model": "base/v51864/l6/f1",
        "profile": {"id": "home-control-en", "version": "0.3.0", "status": "beta"},
        "usage": {"n_encode": 1, "n_encode_total": 1},
        "fallback_provenance": None,
        "api_version": API_VERSION,
    }
    assert classify_field(outcome, "command") is AudioDecisionKind.PROPOSAL
    seen = []

    def policy(proposal):
        seen.append(proposal)
        return PolicyVerdict.CONFIRM

    hub = gate_audio_decision(outcome, field_name="command", policy=policy)
    assert hub.action is HubAction.CONFIRM and hub.value == "lights on"
    assert seen[0].grants_permission is False and seen[0].confidence == 0.93
    assert gate_audio_decision(outcome, field_name="command", policy=_allow).action is HubAction.ACT
    assert gate_audio_decision(outcome, field_name="command", policy=lambda _p: "deny").action is HubAction.DENY
    assert hub.grants_permission is False


def test_ok_uncalibrated_is_only_a_ranking_and_never_acts():
    transport = FakeTransport(200, _result({"command": _field("ok", "lights on", calibrated=False, confidence=0.99)}))
    outcome = _decide(transport)
    assert outcome.fields["command"].confidence is None
    assert classify_field(outcome, "command") is AudioDecisionKind.RANKING
    hub = gate_audio_decision(outcome, field_name="command", policy=_allow)
    assert hub.action is HubAction.CONFIRM and hub.confidence is None


@pytest.mark.parametrize(
    "status,reasons",
    [
        ("abstain", ["out_of_set"]),
        ("abstain", ["no_speech"]),
        ("ambiguous", ["low_margin"]),
        ("unsupported", ["semantic_profile_not_validated"]),
    ],
)
def test_abstain_ambiguous_unsupported_carry_no_value(status, reasons):
    # a server bug that still sends a value must not leak through
    transport = FakeTransport(200, _result({"command": _field(status, "lights on", reasons=reasons)}))
    outcome = _decide(transport)
    assert outcome.ok and outcome.value("command") is None
    assert outcome.fields["command"].value is None and outcome.fields["command"].reasons == tuple(reasons)
    policy_calls = []
    hub = gate_audio_decision(outcome, field_name="command", policy=lambda p: policy_calls.append(p) or "allow")
    assert hub.kind is AudioDecisionKind.NO_VALUE and hub.action is HubAction.ASK_AGAIN and hub.value is None
    assert policy_calls == []


def test_unknown_field_status_is_treated_as_abstain():
    outcome = _decide(FakeTransport(200, _result({"command": _field("maybe", "lights on")})))
    assert outcome.fields["command"].status == "abstain" and outcome.value("command") is None
    assert "malformed_status" in outcome.fields["command"].reasons


def test_fallback_transcript_with_system2_goes_to_system2_path():
    fallback = {
        "used": True,
        "reason": "command:abstain",
        "transcript": SECRET_TRANSCRIPT,
        "provenance": "whisper_full greedy, language=en, no_timestamps, single_segment",
        "system2_required": True,
    }
    payload = _result(
        {"command": _field("abstain", reasons=["out_of_set"], matched_from_transcript="lights on")},
        fallback=fallback,
        usage={"n_encode": 1, "n_encode_total": 2},
    )
    outcome = _decide(FakeTransport(200, payload), fallback="transcribe")
    assert outcome.fallback_used and outcome.system2_required and outcome.transcript == SECRET_TRANSCRIPT
    assert outcome.provenance.fallback_provenance.startswith("whisper_full greedy")
    assert outcome.provenance.n_encode_total == 2
    assert outcome.value("command") is None and outcome.fields["command"].matched_from_transcript == "lights on"
    hub = gate_audio_decision(outcome, field_name="command", policy=_allow)
    assert hub.kind is AudioDecisionKind.SYSTEM2 and hub.action is HubAction.SYSTEM2
    assert hub.system2_transcript == SECRET_TRANSCRIPT and hub.value is None
    assert SECRET_TRANSCRIPT not in json.dumps(hub.as_audit_dict()) and SECRET_TRANSCRIPT not in repr(hub)


def test_semantic_profile_always_requests_transcription_fallback():
    payload = _result(
        {"intent": _field("unsupported", reasons=["semantic_profile_not_validated"])},
        profile="intent-semantic-experimental",
        fallback={"used": True, "transcript": "hello there", "provenance": "p", "system2_required": True},
    )
    transport = FakeTransport(200, payload)
    outcome = _decide(transport, profile="intent-semantic-experimental", fallback="none")
    assert _request_json(transport.calls[0])["options"]["fallback"] == "transcribe"
    assert gate_audio_decision(outcome, field_name="intent", policy=_allow).action is HubAction.SYSTEM2


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_request"),
        (401, "unauthorized"),
        (413, "audio_too_long"),
        (422, "unsupported_language"),
        (429, "queue_full"),
        (500, "internal_error"),
        (503, "unavailable"),
        (504, "deadline_exceeded"),
    ],
)
def test_server_errors_are_no_decision_and_never_allow(status, code):
    outcome = _decide(FakeTransport(status, _error(code)))
    assert outcome.ok is False and outcome.error_code == code and outcome.http_status == status
    assert outcome.fields == {} and outcome.value("command") is None
    policy_calls = []
    hub = gate_audio_decision(outcome, field_name="command", policy=lambda p: policy_calls.append(p) or "allow")
    assert hub.kind is AudioDecisionKind.NO_DECISION and hub.action is HubAction.NORMAL_PATH
    assert hub.error_code == code and policy_calls == []


@pytest.mark.parametrize(
    "transport,code",
    [
        (FakeTransport(exc=ConnectionRefusedError()), "unavailable"),
        (FakeTransport(exc=socket.timeout("timed out")), "deadline_exceeded"),
        (FakeTransport(exc=TimeoutError()), "deadline_exceeded"),
        (FakeTransport(exc=http.client.RemoteDisconnected("gone")), "unavailable"),
        (FakeTransport(200, raw=b"<html>"), "bad_response"),
        (FakeTransport(200, raw=b"[1, 2]"), "bad_response"),
        (FakeTransport(502, raw=b"null"), "bad_response"),
        (FakeTransport(502, {"detail": "proxy"}), "http_502"),
        (FakeTransport(200, _result({}, api_version="audio.decision.v2")), "api_version_mismatch"),
        (FakeTransport(200, _result({"command": _field("ok", "stop")}, profile="speech-commands-en")), "bad_response"),
        (FakeTransport(200, {**_result({}), "fields": None}), "bad_response"),
        (FakeTransport(200, {**_result({}), "object": "something"}), "http_200"),
    ],
)
def test_transport_and_protocol_failures_are_no_decision(transport, code):
    outcome = _decide(transport)
    assert outcome.ok is False and outcome.error_code == code
    assert gate_audio_decision(outcome, field_name="command", policy=_allow).action is HubAction.NORMAL_PATH


def test_policy_failure_or_unknown_verdict_denies():
    outcome = _decide(FakeTransport(200, _result({"command": _field("ok", "stop", calibrated=True, confidence=0.9)})))

    def broken(_proposal):
        raise RuntimeError("policy store down")

    assert gate_audio_decision(outcome, field_name="command", policy=broken).action is HubAction.DENY
    assert gate_audio_decision(outcome, field_name="command", policy=lambda _p: None).action is HubAction.DENY
    assert gate_audio_decision(outcome, field_name="missing", policy=_allow).action is HubAction.ASK_AGAIN


# --- local checks before upload ---------------------------------------------------------------


def test_request_shape_auth_and_normalized_audio():
    fields = {"command": _field("ok", "stop", calibrated=True, confidence=0.9)}
    transport = FakeTransport(200, _result(fields, profile="speech-commands-en"))
    outcome = _decide(
        transport, profile="speech-commands-en", content=_wav(800, rate=44_100), language="EN", fields=("command",)
    )
    assert outcome.ok
    call = transport.calls[0]
    assert call["method"] == "POST" and call["url"] == "http://127.0.0.1:8081/v1/audio/decisions"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert 0 < call["timeout_s"] <= 3.0
    request = _request_json(call)
    assert request["profile"] == "speech-commands-en" and request["fields"] == ["command"]
    assert request["options"]["language"] == "en" and request["options"]["fallback"] == "none"
    assert 0 < request["options"]["timeout_ms"] <= 3000
    audio = call["body"][call["body"].index(b"RIFF") :]
    with wave.open(io.BytesIO(audio)) as decoded:
        assert decoded.getframerate() == 16_000 and decoded.getnchannels() == 1


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"profile": "not-allowed"}, "profile_not_allowed"),
        ({"content": _wav(31_000)}, "audio_too_long"),
        ({"content": b""}, "invalid_audio"),
        ({"content": b"RIFF\x00\x00garbage"}, "invalid_audio"),
        ({"language": "en; rm -rf"}, "invalid_request"),
        ({"fallback": "always"}, "invalid_request"),
        ({"fields": ()}, "invalid_request"),
    ],
)
def test_local_rejections_never_contact_the_service(kwargs, code):
    transport = FakeTransport(200, _result({}))
    outcome = _decide(transport, **kwargs)
    assert outcome.ok is False and outcome.error_code == code
    assert transport.calls == []


def test_expired_or_cancelled_token_is_no_decision_without_upload():
    transport = FakeTransport(200, _result({}))
    expired = BackendCancellationToken(deadline_monotonic=time.monotonic() - 1)
    assert _decide(transport, cancellation_token=expired).error_code == "deadline_exceeded"
    cancelled = BackendCancellationToken(deadline_monotonic=time.monotonic() + 60)
    cancelled.cancel()
    assert _decide(transport, cancellation_token=cancelled).error_code == "cancelled"
    assert transport.calls == []


def test_token_deadline_lowers_request_timeout():
    transport = FakeTransport(200, _result({"command": _field("ok", "stop")}))
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 0.5)
    _decide(transport, cancellation_token=token)
    assert transport.calls[0]["timeout_s"] <= 0.5
    assert _request_json(transport.calls[0])["options"]["timeout_ms"] <= 500


def test_profiles_are_filtered_by_allowlist_and_fail_closed():
    listing = {"data": [{"id": "home-control-en"}, {"id": "secret-profile"}, "junk"]}
    provider = AudioDecisionProvider(_config(profiles=("home-control-en",)), transport=FakeTransport(200, listing))
    assert provider.profiles() == [{"id": "home-control-en"}]
    failing = AudioDecisionProvider(_config(), transport=FakeTransport(exc=OSError("down")))
    assert failing.profiles() == []


def test_logs_contain_no_audio_transcript_or_key():
    fallback = {"used": True, "transcript": SECRET_TRANSCRIPT, "provenance": "p", "system2_required": True}
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("voice_runtime.backends.audio_decision")
    previous_level, previous_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False  # hub test app setup may run dictConfig(disable_existing_loggers=True)
    try:
        _decide(FakeTransport(200, _result({"command": _field("abstain")}, fallback=fallback)), fallback="transcribe")
        _decide(FakeTransport(401, _error("unauthorized", "bad token " + KEY)))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previous_disabled
    text = stream.getvalue()
    assert "audio decision profile=home-control-en" in text
    assert SECRET_TRANSCRIPT not in text and KEY not in text and "RIFF" not in text


def test_parse_is_fail_closed_on_unreachable_status():
    assert parse_decision_response(None, b"connection refused").error_code == "unavailable"
    assert isinstance(parse_decision_response(200, b"{}"), DecisionOutcome)
    assert parse_decision_response(200, b"{}").ok is False


# --- real HTTP round trip against a stub server (no model) ------------------------------------


class _StubHandler(http.server.BaseHTTPRequestHandler):
    delay_s = 0.0
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).seen.append({"auth": self.headers.get("Authorization"), "path": self.path, "body": body})
        time.sleep(type(self).delay_s)
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            payload, status = _error("unauthorized"), 401
        else:
            payload, status = _result({"command": _field("ok", "lights on", calibrated=True, confidence=0.95)}), 200
        data = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        return


@pytest.fixture
def stub_server():
    _StubHandler.delay_s = 0.0
    _StubHandler.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_http_round_trip_against_stub_server(stub_server):
    provider = AudioDecisionProvider(_config(url=stub_server))
    outcome = provider.decide(filename="clip.wav", content=_wav(), profile="home-control-en")
    assert outcome.ok and outcome.value("command") == "lights on"
    assert _StubHandler.seen[0]["path"] == "/v1/audio/decisions"
    assert _StubHandler.seen[0]["auth"] == f"Bearer {KEY}"
    wrong_key = AudioDecisionProvider(_config(url=stub_server, api_key="wrong-key-0123456789"))
    denied = wrong_key.decide(filename="clip.wav", content=_wav(), profile="home-control-en")
    assert denied.ok is False and denied.error_code == "unauthorized"


def test_http_timeout_against_slow_stub_server(stub_server):
    _StubHandler.delay_s = 1.0
    provider = AudioDecisionProvider(_config(url=stub_server, timeout_ms=200))
    started = time.monotonic()
    outcome = provider.decide(filename="clip.wav", content=_wav(), profile="home-control-en")
    assert outcome.ok is False and outcome.error_code == "deadline_exceeded"
    assert time.monotonic() - started < 1.0


def test_http_unreachable_service_is_unavailable():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = AudioDecisionProvider(_config(url=f"http://127.0.0.1:{port}"))
    outcome = provider.decide(filename="clip.wav", content=_wav(), profile="home-control-en")
    assert outcome.ok is False and outcome.error_code == "unavailable"


def test_provider_refuses_disabled_config():
    with pytest.raises(AudioDecisionConfigurationError):
        AudioDecisionProvider(AudioDecisionConfig())
