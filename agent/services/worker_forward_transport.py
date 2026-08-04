"""Explicit transport contract for Hub-owned worker forwarding.

The deadline is process-local and monotonic on purpose.  It is created by the
Hub from an authoritative persisted budget and then shared by every retry of
one forwarding operation.  It must never be reconstructed from request data.
"""

from __future__ import annotations

import inspect
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

DEFAULT_WORKER_CONNECT_TIMEOUT_SECONDS = 5.0
MAX_WORKER_CONNECT_TIMEOUT_SECONDS = 10.0


class WorkerForwardTransportError(RuntimeError):
    """Typed transport failure carrying the retry decision."""

    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.retryable = bool(retryable)


class WorkerForwardDeadlineExceeded(WorkerForwardTransportError):
    def __init__(self) -> None:
        super().__init__(
            "worker_forward_transport_deadline_exceeded",
            retryable=False,
        )


class WorkerForwardAmbiguousTransportError(WorkerForwardTransportError):
    """The POST may have reached the Worker but no result was received."""

    def __init__(self) -> None:
        super().__init__(
            "worker_forward_ambiguous_response_loss",
            retryable=True,
        )


class WorkerForwardPermanentTransportError(
    WorkerForwardTransportError,
    ValueError,
):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code, retryable=False)


@dataclass(frozen=True, slots=True)
class WorkerTransportDeadline:
    """Absolute monotonic deadline plus a separately bounded connect budget."""

    expires_at_monotonic: float
    budget_seconds: int
    connect_timeout_seconds: float = (
        DEFAULT_WORKER_CONNECT_TIMEOUT_SECONDS
    )
    _monotonic_clock: Callable[[], float] = field(
        default=time.monotonic,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.budget_seconds, bool)
            or not isinstance(self.budget_seconds, int)
            or self.budget_seconds < 1
            or not math.isfinite(self.expires_at_monotonic)
            or not math.isfinite(self.connect_timeout_seconds)
            or not 0 < self.connect_timeout_seconds
            <= MAX_WORKER_CONNECT_TIMEOUT_SECONDS
            or not callable(self._monotonic_clock)
        ):
            raise ValueError("worker_forward_transport_deadline_invalid")

    @classmethod
    def after_seconds(
        cls,
        budget_seconds: int,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        connect_timeout_seconds: float = (
            DEFAULT_WORKER_CONNECT_TIMEOUT_SECONDS
        ),
    ) -> "WorkerTransportDeadline":
        if isinstance(budget_seconds, bool) or not isinstance(
            budget_seconds,
            int,
        ):
            raise ValueError("worker_forward_transport_deadline_invalid")
        now = float(monotonic_clock())
        return cls(
            expires_at_monotonic=now + budget_seconds,
            budget_seconds=budget_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            _monotonic_clock=monotonic_clock,
        )

    def remaining_seconds(
        self,
        *,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> float:
        clock = monotonic_clock or self._monotonic_clock
        return max(
            0.0,
            self.expires_at_monotonic - float(clock()),
        )

    def require_remaining_seconds(
        self,
        *,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> float:
        remaining = self.remaining_seconds(
            monotonic_clock=monotonic_clock,
        )
        if remaining <= 0:
            raise WorkerForwardDeadlineExceeded()
        return remaining

    def requests_timeout(
        self,
        *,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> tuple[float, float]:
        remaining = self.require_remaining_seconds(
            monotonic_clock=monotonic_clock,
        )
        return (
            min(self.connect_timeout_seconds, remaining),
            remaining,
        )


@runtime_checkable
class DeadlineAwareWorkerForwarder(Protocol):
    """Port required for governed forwarding with an absolute deadline."""

    def __call__(
        self,
        worker_url: str,
        endpoint: str,
        data: dict[str, Any],
        token: str | None = None,
        *,
        transport_deadline: WorkerTransportDeadline | None = None,
    ) -> Any: ...


def _accepts_transport_deadline(forwarder: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(forwarder)
    except (TypeError, ValueError):
        return False
    return bool(
        "transport_deadline" in signature.parameters
        or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    )


def invoke_worker_forwarder(
    forwarder: Callable[..., Any],
    worker_url: str,
    endpoint: str,
    data: dict[str, Any],
    *,
    token: str | None,
    transport_deadline: WorkerTransportDeadline | None,
) -> Any:
    """Invoke an old or deadline-aware adapter without a silent downgrade."""

    if transport_deadline is None:
        return forwarder(
            worker_url,
            endpoint,
            data,
            token=token,
        )
    if not _accepts_transport_deadline(forwarder):
        raise WorkerForwardPermanentTransportError(
            "worker_forward_deadline_transport_unsupported"
        )
    transport_deadline.require_remaining_seconds()
    return forwarder(
        worker_url,
        endpoint,
        data,
        token=token,
        transport_deadline=transport_deadline,
    )


__all__ = [
    "DeadlineAwareWorkerForwarder",
    "WorkerForwardAmbiguousTransportError",
    "WorkerForwardDeadlineExceeded",
    "WorkerForwardPermanentTransportError",
    "WorkerForwardTransportError",
    "WorkerTransportDeadline",
    "invoke_worker_forwarder",
]
