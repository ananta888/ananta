"""CaseFlow Status Machine — validates status transitions per case type."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TransitionResult:
    valid: bool
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    suggested_event_type: Optional[str] = None


class CaseStatusDefinition:
    """Defines allowed status transitions for a case type."""

    def __init__(
        self,
        statuses: list[str],
        initial_status: str,
        terminal_statuses: list[str],
        transitions: dict[str, list[str]],
    ) -> None:
        self.statuses = statuses
        self.initial_status = initial_status
        self.terminal_statuses = terminal_statuses
        self.transitions = transitions  # from_status -> [allowed_to_statuses]

    def validate_transition(
        self,
        from_status: str,
        to_status: str,
        actor: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> TransitionResult:
        if from_status not in self.statuses:
            return TransitionResult(
                valid=False,
                error_code="UNKNOWN_FROM_STATUS",
                error_detail=f"Status '{from_status}' is not defined for this case type.",
            )
        if to_status not in self.statuses:
            return TransitionResult(
                valid=False,
                error_code="UNKNOWN_TO_STATUS",
                error_detail=f"Status '{to_status}' is not defined for this case type.",
            )
        if from_status in self.terminal_statuses:
            return TransitionResult(
                valid=False,
                error_code="TERMINAL_STATUS",
                error_detail=f"Cannot transition from terminal status '{from_status}'.",
            )
        allowed = self.transitions.get(from_status, [])
        if to_status not in allowed:
            return TransitionResult(
                valid=False,
                error_code="TRANSITION_NOT_ALLOWED",
                error_detail=(
                    f"Transition from '{from_status}' to '{to_status}' is not allowed. "
                    f"Allowed: {allowed}"
                ),
            )
        return TransitionResult(valid=True, suggested_event_type="status_changed")


# Registry
_status_machines: dict[str, CaseStatusDefinition] = {}


def register_status_machine(case_type: str, machine: CaseStatusDefinition) -> None:
    _status_machines[case_type] = machine


def get_status_machine(case_type: str) -> Optional[CaseStatusDefinition]:
    return _status_machines.get(case_type)


# Default generic status machine
_GENERIC_MACHINE = CaseStatusDefinition(
    statuses=["new", "active", "waiting", "action_required", "done", "archived"],
    initial_status="new",
    terminal_statuses=["done", "archived"],
    transitions={
        "new": ["active", "waiting", "archived"],
        "active": ["waiting", "action_required", "done", "archived"],
        "waiting": ["active", "action_required", "archived"],
        "action_required": ["active", "waiting", "done", "archived"],
    },
)
register_status_machine("generic", _GENERIC_MACHINE)
