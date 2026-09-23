"""Voice command flow over the optional AudioDecision specialist.

Audio -> ``AudioDecisionProvider.decide`` -> ``DecisionOutcome`` -> ``gate_audio_decision`` with
``VoiceCommandAudioDecisionPolicy`` -> typed hub action. The provider is built lazily from the
environment; with ``VOICE_AUDIO_DECISION_ENABLED`` off nothing else is read and the service is
never contacted. Audit projections carry no audio, transcript, label or key.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.audio_decision_command_policy import (
    PolicyDecision,
    VoiceCommandAction,
    VoiceCommandAudioDecisionPolicy,
)
from agent.services.audio_decision_hub_gate import (
    AudioDecisionProposal,
    HubAction,
    HubAudioDecision,
    PolicyVerdict,
    gate_audio_decision,
)
from voice_runtime.backends.audio_decision import (
    AudioDecisionConfig,
    AudioDecisionConfigurationError,
    AudioDecisionProvider,
    build_audio_decision_provider,
)
from voice_runtime.execution_control import BackendCancellationToken

# Hub routes stay free of voice_runtime imports (architecture boundary): they use these names.
__all__ = [
    "AudioDecisionCommandResult",
    "AudioDecisionConfigurationError",
    "cancellation_token_for",
    "get_audio_decision_provider",
    "run_audio_decision_command",
]

_ENV_KEYS = (
    "VOICE_AUDIO_DECISION_ENABLED",
    "VOICE_AUDIO_DECISION_URL",
    "VOICE_AUDIO_DECISION_API_KEY_FILE",
    "VOICE_AUDIO_DECISION_API_KEY",
    "VOICE_AUDIO_DECISION_TIMEOUT_MS",
    "VOICE_AUDIO_DECISION_PROFILES",
    "VOICE_AUDIO_DECISION_SEMANTIC_PROFILES",
)
_provider_lock = threading.Lock()
_provider_cache: tuple[tuple[str | None, ...], AudioDecisionProvider] | None = None

SYSTEM2_GOAL_ROUTE = "/v1/voice/goal"
NORMAL_PATH_ROUTE = "/v1/voice/command"
_MAX_PROPOSED_GOAL_CHARS = 400


def cancellation_token_for(budget_seconds: float) -> BackendCancellationToken:
    return BackendCancellationToken(deadline_monotonic=time.monotonic() + budget_seconds)


def get_audio_decision_provider(environ: Mapping[str, str] | None = None) -> AudioDecisionProvider | None:
    """``None`` when the feature is off. Raises ``AudioDecisionConfigurationError`` if misconfigured."""
    global _provider_cache
    env = os.environ if environ is None else environ
    config = AudioDecisionConfig.from_env(env)
    if not config.enabled:
        return None
    key = tuple(env.get(name) for name in _ENV_KEYS)
    with _provider_lock:
        if _provider_cache is None or _provider_cache[0] != key:
            provider = build_audio_decision_provider(config)
            assert provider is not None
            _provider_cache = (key, provider)
        return _provider_cache[1]


@dataclass(frozen=True)
class AudioDecisionCommandResult:
    hub: HubAudioDecision
    profile: str
    policy: PolicyDecision | None
    action: VoiceCommandAction | None

    def as_response(self) -> dict[str, Any]:
        hub = self.hub
        typed_action = None
        if hub.action in {HubAction.ACT, HubAction.CONFIRM} and self.action is not None:
            typed_action = {
                "type": self.action.action_type,
                "command": hub.value,
                "field": hub.field,
                "profile": self.profile,
                "confidence": hub.confidence,
                "requires_confirmation": hub.action is not HubAction.ACT,
            }
        system2 = None
        if hub.action is HubAction.SYSTEM2 and hub.system2_transcript:
            system2 = {
                "transcript": hub.system2_transcript,
                "proposed_goal": hub.system2_transcript[:_MAX_PROPOSED_GOAL_CHARS],
                "matched_from_transcript": hub.matched_from_transcript,
                "requires_approval": True,
                "goal_route": SYSTEM2_GOAL_ROUTE,
            }
        return {
            "enabled": True,
            "hub_action": hub.action.value,
            "decision_kind": hub.kind.value,
            "action": typed_action,
            "policy": self._policy_dict(),
            "system2": system2,
            "reasons": list(hub.reasons),
            "error_code": hub.error_code,
            "fallback_route": NORMAL_PATH_ROUTE if hub.action is HubAction.NORMAL_PATH else None,
            "provenance": dict(hub.provenance),
            "grants_permission": False,
        }

    def as_audit_dict(self) -> dict[str, Any]:
        """No transcript, no label, no audio."""
        return {
            **self.hub.as_audit_dict(),
            "profile": self.profile,
            "policy": self._policy_dict(),
            "action_type": self.action.action_type if self.action is not None else None,
        }

    def _policy_dict(self) -> dict[str, str] | None:
        if self.policy is None:
            return None
        return {"verdict": self.policy.verdict.value, "rule": self.policy.rule}


def run_audio_decision_command(
    provider: AudioDecisionProvider,
    *,
    filename: str,
    content: bytes,
    profile: str,
    field_name: str,
    language: str,
    fallback: str,
    policy: VoiceCommandAudioDecisionPolicy | None = None,
    cancellation_token: BackendCancellationToken | None = None,
) -> AudioDecisionCommandResult:
    resolved = policy or VoiceCommandAudioDecisionPolicy()
    outcome = provider.decide(
        filename=filename,
        content=content,
        profile=profile,
        fields=(field_name,),
        language=language,
        fallback=fallback,
        cancellation_token=cancellation_token,
    )
    decisions: list[PolicyDecision] = []

    def recording_policy(proposal: AudioDecisionProposal):
        decision = resolved.decide(proposal)
        decisions.append(decision)
        return decision.verdict

    hub = gate_audio_decision(outcome, field_name=field_name, policy=recording_policy)
    action = resolved.action_for(profile, field_name, hub.value) if hub.value is not None else None
    policy_decision = decisions[-1] if decisions else None
    if policy_decision is None and hub.action is HubAction.DENY:
        # The policy raised: gate_audio_decision already denied fail-closed.
        policy_decision = PolicyDecision(PolicyVerdict.DENY, "policy_error")
    return AudioDecisionCommandResult(hub=hub, profile=profile, policy=policy_decision, action=action)
