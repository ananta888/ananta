"""Contracts for AudioDecision voice-command policy and service."""
from __future__ import annotations

import pytest

from agent.services.audio_decision_command_policy import (
    DEFAULT_COMMAND_CATALOG,
    PolicyDecision,
    VoiceCommandAudioDecisionPolicy,
)
from agent.services.audio_decision_command_service import run_audio_decision_command
from agent.services.audio_decision_hub_gate import AudioDecisionProposal, PolicyVerdict


class FakeProvider:
    def __init__(self, proposal):
        self.proposal = proposal

    def decide(self, *, filename, content, profile, fields, language, fallback, cancellation_token=None):
        return self.proposal


def _proposal(**overrides) -> AudioDecisionProposal:
    defaults = {
        "kind": "proposal",
        "profile": "speech-commands-en",
        "field": "command",
        "value": "stop",
        "confidence": 0.95,
        "provenance": {"model": "base/v51864/l6/f1", "profile": {"id": "speech-commands-en", "status": "stable"}},
        "grants_permission": False,
    }
    defaults.update(overrides)
    return AudioDecisionProposal(**defaults)


def test_policy_denies_permission_claim():
    policy = VoiceCommandAudioDecisionPolicy()
    decision = policy.decide(_proposal(grants_permission=True))
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.rule == "P0_permission_claim"


def test_policy_allows_direct_low_risk_stop():
    policy = VoiceCommandAudioDecisionPolicy()
    decision = policy.decide(_proposal())
    assert decision.verdict is PolicyVerdict.ALLOW
    assert decision.rule == "P12_direct_low_risk"
    assert decision.action is not None and decision.action.label == "stop"


def test_policy_confirms_yes():
    policy = VoiceCommandAudioDecisionPolicy()
    decision = policy.decide(_proposal(value="yes"))
    assert decision.verdict is PolicyVerdict.CONFIRM
    assert decision.rule == "P11_confirmation_required"


def test_policy_denies_unknown_label():
    policy = VoiceCommandAudioDecisionPolicy()
    decision = policy.decide(_proposal(value="maybe"))
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.rule == "P3_unknown_label"


def test_service_runs_provider_and_policy():
    proposal = _proposal()
    provider = FakeProvider(proposal)
    result = run_audio_decision_command(
        provider,
        filename="a.wav",
        content=b"x",
        profile="speech-commands-en",
        field_name="command",
        language="en",
        fallback="normal",
    )
    assert result.hub.action.value == "act"
    assert result.policy is not None and result.policy.verdict is PolicyVerdict.ALLOW
    assert result.action is not None and result.action.label == "stop"
