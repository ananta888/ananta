"""Hub policy + voice command flow for AudioDecision outcomes (hard rules of the hub gate)."""
from __future__ import annotations

import json

import pytest

from agent.services.audio_decision_command_policy import (
    DEFAULT_COMMAND_CATALOG,
    VoiceCommandAudioDecisionPolicy,
)
from agent.services.audio_decision_command_service import run_audio_decision_command
from agent.services.audio_decision_hub_gate import (
    AudioDecisionKind,
    AudioDecisionProposal,
    HubAction,
    PolicyVerdict,
    gate_audio_decision,
)
from tests.test_voice_audio_decision_provider import KEY, FakeTransport, _config, _error, _field, _result, _wav
from tests.test_voice_audio_decision_stream import decision_log
from voice_runtime.backends.audio_decision import AudioDecisionProvider, parse_decision_response

MODEL = "base/v51864/l6/f1"
SECRET_TRANSCRIPT = "please unlock the front door right now"
PROFILES = tuple(DEFAULT_COMMAND_CATALOG)


def _outcome(fields: dict, *, profile: str = "speech-commands-en", status: str = "beta", **kwargs):
    payload = _result(fields, profile=profile, **kwargs)
    payload["profile"]["status"] = status
    return parse_decision_response(200, json.dumps(payload).encode(), expected_profile=profile)


def _proposal(value, *, kind=AudioDecisionKind.PROPOSAL, confidence=0.97, profile="speech-commands-en",
              status="beta", model=MODEL, field="command", grants_permission=False):
    return AudioDecisionProposal(
        field=field,
        value=value,
        kind=kind,
        confidence=confidence,
        probability=0.98,
        provenance={"model": model, "profile": {"id": profile, "version": "0.2.0", "status": status}},
        grants_permission=grants_permission,
    )


POLICY = VoiceCommandAudioDecisionPolicy()


# --- rule table --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("proposal", "verdict", "rule"),
    [
        (_proposal("stop", grants_permission=True), PolicyVerdict.DENY, "P0_permission_claim"),
        (_proposal("stop", kind=AudioDecisionKind.NO_VALUE), PolicyVerdict.DENY, "P1_not_a_value"),
        (_proposal("stop", kind=AudioDecisionKind.SYSTEM2), PolicyVerdict.DENY, "P1_not_a_value"),
        (_proposal("stop", profile="unknown-profile"), PolicyVerdict.DENY, "P2_unknown_profile_or_field"),
        (_proposal("stop", field="volume"), PolicyVerdict.DENY, "P2_unknown_profile_or_field"),
        (_proposal("unlock"), PolicyVerdict.DENY, "P3_unknown_label"),
        (_proposal(True, profile="confirm-en-de", field="confirmed", status="experimental"),
         PolicyVerdict.CONFIRM, "P8_confirm_only_profile"),
        (_proposal(1, profile="confirm-en-de", field="confirmed", status="experimental"),
         PolicyVerdict.DENY, "P3_unknown_label"),
        (_proposal("stop", status="retired"), PolicyVerdict.DENY, "P5_unknown_profile_status"),
        (_proposal("stop", kind=AudioDecisionKind.RANKING, confidence=None), PolicyVerdict.CONFIRM,
         "P6_uncalibrated_ranking"),
        (_proposal("stop", confidence=None), PolicyVerdict.DENY, "P7_missing_confidence"),
        (_proposal("stop", status="experimental"), PolicyVerdict.CONFIRM, "P8_confirm_only_profile"),
        (_proposal("lights_on", profile="home-control-en"), PolicyVerdict.CONFIRM, "P8_confirm_only_profile"),
        (_proposal("stop", model="tiny/v51864/l4/f1"), PolicyVerdict.CONFIRM, "P9_model_not_calibrated"),
        (_proposal("stop", confidence=0.8), PolicyVerdict.CONFIRM, "P10_low_confidence"),
        (_proposal("stop", confidence=float("nan")), PolicyVerdict.CONFIRM, "P10_low_confidence"),
        (_proposal("yes"), PolicyVerdict.CONFIRM, "P11_confirmation_required"),
        (_proposal("on"), PolicyVerdict.CONFIRM, "P11_confirmation_required"),
        (_proposal("stop"), PolicyVerdict.ALLOW, "P12_direct_low_risk"),
        (_proposal("left"), PolicyVerdict.ALLOW, "P12_direct_low_risk"),
    ],
)
def test_policy_rules_are_deterministic_and_named(proposal, verdict, rule):
    decision = POLICY.decide(proposal)
    assert (decision.verdict, decision.rule) == (verdict, rule)
    assert POLICY.evaluate(proposal) is verdict


def test_permitted_action_types_restrict_the_catalog():
    policy = VoiceCommandAudioDecisionPolicy(permitted_action_types=frozenset({"voice.control.stop"}))
    assert policy.decide(_proposal("stop")).verdict is PolicyVerdict.ALLOW
    assert policy.decide(_proposal("left")).rule == "P4_action_not_permitted"


def test_policy_threshold_is_validated():
    with pytest.raises(ValueError):
        VoiceCommandAudioDecisionPolicy(allow_min_confidence=0.0)


# --- hard rules through the gate ----------------------------------------------------------------


class ExplodingPolicy:
    def evaluate(self, _proposal):
        raise RuntimeError("policy backend down")


class SpyPolicy:
    def __init__(self, verdict="allow"):
        self.verdict = verdict
        self.calls: list[AudioDecisionProposal] = []

    def evaluate(self, proposal):
        self.calls.append(proposal)
        return self.verdict


def test_decision_value_never_grants_a_permission():
    spy = SpyPolicy()
    hub = gate_audio_decision(
        _outcome({"command": _field("ok", "stop", calibrated=True, confidence=0.99)}), field_name="command", policy=spy
    )
    assert hub.action is HubAction.ACT
    assert spy.calls and all(call.grants_permission is False for call in spy.calls)
    assert hub.grants_permission is False and hub.as_audit_dict()["grants_permission"] is False
    # Without the policy's allow the same confident value is not acted upon.
    denied = gate_audio_decision(
        _outcome({"command": _field("ok", "stop", calibrated=True, confidence=0.99)}),
        field_name="command",
        policy=SpyPolicy("deny"),
    )
    assert denied.action is HubAction.DENY


@pytest.mark.parametrize(
    "code",
    ["unavailable", "deadline_exceeded", "cancelled", "unauthorized", "queue_full", "bad_response", "internal_error"],
)
def test_errors_and_timeouts_never_become_allow_or_a_label(code):
    spy = SpyPolicy()
    hub = gate_audio_decision(
        parse_decision_response(503, json.dumps(_error(code)).encode()), field_name="command", policy=spy
    )
    assert hub.action is HubAction.NORMAL_PATH and hub.value is None and spy.calls == []


@pytest.mark.parametrize("status", ["abstain", "ambiguous", "unsupported", "garbage"])
def test_abstentions_never_become_allow_or_a_default_label(status):
    spy = SpyPolicy()
    raw = _field(status, "stop", reasons=["low_margin"])  # a stray value next to a non-ok status
    hub = gate_audio_decision(_outcome({"command": raw}), field_name="command", policy=spy)
    assert hub.action is HubAction.ASK_AGAIN and hub.value is None and spy.calls == []


def test_calibrated_ok_is_only_a_proposal_and_policy_errors_deny():
    outcome = _outcome({"command": _field("ok", "stop", calibrated=True, confidence=0.99)})
    assert gate_audio_decision(outcome, field_name="command", policy=ExplodingPolicy()).action is HubAction.DENY
    assert gate_audio_decision(outcome, field_name="command", policy=lambda _p: "sure").action is HubAction.DENY
    assert gate_audio_decision(outcome, field_name="command", policy=POLICY).action is HubAction.ACT
    confirm = gate_audio_decision(
        _outcome({"command": _field("ok", "yes", calibrated=True, confidence=0.99)}),
        field_name="command",
        policy=POLICY,
    )
    assert confirm.action is HubAction.CONFIRM


def test_uncalibrated_ok_is_at_most_a_confirmation():
    outcome = _outcome({"command": _field("ok", "stop", calibrated=False, confidence=0.99)})
    hub = gate_audio_decision(outcome, field_name="command", policy=SpyPolicy("allow"))
    assert hub.kind is AudioDecisionKind.RANKING and hub.action is HubAction.CONFIRM and hub.confidence is None
    assert gate_audio_decision(outcome, field_name="command", policy=POLICY).action is HubAction.CONFIRM


def test_transcript_with_system2_required_goes_to_system2():
    outcome = _outcome(
        {"command": _field("abstain", reasons=["out_of_set"])},
        fallback={"used": True, "transcript": SECRET_TRANSCRIPT, "system2_required": True,
                  "provenance": "whisper_full greedy", "reason": "command:abstain"},
    )
    spy = SpyPolicy()
    hub = gate_audio_decision(outcome, field_name="command", policy=spy)
    assert hub.action is HubAction.SYSTEM2 and hub.system2_transcript == SECRET_TRANSCRIPT and hub.value is None
    assert spy.calls == [] and SECRET_TRANSCRIPT not in json.dumps(hub.as_audit_dict())


# --- command flow (provider -> gate -> typed action) --------------------------------------------


def _run(transport: FakeTransport, *, profile="speech-commands-en", field_name="command", policy=None):
    provider = AudioDecisionProvider(_config(profiles=PROFILES), transport=transport)
    return run_audio_decision_command(
        provider,
        filename="clip.wav",
        content=_wav(),
        profile=profile,
        field_name=field_name,
        language="en",
        fallback="transcribe",
        policy=policy,
    )


def _speech_result(field: dict, **kwargs) -> dict:
    payload = _result({"command": field}, profile="speech-commands-en", **kwargs)
    payload["model"] = MODEL
    return payload


def test_command_flow_acts_on_a_calibrated_direct_command():
    result = _run(FakeTransport(200, _speech_result(_field("ok", "stop", calibrated=True, confidence=0.97))))
    response = result.as_response()
    assert response["hub_action"] == "act" and response["decision_kind"] == "proposal"
    assert response["action"] == {
        "type": "voice.control.stop", "command": "stop", "field": "command", "profile": "speech-commands-en",
        "confidence": 0.97, "requires_confirmation": False,
    }
    assert response["policy"] == {"verdict": "allow", "rule": "P12_direct_low_risk"}
    assert response["grants_permission"] is False and response["system2"] is None


def test_command_flow_requests_confirmation_for_state_changes_and_rankings():
    go = _speech_result(_field("ok", "go", calibrated=True, confidence=0.99))
    confirm = _run(FakeTransport(200, go)).as_response()
    assert confirm["hub_action"] == "confirm" and confirm["action"]["requires_confirmation"] is True
    ranking = _run(FakeTransport(200, _speech_result(_field("ok", "stop")))).as_response()
    assert ranking["hub_action"] == "confirm" and ranking["decision_kind"] == "ranking"
    assert ranking["action"]["confidence"] is None and ranking["policy"]["rule"] == "P6_uncalibrated_ranking"


def test_command_flow_policy_exception_denies():
    class Broken(VoiceCommandAudioDecisionPolicy):
        def decide(self, proposal):
            raise RuntimeError("boom")

    result = _run(FakeTransport(200, _speech_result(_field("ok", "stop", calibrated=True, confidence=0.99))),
                  policy=Broken())
    response = result.as_response()
    assert response["hub_action"] == "deny" and response["action"] is None
    assert response["policy"] == {"verdict": "deny", "rule": "policy_error"}


def test_command_flow_errors_take_the_normal_path_without_a_value():
    response = _run(FakeTransport(None, exc=TimeoutError())).as_response()
    assert response["hub_action"] == "normal_path" and response["error_code"] == "deadline_exceeded"
    assert response["action"] is None and response["policy"] is None
    assert response["fallback_route"] == "/v1/voice/command"


def test_command_flow_system2_carries_transcript_only_in_the_response():
    payload = _speech_result(
        _field("abstain", reasons=["out_of_set"]),
        fallback={"used": True, "transcript": SECRET_TRANSCRIPT, "system2_required": True, "provenance": "greedy"},
    )
    with decision_log() as log:
        result = _run(FakeTransport(200, payload))
    response = result.as_response()
    assert response["hub_action"] == "system2" and response["action"] is None
    assert response["system2"]["transcript"] == SECRET_TRANSCRIPT
    assert response["system2"]["requires_approval"] is True
    audit = json.dumps(result.as_audit_dict())
    assert SECRET_TRANSCRIPT not in audit and '"stop"' not in audit
    text = log.getvalue()
    assert "audio decision profile=speech-commands-en ok=True" in text
    assert SECRET_TRANSCRIPT not in text and KEY not in text


def test_command_flow_audit_has_no_label():
    result = _run(FakeTransport(200, _speech_result(_field("ok", "right", calibrated=True, confidence=0.97))))
    audit = result.as_audit_dict()
    assert audit["action"] == "act" and audit["action_type"] == "voice.navigate.right"
    assert "right\"" not in json.dumps({k: v for k, v in audit.items() if k != "action_type"})
