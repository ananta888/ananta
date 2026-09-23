"""``POST /v1/voice/audio-decisions/command``: audio -> provider -> gate -> typed hub action."""
from __future__ import annotations

import http.client
import json
import socket
from io import BytesIO
from unittest.mock import patch

import pytest

from agent.services import audio_decision_command_service
from tests.test_voice_audio_decision_provider import KEY, FakeTransport, _config, _field, _result, _wav
from voice_runtime.backends.audio_decision import AudioDecisionProvider

ROUTE = "/v1/voice/audio-decisions/command"
MODEL = "base/v51864/l6/f1"
SECRET_TRANSCRIPT = "wire the savings to account nine"
PROFILES = ("speech-commands-en", "home-control-en", "intent-semantic-experimental")


@pytest.fixture(autouse=True)
def _clean_audio_decision_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("VOICE_AUDIO_DECISION_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(audio_decision_command_service, "_provider_cache", None)


def _post(client, headers, **form):
    data = {"file": (BytesIO(form.pop("audio", _wav())), "clip.wav"), **form}
    return client.post(ROUTE, headers=headers, data=data, content_type="multipart/form-data")


def _speech_result(field: dict, **kwargs) -> dict:
    payload = _result({"command": field}, profile="speech-commands-en", **kwargs)
    payload["model"] = MODEL
    return payload


def _with_provider(transport):
    provider = AudioDecisionProvider(_config(profiles=PROFILES), transport=transport)
    return patch("agent.routes.voice_audio_decision.get_audio_decision_provider", return_value=provider)


def test_feature_off_answers_normal_path_without_contacting_the_service(client, admin_auth_header, monkeypatch):
    contacted: list[object] = []

    def forbidden(*args, **kwargs):
        contacted.append(args)
        raise AssertionError("audio decision service must not be contacted when the feature is off")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    with patch("agent.routes.voice_audio_decision.log_audit") as audit:
        res = _post(client, admin_auth_header)
        no_file = client.post(ROUTE, headers=admin_auth_header)
    assert res.status_code == 200 and no_file.status_code == 200
    data = res.get_json()["data"]
    assert data == {
        "enabled": False,
        "hub_action": "normal_path",
        "error_code": "audio_decision_disabled",
        "fallback_route": "/v1/voice/command",
        "grants_permission": False,
    }
    assert contacted == [] and audit.call_count == 0


def test_route_requires_authentication(client):
    assert _post(client, {}).status_code == 401


def test_misconfigured_feature_is_a_503_without_key_material(client, admin_auth_header, monkeypatch):
    monkeypatch.setenv("VOICE_AUDIO_DECISION_ENABLED", "true")
    monkeypatch.setenv("VOICE_AUDIO_DECISION_API_KEY", "short")
    res = _post(client, admin_auth_header)
    assert res.status_code == 503
    assert res.get_json()["data"]["error"]["code"] == "audio_decision.misconfigured"
    assert "short" not in res.get_data(as_text=True)


def test_enabled_provider_is_built_from_env_and_cached(monkeypatch):
    env = {"VOICE_AUDIO_DECISION_ENABLED": "true", "VOICE_AUDIO_DECISION_API_KEY": KEY}
    first = audio_decision_command_service.get_audio_decision_provider(env)
    assert first is audio_decision_command_service.get_audio_decision_provider(dict(env))
    assert audio_decision_command_service.get_audio_decision_provider({}) is None


def test_calibrated_direct_command_acts(client, admin_auth_header):
    transport = FakeTransport(200, _speech_result(_field("ok", "stop", calibrated=True, confidence=0.97)))
    with _with_provider(transport), patch("agent.routes.voice_audio_decision.log_audit") as audit:
        res = _post(client, admin_auth_header)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["hub_action"] == "act" and data["grants_permission"] is False
    assert data["action"]["type"] == "voice.control.stop" and data["action"]["requires_confirmation"] is False
    assert data["policy"] == {"verdict": "allow", "rule": "P12_direct_low_risk"}
    request_json = transport.calls[0]["body"].split(b'name="request"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0]
    assert json.loads(request_json)["options"] | {"timeout_ms": 0} == {
        "language": "en", "fallback": "transcribe", "timeout_ms": 0,
    }
    assert json.loads(request_json)["fields"] == ["command"]
    event, details = audit.call_args.args
    assert event == "voice_audio_decision_command"
    assert details["decision"]["action"] == "act" and details["decision"]["action_type"] == "voice.control.stop"
    assert '"stop"' not in json.dumps(details)


def test_state_change_needs_confirmation_and_ranking_never_acts(client, admin_auth_header):
    with _with_provider(FakeTransport(200, _speech_result(_field("ok", "on", calibrated=True, confidence=0.99)))):
        confirm = _post(client, admin_auth_header).get_json()["data"]
    assert confirm["hub_action"] == "confirm" and confirm["action"]["requires_confirmation"] is True
    with _with_provider(FakeTransport(200, _speech_result(_field("ok", "stop", calibrated=False)))):
        ranking = _post(client, admin_auth_header).get_json()["data"]
    assert ranking["hub_action"] == "confirm" and ranking["decision_kind"] == "ranking"


@pytest.mark.parametrize(
    ("transport", "action", "code"),
    [
        (FakeTransport(None, exc=TimeoutError()), "normal_path", "deadline_exceeded"),
        (FakeTransport(None, exc=ConnectionRefusedError()), "normal_path", "unavailable"),
        (FakeTransport(200, _speech_result(_field("abstain", reasons=["out_of_set"]))), "ask_again", None),
        (FakeTransport(200, _speech_result(_field("ambiguous", reasons=["low_margin"]))), "ask_again", None),
    ],
)
def test_failures_and_abstentions_never_act(client, admin_auth_header, transport, action, code):
    with _with_provider(transport):
        data = _post(client, admin_auth_header).get_json()["data"]
    assert data["hub_action"] == action and data["action"] is None and data["error_code"] == code


def test_transcript_with_system2_required_goes_to_the_system2_goal_path(client, admin_auth_header):
    payload = _speech_result(
        _field("abstain", reasons=["out_of_set"]),
        fallback={"used": True, "transcript": SECRET_TRANSCRIPT, "system2_required": True, "provenance": "greedy"},
    )
    with _with_provider(FakeTransport(200, payload)), patch("agent.routes.voice_audio_decision.log_audit") as audit:
        data = _post(client, admin_auth_header).get_json()["data"]
    assert data["hub_action"] == "system2" and data["action"] is None
    assert data["system2"]["transcript"] == SECRET_TRANSCRIPT
    assert data["system2"]["requires_approval"] is True and data["system2"]["goal_route"] == "/v1/voice/goal"
    assert SECRET_TRANSCRIPT not in json.dumps(audit.call_args.args[1])


@pytest.mark.parametrize(
    ("form", "code"),
    [
        ({"profile": "confirm-en-de"}, "audio_decision.profile_not_supported"),
        ({"profile": "made-up"}, "audio_decision.profile_not_supported"),
        ({"field": "volume"}, "audio_decision.field_not_supported"),
        ({"fallback": "always"}, "audio_decision.invalid_fallback"),
    ],
)
def test_invalid_requests_are_rejected_before_contacting_the_service(client, admin_auth_header, form, code):
    transport = FakeTransport(200, {})
    with _with_provider(transport):
        res = _post(client, admin_auth_header, **form)
    assert res.status_code == 422 and res.get_json()["data"]["error"]["code"] == code
    assert transport.calls == []


def test_missing_audio_is_a_validation_error(client, admin_auth_header):
    transport = FakeTransport(200, {})
    with _with_provider(transport):
        res = client.post(ROUTE, headers=admin_auth_header, data={"profile": "speech-commands-en"},
                          content_type="multipart/form-data")
    assert res.status_code == 400 and transport.calls == []


def test_semantic_profile_is_routed_through_system2(client, admin_auth_header):
    payload = _result(
        {"intent": _field("unsupported", reasons=["semantic_profile_not_validated"])},
        profile="intent-semantic-experimental",
        fallback={"used": True, "transcript": SECRET_TRANSCRIPT, "system2_required": True},
    )
    transport = FakeTransport(200, payload)
    with _with_provider(transport):
        data = _post(client, admin_auth_header, profile="intent-semantic-experimental").get_json()["data"]
    assert data["hub_action"] == "system2" and data["system2"]["transcript"] == SECRET_TRANSCRIPT
