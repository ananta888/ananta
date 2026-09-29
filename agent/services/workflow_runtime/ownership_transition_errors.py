"""Error taxonomy for workflow transition ownership reservations."""

from __future__ import annotations


class WorkflowTransitionOwnershipReservationError(RuntimeError):
    """Stable transition-only ownership persistence failure."""


class WorkflowTransitionOwnershipReservationConflict(WorkflowTransitionOwnershipReservationError):
    """A proven binding, projection, or partial-state conflict."""


class WorkflowTransitionOwnershipReservationStale(WorkflowTransitionOwnershipReservationError):
    """A retryable optimistic observation or compare-and-set loss."""


class WorkflowTransitionOwnershipReservationHeld(WorkflowTransitionOwnershipReservationError):
    """Another exact live owner currently holds the requested step."""


class WorkflowTransitionOwnershipReservationUnavailable(WorkflowTransitionOwnershipReservationError):
    """The authoritative reservation snapshot could not be read."""
