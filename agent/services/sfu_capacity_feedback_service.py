"""Conservative Hub capacity feedback from aggregate egress observations."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Literal, Protocol


@dataclass(frozen=True, slots=True)
class SfuCapacityFeedbackSample:
    tenant_id: str
    room_id: str
    publication_id: str
    node_id: str
    route_epoch: int
    topology_epoch: int
    fencing_token: int
    observed_at_ms: int
    expires_at_ms: int
    aggregate_window_count: int
    value_kind: Literal["actual", "estimated", "missing"]
    egress_bps: int | None
    hard_egress_bps_max: int
    active_receivers: int
    current_receiver_cap: int
    current_spatial_layer_cap: int
    reconciliation_reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SfuCapacityFeedbackDecision:
    tenant_id: str
    room_id: str
    publication_id: str
    route_epoch: int
    topology_epoch: int
    fencing_token: int
    admission_allowed: bool
    receiver_cap: int
    spatial_layer_cap: int
    smoothed_utilization_basis_points: int
    reason_code: str
    expires_at_ms: int


class SfuCapacityFeedbackReadPort(Protocol):
    def current(self, *, tenant_id: str, room_id: str,
                publication_id: str) -> SfuCapacityFeedbackDecision | None: ...


class SfuCapacityFeedbackService(SfuCapacityFeedbackReadPort):
    """Never raises a reservation, right, receiver cap, or layer cap."""

    def __init__(self, *, alpha_basis_points: int = 2500,
                 entries_max: int = 1024,
                 clock: Callable[[], float] = time.time,
                 control_observer: SfuBroadcastControlObservationPort | None = None) -> None:
        if not 1 <= alpha_basis_points <= 5000 or not 1 <= entries_max <= 4096:
            raise ValueError("sfu_capacity_feedback_bounds_invalid")
        self._alpha = alpha_basis_points
        self._entries_max = entries_max
        self._clock = clock
        self._control_observer = control_observer_or_null(control_observer)
        self._decisions: OrderedDict[tuple[str, str, str], SfuCapacityFeedbackDecision] = OrderedDict()

    @observed_control_path("capacity_feedback")
    def evaluate(self, sample: SfuCapacityFeedbackSample) -> SfuCapacityFeedbackDecision:
        _validate(sample)
        key = (sample.tenant_id, sample.room_id, sample.publication_id)
        previous = self._decisions.get(key)
        now_ms = int(self._clock() * 1000)
        stale = sample.expires_at_ms <= now_ms or sample.observed_at_ms > now_ms + 5000
        unsafe_reason = next((reason for reason in sample.reconciliation_reason_codes if any(
            marker in reason for marker in ("gap", "regression", "restart", "divergence", "missing")
        )), None)
        if sample.egress_bps is None:
            utilization = 10_000
        else:
            utilization = min(20_000, sample.egress_bps * 10_000 // max(1, sample.hard_egress_bps_max))
        previous_utilization = previous.smoothed_utilization_basis_points if previous \
            and previous.route_epoch == sample.route_epoch \
            and previous.topology_epoch == sample.topology_epoch \
            and previous.fencing_token == sample.fencing_token else utilization
        smoothed = (
            previous_utilization * (10_000 - self._alpha) + utilization * self._alpha
        ) // 10_000
        effective = max(utilization, smoothed)
        receiver_cap = sample.current_receiver_cap
        layer_cap = sample.current_spatial_layer_cap
        admission = True
        reason = "sfu_capacity_feedback_within_limit"
        if stale or sample.aggregate_window_count < 2 or sample.value_kind == "missing" or unsafe_reason:
            admission = False
            receiver_cap = min(receiver_cap, sample.active_receivers)
            layer_cap = 0
            reason = unsafe_reason or ("sfu_capacity_feedback_stale" if stale else "sfu_capacity_feedback_unknown")
        elif sample.value_kind == "estimated":
            receiver_cap = min(receiver_cap, max(1, sample.active_receivers))
            layer_cap = max(0, layer_cap - 1)
            reason = "sfu_capacity_feedback_estimated_downshift"
        elif effective >= 9000:
            admission = False
            receiver_cap = min(receiver_cap, sample.active_receivers)
            layer_cap = 0
            reason = "sfu_capacity_feedback_hard_limit"
        elif effective >= 7500:
            receiver_cap = min(receiver_cap, max(1, sample.active_receivers))
            layer_cap = max(0, layer_cap - 1)
            reason = "sfu_capacity_feedback_layer_cap_lowered"
        decision = SfuCapacityFeedbackDecision(
            sample.tenant_id, sample.room_id, sample.publication_id,
            sample.route_epoch, sample.topology_epoch, sample.fencing_token,
            admission, receiver_cap, layer_cap, effective, reason,
            min(sample.expires_at_ms, now_ms + 10_000),
        )
        if key not in self._decisions and len(self._decisions) >= self._entries_max:
            oldest, _ = self._decisions.popitem(last=False)
            del oldest
        self._decisions[key] = decision
        self._decisions.move_to_end(key)
        return decision

    def current(self, *, tenant_id: str, room_id: str,
                publication_id: str) -> SfuCapacityFeedbackDecision | None:
        value = self._decisions.get((tenant_id, room_id, publication_id))
        if value is None or value.expires_at_ms <= int(self._clock() * 1000):
            return None
        return value


def _validate(value: SfuCapacityFeedbackSample) -> None:
    for item in (value.tenant_id, value.room_id, value.publication_id, value.node_id):
        if not isinstance(item, str) or not item or len(item.encode()) > 128:
            raise ValueError("sfu_capacity_feedback_scope_invalid")
    integers = (
        value.route_epoch, value.topology_epoch, value.fencing_token,
        value.observed_at_ms, value.expires_at_ms, value.aggregate_window_count,
        value.hard_egress_bps_max, value.active_receivers,
        value.current_receiver_cap, value.current_spatial_layer_cap,
    )
    if any(type(item) is not int or item < 0 for item in integers) \
            or min(value.route_epoch, value.topology_epoch, value.fencing_token,
                   value.hard_egress_bps_max) < 1 \
            or value.current_spatial_layer_cap > 3 \
            or value.expires_at_ms - value.observed_at_ms > 30_000:
        raise ValueError("sfu_capacity_feedback_bounds_invalid")
    if value.egress_bps is not None and (type(value.egress_bps) is not int or value.egress_bps < 0):
        raise ValueError("sfu_capacity_feedback_egress_invalid")


__all__ = [
    "SfuCapacityFeedbackDecision", "SfuCapacityFeedbackReadPort",
    "SfuCapacityFeedbackSample", "SfuCapacityFeedbackService",
]
