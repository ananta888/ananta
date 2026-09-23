"""Hub policy for AudioDecision voice commands (``AudioDecisionPolicy`` implementation).

The AudioDecision provider only proposes a value; this policy decides whether the hub may act
on it. Rules are evaluated in order and the first match wins; every decision names its rule:

======  ====================================================================  =========
Rule    Condition                                                             Verdict
======  ====================================================================  =========
P0      the proposal claims ``grants_permission``                             deny
P1      kind is neither ``proposal`` nor ``ranking``                          deny
P2      profile or field is not in the command catalog                        deny
P3      value is not a known label of that field                              deny
P4      the mapped hub action is not permitted for this policy instance       deny
P5      profile status is unknown (not experimental/beta/stable)              deny
P6      ``ranking`` (uncalibrated): an ordering, never a reliable value       confirm
P7      proposal without confidence                                           deny
P8      experimental profile or confirm-only profile/field                    confirm
P9      serving model is not the model the profile was calibrated for         confirm
P10     calibrated confidence below ``allow_min_confidence``                  confirm
P11     action needs a confirmation by design (state change, affirmation)     confirm
P12     otherwise (calibrated, confident, low-risk direct action)             allow
======  ====================================================================  =========

``allow`` only lets the hub return a typed low-risk action; nothing in a decision value is a
permission by itself (``gate_audio_decision`` enforces ``grants_permission=False`` and turns
policy exceptions into ``deny``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agent.services.audio_decision_hub_gate import AudioDecisionKind, AudioDecisionProposal, PolicyVerdict

KNOWN_PROFILE_STATUSES = frozenset({"experimental", "beta", "stable"})
DEFAULT_ALLOW_MIN_CONFIDENCE = 0.9


@dataclass(frozen=True)
class VoiceCommandAction:
    """Typed hub action a decision label maps to."""

    label: str | bool
    action_type: str
    direct: bool = False  # False: always needs an explicit confirmation (P11)


@dataclass(frozen=True)
class VoiceCommandField:
    name: str
    actions: Mapping[str | bool, VoiceCommandAction]
    confirm_only: bool = False


@dataclass(frozen=True)
class VoiceCommandProfile:
    profile_id: str
    language: str
    fields: Mapping[str, VoiceCommandField]
    calibrated_models: frozenset[str] = frozenset()
    confirm_only: bool = False
    system2_only: bool = False  # never scored directly; only the transcript is used

    @property
    def default_field(self) -> str:
        return next(iter(self.fields))


def _field(name: str, actions: tuple[VoiceCommandAction, ...], *, confirm_only: bool = False) -> VoiceCommandField:
    return VoiceCommandField(name=name, actions={item.label: item for item in actions}, confirm_only=confirm_only)


# speech-commands-en (beta, calibrated for base.en): only cancelling and navigating words may run
# directly; "yes"/"go"/"on"/"off" change state or affirm something and always need a confirmation.
_SPEECH_COMMANDS = VoiceCommandProfile(
    profile_id="speech-commands-en",
    language="en",
    calibrated_models=frozenset({"base/v51864/l6/f1"}),
    fields={
        "command": _field(
            "command",
            (
                VoiceCommandAction("stop", "voice.control.stop", direct=True),
                VoiceCommandAction("no", "voice.dialog.reject", direct=True),
                VoiceCommandAction("up", "voice.navigate.up", direct=True),
                VoiceCommandAction("down", "voice.navigate.down", direct=True),
                VoiceCommandAction("left", "voice.navigate.left", direct=True),
                VoiceCommandAction("right", "voice.navigate.right", direct=True),
                VoiceCommandAction("yes", "voice.dialog.affirm"),
                VoiceCommandAction("go", "voice.control.start"),
                VoiceCommandAction("on", "voice.control.switch_on"),
                VoiceCommandAction("off", "voice.control.switch_off"),
            ),
        )
    },
)

# home-control-en is beta but validated with synthetic voices only: confirm everything.
_HOME_CONTROL = VoiceCommandProfile(
    profile_id="home-control-en",
    language="en",
    calibrated_models=frozenset({"base/v51864/l6/f1"}),
    confirm_only=True,
    fields={
        "command": _field(
            "command",
            tuple(
                VoiceCommandAction(label, f"home.{label}")
                for label in ("lights_on", "lights_off", "music_play", "music_stop", "volume_up", "volume_down", "stop")
            ),
        ),
        "confirmed": _field(
            "confirmed",
            (VoiceCommandAction(True, "voice.dialog.affirm"), VoiceCommandAction(False, "voice.dialog.reject")),
            confirm_only=True,
        ),
    },
)

# confirm-en-de is experimental (German not validated): a spoken yes/no is at most a hint.
_CONFIRM = VoiceCommandProfile(
    profile_id="confirm-en-de",
    language="en",
    confirm_only=True,
    fields={
        "confirmed": _field(
            "confirmed",
            (VoiceCommandAction(True, "voice.dialog.affirm"), VoiceCommandAction(False, "voice.dialog.reject")),
            confirm_only=True,
        )
    },
)

# Abstract intents are at chance level when scored directly: System-2 over the transcript only.
_SEMANTIC = VoiceCommandProfile(
    profile_id="intent-semantic-experimental",
    language="en",
    confirm_only=True,
    system2_only=True,
    fields={"intent": _field("intent", ())},
)

DEFAULT_COMMAND_CATALOG: Mapping[str, VoiceCommandProfile] = {
    item.profile_id: item for item in (_SPEECH_COMMANDS, _HOME_CONTROL, _CONFIRM, _SEMANTIC)
}


@dataclass(frozen=True)
class PolicyDecision:
    verdict: PolicyVerdict
    rule: str
    action: VoiceCommandAction | None = None


@dataclass(frozen=True)
class VoiceCommandAudioDecisionPolicy:
    """Deterministic rule table above; ``evaluate`` satisfies ``AudioDecisionPolicy``."""

    catalog: Mapping[str, VoiceCommandProfile] = field(default_factory=lambda: DEFAULT_COMMAND_CATALOG)
    allow_min_confidence: float = DEFAULT_ALLOW_MIN_CONFIDENCE
    permitted_action_types: frozenset[str] | None = None  # None: every catalog action

    def __post_init__(self) -> None:
        if not 0.0 < self.allow_min_confidence <= 1.0:
            raise ValueError("allow_min_confidence must be within (0, 1]")

    def evaluate(self, proposal: AudioDecisionProposal) -> PolicyVerdict:
        return self.decide(proposal).verdict

    def action_for(self, profile_id: str | None, field_name: str, value: object) -> VoiceCommandAction | None:
        profile = self.catalog.get(str(profile_id))
        command_field = profile.fields.get(field_name) if profile is not None else None
        if command_field is None or isinstance(value, (dict, list)):
            return None
        try:
            action = command_field.actions.get(value)  # type: ignore[call-overload]
        except TypeError:
            return None
        # 1 == True in Python: a label only matches a value of exactly the same type.
        return action if action is not None and type(action.label) is type(value) else None

    def decide(self, proposal: AudioDecisionProposal) -> PolicyDecision:
        if proposal.grants_permission is not False:
            return PolicyDecision(PolicyVerdict.DENY, "P0_permission_claim")
        if proposal.kind not in {AudioDecisionKind.PROPOSAL, AudioDecisionKind.RANKING}:
            return PolicyDecision(PolicyVerdict.DENY, "P1_not_a_value")
        provenance_profile = proposal.provenance.get("profile") if isinstance(proposal.provenance, Mapping) else None
        profile_meta = provenance_profile if isinstance(provenance_profile, Mapping) else {}
        profile = self.catalog.get(str(profile_meta.get("id")))
        if profile is None or proposal.field not in profile.fields:
            return PolicyDecision(PolicyVerdict.DENY, "P2_unknown_profile_or_field")
        command_field = profile.fields[proposal.field]
        action = self.action_for(profile.profile_id, proposal.field, proposal.value)
        if action is None:
            return PolicyDecision(PolicyVerdict.DENY, "P3_unknown_label")
        if self.permitted_action_types is not None and action.action_type not in self.permitted_action_types:
            return PolicyDecision(PolicyVerdict.DENY, "P4_action_not_permitted", action)
        status = profile_meta.get("status")
        if status not in KNOWN_PROFILE_STATUSES:
            return PolicyDecision(PolicyVerdict.DENY, "P5_unknown_profile_status", action)
        if proposal.kind is AudioDecisionKind.RANKING:
            return PolicyDecision(PolicyVerdict.CONFIRM, "P6_uncalibrated_ranking", action)
        if proposal.confidence is None:
            return PolicyDecision(PolicyVerdict.DENY, "P7_missing_confidence", action)
        if status == "experimental" or profile.confirm_only or command_field.confirm_only:
            return PolicyDecision(PolicyVerdict.CONFIRM, "P8_confirm_only_profile", action)
        if proposal.provenance.get("model") not in profile.calibrated_models:
            return PolicyDecision(PolicyVerdict.CONFIRM, "P9_model_not_calibrated", action)
        if not proposal.confidence >= self.allow_min_confidence:
            return PolicyDecision(PolicyVerdict.CONFIRM, "P10_low_confidence", action)
        if not action.direct:
            return PolicyDecision(PolicyVerdict.CONFIRM, "P11_confirmation_required", action)
        return PolicyDecision(PolicyVerdict.ALLOW, "P12_direct_low_risk", action)
