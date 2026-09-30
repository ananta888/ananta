"""Compatibility re-export of the offline speech reconciliation state machine.

The table-driven lifecycle is pure domain logic over
``ananta_contracts.speech_reconciliation_state`` and lives in
:mod:`agent.models.speech_reconciliation_state_machine`, so the persistence
adapter can use it without importing the service layer.
"""

from __future__ import annotations

from agent.models.speech_reconciliation_state_machine import (
    SpeechReconciliationStateError,
    SpeechReconciliationStateMachine,
)

__all__ = ["SpeechReconciliationStateError", "SpeechReconciliationStateMachine"]
