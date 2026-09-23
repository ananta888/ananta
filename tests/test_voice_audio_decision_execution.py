"""AudioDecision as primary path of ``/v1/voice/command``: hub executor and explicit confirmations."""
from __future__ import annotations

import http.client
import json
import socket
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.services import audio_decision_command_service
from agent.services.audio_decision_command_executor import (
    VoiceCommandConfirmationStore,
    VoiceCommandExecutor,
    VoiceCommandInvocation,
    confirm_voice_command,
    confirmation_ttl_seconds,
    default_handlers,
    dispatch_audio_decision,
)
from agent.services.audio_decision_command_policy import PolicyDecision, VoiceCommandAction
from agent.services.audio_decision_command_service import AudioDecisionCommandResult, run_audio_decision_command
from agent.services.audio_decision_hub_gate import AudioDecisionKind, HubAction, HubAudioDecision, PolicyVerdict
from agent.services.voice_governance_domain import VoicePrincipal
from tests.test_voice_audio_decision_provider import FakeTransport, _config, _field, _result, _wav
from voice_runtime.backends.audio_decision import AudioDecisionProvider, DecisionOutcome

COMMAND = "/v1/voice/command"
CONFIRM = "/v1/voice/command/confirm"
MODEL = "base/v51864/l6/f1"
SECRET_TRANSCRIPT = "wire the savings to account nine"
RUNTIME_TRANSCRIPT = "create repo health goal"
PROFILES = ("speech-commands-en", "home-control-en", "intent-semantic-experimental")
ALICE = VoicePrincipal(tenant_id="tenant-a", subject="alice")
MALLORY = VoicePrincipal(tenant_id="tenant-b", subject="mallory")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean_audio_decision_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("VOICE_AUDIO_DECISION_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(audio_decision_command_service, "_provider_cache", None)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    fresh = VoiceCommandConfirmationStore(clock=clock)
    with patch("agent.routes.voice_audio_decision.get_voice_command_confirmation_store", return_value=fresh):
        yield fresh


@pytest.fixture
def runtime():
    """The regular voice-runtime pipeline of ``/v1/voice/command``."""
    with patch("agent.routes.voice.get_voice_provider_service") as factory:
        factory.return_value.voice_command.return_value = {
            "text": RUNTIME_TRANSCRIPT,
            "transcript": RUNTIME_TRANSCRIPT,
            "tool_intent": {"type": "voice_command", "confidence": 0.8},
        }
        yield factory.return_value


def _speech_result(field: dict, **kwargs) -> dict:
    payload = _result({"command": field}, profile="speech-commands-en", **kwargs)
    payload["model"] = MODEL
    return payload


def _with_provider(transport):
    provider = AudioDecisionProvider(_config(profiles=PROFILES), transport=transport)
    return patch("agent.routes.voice_audio_decision.get_audio_decision_provider", return_value=provider)


def _command(client, headers, **form):
    data = {"file": (BytesIO(form.pop("audio", _wav())), "clip.wav"), **form}
    return client.post(COMMAND, headers=headers, data=data, content_type="multipart/form-data")


def _confirm(client, headers, body):
    return client.post(CONFIRM, headers=headers, json=body)


def _no_permission_anywhere(value) -> None:
    if isinstance(value, dict):
        if "grants_permission" in value:
            assert value["grants_permission"] is False
        for item in value.values():
            _no_permission_anywhere(item)
    elif isinstance(value, list):
        for item in value:
            _no_permission_anywhere(item)


def _audits(audit) -> dict[str, dict]:
    return {call.args[0]: call.args[1] for call in audit.call_args_list}


def _forbid_network(monkeypatch) -> list:
    contacted: list = []

    def forbidden(*args, **kwargs):
        contacted.append(args)
        raise AssertionError("audio decision service must not be contacted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    return contacted


# --- (A) primary path of /v1/voice/command ---------------------------------------------------------


def test_feature_off_keeps_the_command_route_unchanged(client, admin_auth_header, runtime, monkeypatch):
    contacted = _forbid_network(monkeypatch)
    with (
        patch("agent.routes.voice.log_audit") as audit,
        patch("agent.routes.voice_audio_decision.log_audit") as decision_audit,
    ):
        plain = _command(client, admin_auth_header)
        with_profile = _command(client, admin_auth_header, decision_profile="speech-commands-en")
    for res in (plain, with_profile):
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["transcript"] == RUNTIME_TRANSCRIPT and data["requires_approval"] is True
        assert set(data) == {
            "transcript", "intent", "confidence", "proposed_goal", "requires_approval", "audit_id",
            "task_id", "result_ref", "result_digest", "idempotent_replay",
        }
    assert runtime.voice_command.call_count == 2
    assert contacted == [] and decision_audit.call_count == 0
    assert [call.args[0] for call in audit.call_args_list] == ["voice_command", "voice_command"]


def test_enabled_feature_without_decision_profile_never_builds_the_provider(client, admin_auth_header, runtime):
    provider_factory = MagicMock(side_effect=AssertionError("provider must not be built"))
    with patch("agent.routes.voice_audio_decision.get_audio_decision_provider", provider_factory):
        res = _command(client, admin_auth_header)
    assert res.status_code == 200 and "audio_decision" not in res.get_json()["data"]
    assert provider_factory.call_count == 0 and runtime.voice_command.call_count == 1


def test_exposure_policy_is_enforced_before_the_decision_path(client, admin_auth_header, runtime, store):
    transport = FakeTransport(200, _speech_result(_field("ok", "stop", calibrated=True, confidence=0.97)))
    denied = SimpleNamespace(
        allowed=False, reason="voice_exposure_disabled", auth_source="user_jwt", policy={"emit_audit_events": False}
    )
    with _with_provider(transport), patch("agent.routes.voice.get_exposure_policy_service") as policy:
        policy.return_value.evaluate_voice_access.return_value = denied
        res = _command(client, admin_auth_header, decision_profile="speech-commands-en")
    assert res.status_code == 403 and res.get_json()["data"]["error"]["code"] == "policy_denied"
    assert transport.calls == [] and runtime.voice_command.call_count == 0
    assert store.pending_count() == 0


def test_calibrated_direct_command_is_executed_and_audited(client, admin_auth_header, runtime, store):
    transport = FakeTransport(200, _speech_result(_field("ok", "stop", calibrated=True, confidence=0.97)))
    audio = _wav()
    with _with_provider(transport), patch("agent.routes.voice_audio_decision.log_audit") as audit:
        res = _command(client, admin_auth_header, audio=audio, decision_profile="speech-commands-en")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["path"] == "audio_decision" and data["hub_action"] == "act"
    assert data["policy"] == {"verdict": "allow", "rule": "P12_direct_low_risk"}
    assert data["execution"] == {
        "status": "executed",
        "action_type": "voice.control.stop",
        "error_code": None,
        "effect": {"kind": "control", "control": "stop"},
        "grants_permission": False,
    }
    assert data["confirmation"] is None
    _no_permission_anywhere(data)
    assert runtime.voice_command.call_count == 0 and len(transport.calls) == 1
    details = _audits(audit)["voice_audio_decision_command"]
    assert details["endpoint"] == COMMAND
    assert details["decision"]["execution"] == {
        "status": "executed", "action_type": "voice.control.stop", "error_code": None, "grants_permission": False,
    }
    dumped = json.dumps(details)
    assert '"stop"' not in dumped and "effect" not in dumped
    assert audio.hex()[:64] not in dumped and details["audio_size_bytes"] == len(audio)


def test_unusable_decision_falls_through_to_the_regular_pipeline(client, admin_auth_header, runtime, store):
    audio = _wav(700)
    with _with_provider(FakeTransport(None, exc=TimeoutError())):
        res = _command(client, admin_auth_header, audio=audio, decision_profile="speech-commands-en")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["transcript"] == RUNTIME_TRANSCRIPT and data["requires_approval"] is True
    note = data["audio_decision"]
    assert note["hub_action"] == "normal_path" and note["error_code"] == "deadline_exceeded"
    assert note["grants_permission"] is False
    assert runtime.voice_command.call_args.kwargs["content"] == audio


@pytest.mark.parametrize(
    ("form", "code"),
    [
        ({"decision_profile": "made-up"}, "audio_decision.profile_not_supported"),
        ({"decision_profile": "speech-commands-en", "decision_field": "volume"}, "audio_decision.field_not_supported"),
        ({"decision_profile": "speech-commands-en", "decision_fallback": "always"}, "audio_decision.invalid_fallback"),
    ],
)
def test_invalid_decision_parameters_are_rejected_before_any_contact(client, admin_auth_header, runtime, form, code):
    transport = FakeTransport(200, {})
    with _with_provider(transport):
        res = _command(client, admin_auth_header, **form)
    assert res.status_code == 422 and res.get_json()["data"]["error"]["code"] == code
    assert transport.calls == [] and runtime.voice_command.call_count == 0


def test_misconfigured_feature_is_a_503_on_the_command_route(client, admin_auth_header, runtime, monkeypatch):
    monkeypatch.setenv("VOICE_AUDIO_DECISION_ENABLED", "true")
    monkeypatch.setenv("VOICE_AUDIO_DECISION_API_KEY", "short")
    res = _command(client, admin_auth_header, decision_profile="speech-commands-en")
    assert res.status_code == 503 and "short" not in res.get_data(as_text=True)
    assert runtime.voice_command.call_count == 0


def test_ask_again_is_typed_and_executes_nothing(client, admin_auth_header, runtime, store):
    with _with_provider(FakeTransport(200, _speech_result(_field("abstain", reasons=["out_of_set"])))):
        data = _command(client, admin_auth_header, decision_profile="speech-commands-en").get_json()["data"]
    assert data["hub_action"] == "ask_again" and data["execution"] is None and data["confirmation"] is None
    assert runtime.voice_command.call_count == 0 and store.pending_count() == 0


def test_system2_hands_off_to_the_goal_path_without_logging_the_transcript(client, admin_auth_header, runtime, store):
    payload = _speech_result(
        _field("abstain", reasons=["out_of_set"]),
        fallback={"used": True, "transcript": SECRET_TRANSCRIPT, "system2_required": True, "provenance": "greedy"},
    )
    with (
        _with_provider(FakeTransport(200, payload)),
        patch("agent.routes.voice_audio_decision.log_audit") as audit,
        patch("agent.routes.voice.log_audit") as command_audit,
    ):
        data = _command(client, admin_auth_header, decision_profile="speech-commands-en").get_json()["data"]
    assert data["hub_action"] == "system2" and data["execution"] is None and data["confirmation"] is None
    system2 = data.pop("system2")
    assert system2["goal_route"] == "/v1/voice/goal" and system2["requires_approval"] is True
    assert system2["transcript"] == SECRET_TRANSCRIPT
    assert SECRET_TRANSCRIPT not in json.dumps(data)  # only in the System-2 hand-off block
    assert SECRET_TRANSCRIPT not in json.dumps([call.args for call in audit.call_args_list])
    assert command_audit.call_count == 0 and runtime.voice_command.call_count == 0 and store.pending_count() == 0


# --- (B) confirmation flow ---------------------------------------------------------------------------


def _issue_confirmation(client, headers, label="on"):
    field = _field("ok", label, calibrated=True, confidence=0.99)
    with _with_provider(FakeTransport(200, _speech_result(field))):
        res = _command(client, headers, decision_profile="speech-commands-en")
    assert res.status_code == 200
    return res.get_json()["data"]


def test_confirm_issues_a_single_use_confirmation_and_only_an_explicit_request_executes(
    client, admin_auth_header, runtime, store
):
    with patch("agent.routes.voice_audio_decision.log_audit") as audit:
        data = _issue_confirmation(client, admin_auth_header)
    assert data["hub_action"] == "confirm" and data["execution"] is None
    assert data["policy"]["rule"] == "P11_confirmation_required"
    confirmation = data["confirmation"]
    assert confirmation["action_type"] == "voice.control.switch_on" and confirmation["single_use"] is True
    assert confirmation["confirm_route"] == CONFIRM and confirmation["grants_permission"] is False
    assert store.pending_count() == 1 and runtime.voice_command.call_count == 0
    issued_audit = json.dumps(_audits(audit)["voice_audio_decision_command"])
    assert confirmation["confirmation_id"] not in issued_audit and '"on"' not in issued_audit

    body = {"confirmation_id": confirmation["confirmation_id"], "action_type": "voice.control.switch_on"}
    with patch("agent.routes.voice_audio_decision.log_audit") as audit:
        executed = _confirm(client, admin_auth_header, {**body, "confirmed": True})
        replay = _confirm(client, admin_auth_header, {**body, "confirmed": True})
    assert executed.status_code == 200
    execution = executed.get_json()["data"]["execution"]
    assert execution["status"] == "executed" and execution["effect"] == {"kind": "control", "control": "switch_on"}
    assert replay.status_code == 403
    assert replay.get_json()["data"]["error"]["code"] == "confirmation.already_used"
    assert replay.get_json()["data"]["execution"]["status"] == "denied"
    events = [call.args for call in audit.call_args_list]
    assert [name for name, _ in events] == ["voice_audio_decision_confirmation"] * 2
    assert [details["policy_decision"] for _, details in events] == ["allowed", "denied"]
    assert confirmation["confirmation_id"] not in json.dumps(events)


def test_expired_confirmation_is_denied(client, admin_auth_header, runtime, store, clock):
    confirmation = _issue_confirmation(client, admin_auth_header)["confirmation"]
    clock.now += confirmation["expires_in_seconds"] + 0.01
    res = _confirm(
        client,
        admin_auth_header,
        {"confirmation_id": confirmation["confirmation_id"], "action_type": confirmation["action_type"],
         "confirmed": True},
    )
    assert res.status_code == 410
    assert res.get_json()["data"]["error"]["code"] == "confirmation.expired"
    assert res.get_json()["data"]["execution"]["effect"] is None


def test_foreign_principal_cannot_use_and_burns_the_confirmation(client, admin_auth_header, runtime, store):
    confirmation = _issue_confirmation(client, admin_auth_header)["confirmation"]
    body = {"confirmation_id": confirmation["confirmation_id"], "action_type": confirmation["action_type"],
            "confirmed": True}
    with patch("agent.routes.voice_audio_decision._principal", return_value=MALLORY):
        foreign = _confirm(client, admin_auth_header, body)
    assert foreign.status_code == 403
    assert foreign.get_json()["data"]["error"]["code"] == "confirmation.foreign_binding"
    owner = _confirm(client, admin_auth_header, body)
    assert owner.status_code == 403 and owner.get_json()["data"]["error"]["code"] == "confirmation.already_used"


def test_confirmation_is_bound_to_the_concrete_action(client, admin_auth_header, runtime, store):
    confirmation = _issue_confirmation(client, admin_auth_header)["confirmation"]
    res = _confirm(
        client,
        admin_auth_header,
        {"confirmation_id": confirmation["confirmation_id"], "action_type": "voice.control.switch_off",
         "confirmed": True},
    )
    assert res.status_code == 403 and res.get_json()["data"]["error"]["code"] == "confirmation.action_mismatch"
    assert store.pending_count() == 0


@pytest.mark.parametrize("confirmed", ["true", 1, None, "yes"])
def test_only_a_literal_boolean_confirms(client, admin_auth_header, runtime, store, confirmed):
    confirmation = _issue_confirmation(client, admin_auth_header)["confirmation"]
    body = {"confirmation_id": confirmation["confirmation_id"], "action_type": confirmation["action_type"]}
    res = _confirm(client, admin_auth_header, {**body, "confirmed": confirmed})
    assert res.status_code == 422 and store.pending_count() == 1
    rejected = _confirm(client, admin_auth_header, {**body, "confirmed": False})
    assert rejected.status_code == 200
    assert rejected.get_json()["data"]["execution"] == {
        "status": "denied", "action_type": "voice.control.switch_on", "error_code": "confirmation.rejected",
        "effect": None, "grants_permission": False,
    }
    assert store.pending_count() == 0


def test_confirm_route_enforces_auth_and_exposure_policy(client, admin_auth_header, runtime, store):
    confirmation = _issue_confirmation(client, admin_auth_header)["confirmation"]
    body = {"confirmation_id": confirmation["confirmation_id"], "action_type": confirmation["action_type"],
            "confirmed": True}
    assert _confirm(client, {}, body).status_code == 401
    denied = SimpleNamespace(
        allowed=False, reason="voice_exposure_disabled", auth_source="user_jwt", policy={"emit_audit_events": False}
    )
    with patch("agent.routes.voice.get_exposure_policy_service") as policy:
        policy.return_value.evaluate_voice_access.return_value = denied
        res = _confirm(client, admin_auth_header, body)
    assert res.status_code == 403 and res.get_json()["data"]["error"]["code"] == "policy_denied"
    assert store.pending_count() == 1


def test_unknown_confirmation_id_is_denied(client, admin_auth_header, store):
    res = _confirm(client, admin_auth_header, {"confirmation_id": "vcc-guess", "action_type": "voice.control.stop",
                                               "confirmed": True})
    assert res.status_code == 403 and res.get_json()["data"]["error"]["code"] == "confirmation.unknown"


def test_confirm_for_an_unregistered_action_type_is_denied_without_a_confirmation(
    client, admin_auth_header, runtime, store
):
    payload = _result({"command": _field("ok", "lights_on", calibrated=True, confidence=0.99)},
                      profile="home-control-en")
    payload["model"] = MODEL
    with _with_provider(FakeTransport(200, payload)):
        data = _command(client, admin_auth_header, decision_profile="home-control-en").get_json()["data"]
    assert data["hub_action"] == "deny" and data["confirmation"] is None
    assert data["execution"]["error_code"] == "executor.unknown_action_type"
    assert store.pending_count() == 0 and runtime.voice_command.call_count == 0


def test_facade_route_shares_the_executor(client, admin_auth_header, store):
    transport = FakeTransport(200, _speech_result(_field("ok", "left", calibrated=True, confidence=0.95)))
    with _with_provider(transport):
        data = client.post(
            "/v1/voice/audio-decisions/command",
            headers=admin_auth_header,
            data={"file": (BytesIO(_wav()), "clip.wav")},
            content_type="multipart/form-data",
        ).get_json()["data"]
    assert data["hub_action"] == "act" and data["execution"]["effect"] == {"kind": "navigate", "direction": "left"}


# --- executor / dispatch units ------------------------------------------------------------------------


def _invocation(action_type: str) -> VoiceCommandInvocation:
    return VoiceCommandInvocation(action_type=action_type, command="stop", profile="p", field="f", principal=ALICE)


def test_registry_covers_exactly_the_voice_action_types_of_the_policy():
    from agent.services.audio_decision_command_policy import DEFAULT_COMMAND_CATALOG

    voice_types = {
        action.action_type
        for profile in DEFAULT_COMMAND_CATALOG.values()
        for command_field in profile.fields.values()
        for action in command_field.actions.values()
        if action.action_type.startswith("voice.")
    }
    assert voice_types <= set(default_handlers())
    assert all(name.split(".")[1] in {"control", "navigate", "dialog"} for name in default_handlers())


@pytest.mark.parametrize(
    "action_type", ["voice.control.explode", "voice.control", "voice.control.*", "voice.control.stop ", "", "home.stop"]
)
def test_unknown_action_type_is_denied_and_never_guessed(action_type):
    authorize = MagicMock(return_value=True)
    execution = VoiceCommandExecutor().execute(_invocation(action_type), authorize=authorize)
    assert execution.executed is False and execution.error_code == "executor.unknown_action_type"
    assert authorize.call_count == 0


@pytest.mark.parametrize(
    ("authorize", "code"),
    [
        (lambda: False, "executor.not_authorized"),
        (lambda: None, "executor.not_authorized"),
        (lambda: "yes", "executor.not_authorized"),
        (MagicMock(side_effect=RuntimeError("policy down")), "executor.not_authorized"),
    ],
)
def test_execution_requires_a_positive_authorization(authorize, code):
    handler = MagicMock(return_value={"kind": "control"})
    execution = VoiceCommandExecutor({"voice.control.stop": handler}).execute(
        _invocation("voice.control.stop"), authorize=authorize
    )
    assert execution.executed is False and execution.error_code == code and handler.call_count == 0


@pytest.mark.parametrize("handler", [MagicMock(side_effect=RuntimeError("boom")), MagicMock(return_value="done")])
def test_failing_handler_is_denied(handler):
    execution = VoiceCommandExecutor({"voice.control.stop": handler}).execute(
        _invocation("voice.control.stop"), authorize=lambda: True
    )
    assert execution.executed is False and execution.error_code == "executor.handler_failed"
    assert execution.as_response()["effect"] is None


def test_register_refuses_to_overwrite():
    executor = VoiceCommandExecutor()
    with pytest.raises(ValueError):
        executor.register("voice.control.stop", lambda _invocation: {})


def _hub_result(action: HubAction, verdict: PolicyVerdict, *, grants_permission: bool = False,
                with_action: bool = True) -> AudioDecisionCommandResult:
    hub = HubAudioDecision(
        field="command", kind=AudioDecisionKind.PROPOSAL, action=action, value="stop", confidence=0.99,
        grants_permission=grants_permission,
    )
    return AudioDecisionCommandResult(
        hub=hub,
        profile="speech-commands-en",
        policy=PolicyDecision(verdict, "test"),
        action=VoiceCommandAction("stop", "voice.control.stop", direct=True) if with_action else None,
    )


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (_hub_result(HubAction.ACT, PolicyVerdict.CONFIRM), "executor.not_policy_allowed"),
        (_hub_result(HubAction.ACT, PolicyVerdict.DENY), "executor.not_policy_allowed"),
        (_hub_result(HubAction.ACT, PolicyVerdict.ALLOW, grants_permission=True), "executor.permission_claim"),
        (_hub_result(HubAction.CONFIRM, PolicyVerdict.CONFIRM, grants_permission=True), "executor.permission_claim"),
        (_hub_result(HubAction.ACT, PolicyVerdict.ALLOW, with_action=False), "executor.missing_action"),
    ],
)
def test_dispatch_never_executes_what_the_hub_policy_did_not_allow(result, code):
    store = VoiceCommandConfirmationStore()
    handler = MagicMock(return_value={})
    dispatch = dispatch_audio_decision(
        result, principal=ALICE, authorize=lambda: True,
        executor=VoiceCommandExecutor({"voice.control.stop": handler}), confirmations=store, ttl_seconds=30,
    )
    assert dispatch.response["hub_action"] == "deny" and dispatch.response["error_code"] == code
    assert dispatch.response["grants_permission"] is False and dispatch.response["confirmation"] is None
    assert handler.call_count == 0 and store.pending_count() == 0


@pytest.mark.parametrize(
    "outcome",
    [
        DecisionOutcome.failure("deadline_exceeded"),
        DecisionOutcome.failure("unavailable"),
        DecisionOutcome.failure("unauthorized"),
        DecisionOutcome.failure("queue_full"),
        DecisionOutcome.failure("bad_response"),
    ],
)
def test_errors_and_timeouts_never_become_an_allow(outcome):
    provider = MagicMock()
    provider.decide.return_value = outcome
    allow_everything = MagicMock()
    allow_everything.decide.side_effect = AssertionError("policy must not see a failed decision")
    result = run_audio_decision_command(
        provider, filename="a.wav", content=b"x", profile="speech-commands-en", field_name="command",
        language="en", fallback="none", policy=allow_everything,
    )
    handler = MagicMock(return_value={})
    store = VoiceCommandConfirmationStore()
    dispatch = dispatch_audio_decision(
        result, principal=ALICE, authorize=lambda: True,
        executor=VoiceCommandExecutor({"voice.control.stop": handler}), confirmations=store, ttl_seconds=30,
    )
    assert dispatch.response["hub_action"] == "normal_path" and dispatch.execution is None
    assert handler.call_count == 0 and store.pending_count() == 0
    _no_permission_anywhere(dispatch.response)


def test_store_binding_expiry_and_replay():
    clock = Clock()
    store = VoiceCommandConfirmationStore(clock=clock)
    issue = dict(action_type="voice.control.start", command="go", profile="p", field_name="command", ttl_seconds=10)
    first = store.issue(ALICE, **issue)
    second = store.issue(ALICE, **issue)
    assert first.confirmation_id != second.confirmation_id
    handler = MagicMock(return_value={"ok": True})
    executor = VoiceCommandExecutor({"voice.control.start": handler})

    def confirm(pending, principal=ALICE, action_type="voice.control.start"):
        return confirm_voice_command(
            pending.confirmation_id, principal=principal, action_type=action_type, confirmed=True,
            authorize=lambda: True, executor=executor, confirmations=store,
        )

    assert confirm(first, principal=VoicePrincipal(tenant_id="tenant-b", subject="alice")).error_code == (
        "confirmation.foreign_binding"
    )
    clock.now += 10
    assert confirm(second).error_code == "confirmation.expired"
    third = store.issue(ALICE, **issue)
    assert confirm(third).executed is True and confirm(third).error_code == "confirmation.already_used"
    assert handler.call_count == 1


def test_confirmation_denied_when_authorization_is_revoked():
    store = VoiceCommandConfirmationStore()
    pending = store.issue(ALICE, action_type="voice.control.stop", command="stop", profile="p", field_name="f",
                          ttl_seconds=30)
    execution = confirm_voice_command(
        pending.confirmation_id, principal=ALICE, action_type="voice.control.stop", confirmed=True,
        authorize=lambda: False, executor=VoiceCommandExecutor(), confirmations=store,
    )
    assert execution.executed is False and execution.error_code == "executor.not_authorized"


def test_store_capacity_and_principal_are_fail_closed():
    store = VoiceCommandConfirmationStore(max_pending=1)
    issue = dict(action_type="voice.control.stop", command="stop", profile="p", field_name="f", ttl_seconds=30)
    store.issue(ALICE, **issue)
    from agent.services.audio_decision_command_executor import ConfirmationError

    with pytest.raises(ConfirmationError, match="capacity_exceeded"):
        store.issue(ALICE, **issue)
    with pytest.raises(ConfirmationError, match="principal_required"):
        VoiceCommandConfirmationStore().issue(SimpleNamespace(tenant_id="", subject=""), **issue)


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 30.0), ("", 30.0), ("abc", 30.0), ("nan", 30.0), ("1", 5.0), ("9999", 300.0),
                          ("45", 45.0)]
)
def test_confirmation_ttl_is_clamped(raw, expected):
    env = {} if raw is None else {"VOICE_AUDIO_DECISION_CONFIRM_TTL_SECONDS": raw}
    assert confirmation_ttl_seconds(env) == expected
