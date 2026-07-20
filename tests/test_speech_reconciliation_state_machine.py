from __future__ import annotations

import pytest

from agent.services.speech_reconciliation_state_machine import (
    SpeechReconciliationStateError,
    SpeechReconciliationStateMachine,
)
from ananta_contracts.speech_reconciliation_state import TERMINAL_STATES, TRANSITIONS


def test_transition_table_accepts_every_declared_edge_and_duplicate() -> None:
    machine = SpeechReconciliationStateMachine()
    for source, targets in TRANSITIONS.items():
        duplicate = machine.transition(source, source, stage="admission", reason_code="idempotent_retry")
        assert duplicate.duplicate
        for target in targets:
            result = machine.transition(source, target, stage="staging", reason_code="valid_transition")
            assert result.target_state == target and not result.duplicate


def test_late_invalid_and_worker_events_fail_closed() -> None:
    machine = SpeechReconciliationStateMachine()
    for terminal in TERMINAL_STATES:
        with pytest.raises(SpeechReconciliationStateError, match="speech_reconciliation_late_event"):
            machine.transition(terminal, "running", stage="staging", reason_code="late_event")
    with pytest.raises(SpeechReconciliationStateError, match="speech_reconciliation_transition_invalid"):
        machine.transition("queued", "completed", stage="finalization", reason_code="skip_execution")
    with pytest.raises(SpeechReconciliationStateError, match="speech_reconciliation_hub_authority_required"):
        machine.transition("queued", "running", stage="staging", reason_code="worker_claim", authority="worker")
