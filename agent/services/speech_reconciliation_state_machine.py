"""Table-driven, authority-fenced lifecycle for offline reconciliation."""

from __future__ import annotations

from ananta_contracts.speech_reconciliation_state import (
    TERMINAL_STATES,
    TRANSITIONS,
    SpeechReconciliationTransition,
    validate_stage,
    validate_state,
)


class SpeechReconciliationStateError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SpeechReconciliationStateMachine:
    def transition(
        self,
        current_state: str,
        target_state: str,
        *,
        stage: str,
        reason_code: str,
        authority: str = "hub",
    ) -> SpeechReconciliationTransition:
        if authority != "hub":
            raise SpeechReconciliationStateError("speech_reconciliation_hub_authority_required")
        try:
            current = validate_state(current_state)
            target = validate_state(target_state)
            validated_stage = validate_stage(stage)
        except ValueError as exc:
            raise SpeechReconciliationStateError(str(exc)) from exc
        reason = str(reason_code or "").strip()
        if not reason or len(reason) > 128 or any(character.isspace() for character in reason):
            raise SpeechReconciliationStateError("speech_reconciliation_reason_invalid")
        if target == current:
            return SpeechReconciliationTransition(current, target, validated_stage, reason, duplicate=True)
        if current in TERMINAL_STATES:
            raise SpeechReconciliationStateError("speech_reconciliation_late_event")
        if target not in TRANSITIONS[current]:
            raise SpeechReconciliationStateError("speech_reconciliation_transition_invalid")
        return SpeechReconciliationTransition(current, target, validated_stage, reason)


__all__ = ["SpeechReconciliationStateError", "SpeechReconciliationStateMachine"]
