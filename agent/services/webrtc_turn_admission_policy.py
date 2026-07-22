"""Atomic Hub-side TURN quota, cost and reservation policy."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Mapping, Protocol


class WebrtcTurnAdmissionError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnQuotaVector:
    allocations: int = 0
    ports: int = 0
    receivers: int = 0
    bps: int = 0
    bytes_per_window: int = 0
    cost_units: float = 0.0

    def __post_init__(self) -> None:
        for name, value in self.values().items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise WebrtcTurnAdmissionError(f"turn_quota_{name}_invalid", 503)
        if any(not isinstance(getattr(self, name), int) for name in ("allocations", "ports", "receivers", "bps", "bytes_per_window")):
            raise WebrtcTurnAdmissionError("turn_quota_integer_unit_invalid", 503)

    def values(self) -> dict[str, int | float]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def plus(self, other: "TurnQuotaVector") -> "TurnQuotaVector":
        return TurnQuotaVector(**{name: self.values()[name] + other.values()[name] for name in self.values()})

    def exceeds(self, limit: "TurnQuotaVector") -> bool:
        return any(self.values()[name] > limit.values()[name] for name in self.values())


@dataclass(frozen=True, slots=True)
class TurnQuotaPolicyConfig:
    limits: Mapping[str, TurnQuotaVector]
    cost_per_gib_ingress: float
    cost_per_gib_egress: float
    reservation_ttl_seconds: int
    lower_cap_order: tuple[str, ...]

    @classmethod
    def from_path(cls, path: Path) -> "TurnQuotaPolicyConfig":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if set(raw) != {"version", "reservation_ttl_seconds", "lower_cap_order", "unit_costs", "limits"}:
                raise ValueError
            if raw["version"] != "1.0" or set(raw["limits"]) != {"receiver", "room", "tenant", "region", "pool"}:
                raise ValueError
            unit_costs = raw["unit_costs"]
            if set(unit_costs) != {"ingress_per_gib", "egress_per_gib"}:
                raise ValueError
            limits = {scope: TurnQuotaVector(**values) for scope, values in raw["limits"].items()}
            return cls(
                limits,
                float(unit_costs["ingress_per_gib"]),
                float(unit_costs["egress_per_gib"]),
                int(raw["reservation_ttl_seconds"]),
                tuple(raw["lower_cap_order"]),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise WebrtcTurnAdmissionError("turn_quota_config_invalid", 503) from exc

    def __post_init__(self) -> None:
        costs = (self.cost_per_gib_ingress, self.cost_per_gib_egress)
        if any(not math.isfinite(value) or value < 0 for value in costs):
            raise WebrtcTurnAdmissionError("turn_quota_cost_config_invalid", 503)
        if self.reservation_ttl_seconds <= 0 or not self.lower_cap_order:
            raise WebrtcTurnAdmissionError("turn_quota_config_invalid", 503)
        if any(layer not in {"high", "medium", "low", "none"} for layer in self.lower_cap_order):
            raise WebrtcTurnAdmissionError("turn_quota_config_invalid", 503)


class TurnQuotaReservationPort(Protocol):
    def reserve(
        self,
        *,
        reservation_id: str,
        demands: Mapping[str, TurnQuotaVector],
        limits: Mapping[str, TurnQuotaVector],
        expires_at: float,
        now: float,
    ) -> bool: ...

    def release(self, reservation_id: str) -> bool: ...


class InMemoryTurnQuotaReservationPort:
    """Atomic bounded reservation reference; production requires a durable shared port."""

    def __init__(self, *, max_reservations: int = 10_000) -> None:
        self._max = max_reservations
        self._reservations: dict[str, tuple[Mapping[str, TurnQuotaVector], float]] = {}
        self._baseline: dict[str, TurnQuotaVector] = {}
        self._lock = RLock()

    def set_usage(self, scope_key: str, usage: TurnQuotaVector) -> None:
        with self._lock:
            self._baseline[scope_key] = usage

    def reserve(
        self,
        *,
        reservation_id: str,
        demands: Mapping[str, TurnQuotaVector],
        limits: Mapping[str, TurnQuotaVector],
        expires_at: float,
        now: float,
    ) -> bool:
        with self._lock:
            self._reservations = {key: value for key, value in self._reservations.items() if value[1] > now}
            existing = self._reservations.get(reservation_id)
            if existing is not None:
                return dict(existing[0]) == dict(demands)
            if len(self._reservations) >= self._max:
                return False
            totals = dict(self._baseline)
            for values, _expiry in self._reservations.values():
                for scope, vector in values.items():
                    totals[scope] = totals.get(scope, TurnQuotaVector()).plus(vector)
            for scope, demand in demands.items():
                if totals.get(scope, TurnQuotaVector()).plus(demand).exceeds(limits[scope]):
                    return False
            self._reservations[reservation_id] = dict(demands), expires_at
            return True

    def release(self, reservation_id: str) -> bool:
        with self._lock:
            return self._reservations.pop(reservation_id, None) is not None


@dataclass(frozen=True, slots=True)
class TurnAdmissionRequest:
    reservation_id: str
    tenant_ref: str
    room_ref: str
    receiver_ref: str
    region: str
    pool_id: str
    requested_layer: str
    layer_projections: Mapping[str, TurnQuotaVector]
    publisher_to_sfu_ingress_bps: int
    sfu_to_turn_egress_bps: int
    accounting_available: bool


@dataclass(frozen=True, slots=True)
class TurnAdmissionResult:
    decision: str
    reason_code: str
    allowed_layer: str | None
    reservation_ref: str | None
    publisher_to_sfu_ingress_bps: int
    sfu_to_turn_egress_bps: int


class WebrtcTurnAdmissionPolicy:
    def __init__(
        self,
        *,
        config: TurnQuotaPolicyConfig,
        reservations: TurnQuotaReservationPort,
        scope_secret: bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(scope_secret) < 32:
            raise WebrtcTurnAdmissionError("turn_quota_scope_secret_invalid", 503)
        self._config = config
        self._reservations = reservations
        self._secret = bytes(scope_secret)
        self._clock = clock

    def reserve(self, request: TurnAdmissionRequest) -> TurnAdmissionResult:
        self._validate(request)
        if not request.accounting_available:
            return self._deny(request, "turn_accounting_unavailable")
        layers = [request.requested_layer]
        for layer in self._config.lower_cap_order:
            if layer not in layers and self._rank(layer) < self._rank(request.requested_layer):
                layers.append(layer)
        scopes = self._scopes(request)
        now = self._clock()
        for layer in layers:
            projection = request.layer_projections.get(layer)
            if projection is None:
                continue
            priced = self._priced(projection)
            demands = {scope_key: priced for scope_key in scopes.values()}
            limits = {scopes[name]: self._config.limits[name] for name in scopes}
            reservation_id = self._digest("reservation", request.reservation_id + "\0" + layer)
            if self._reservations.reserve(
                reservation_id=reservation_id,
                demands=demands,
                limits=limits,
                expires_at=now + self._config.reservation_ttl_seconds,
                now=now,
            ):
                lower = layer != request.requested_layer
                return TurnAdmissionResult(
                    "lower_cap" if lower else "allow",
                    "turn_relay_lower_cap_reserved" if lower else "turn_relay_capacity_reserved",
                    layer,
                    "trr1." + reservation_id,
                    request.publisher_to_sfu_ingress_bps,
                    request.sfu_to_turn_egress_bps,
                )
        return self._deny(request, "turn_relay_capacity_exhausted")

    def rollback(self, reservation_ref: str) -> bool:
        if not isinstance(reservation_ref, str) or not reservation_ref.startswith("trr1."):
            raise WebrtcTurnAdmissionError("turn_reservation_ref_invalid")
        # Public refs are deliberately one-way; callers retain the opaque internal reservation token.
        return self._reservations.release(reservation_ref.removeprefix("trr1."))

    def _validate(self, request: TurnAdmissionRequest) -> None:
        for value in (
            request.reservation_id,
            request.tenant_ref,
            request.room_ref,
            request.receiver_ref,
            request.region,
            request.pool_id,
        ):
            if not isinstance(value, str) or not value or len(value.encode()) > 128:
                raise WebrtcTurnAdmissionError("turn_admission_scope_invalid")
        if request.requested_layer not in {"high", "medium", "low", "none"}:
            raise WebrtcTurnAdmissionError("turn_admission_layer_invalid")
        for value in (request.publisher_to_sfu_ingress_bps, request.sfu_to_turn_egress_bps):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WebrtcTurnAdmissionError("turn_admission_projection_invalid")
        if request.sfu_to_turn_egress_bps == 0 or not isinstance(request.accounting_available, bool):
            raise WebrtcTurnAdmissionError("turn_admission_projection_invalid")

    def _priced(self, vector: TurnQuotaVector) -> TurnQuotaVector:
        gib = 1024**3
        ingress_cost = vector.bytes_per_window / gib * self._config.cost_per_gib_ingress
        egress_cost = vector.bytes_per_window / gib * self._config.cost_per_gib_egress
        return TurnQuotaVector(
            vector.allocations,
            vector.ports,
            vector.receivers,
            vector.bps,
            vector.bytes_per_window,
            max(vector.cost_units, ingress_cost + egress_cost),
        )

    def _scopes(self, request: TurnAdmissionRequest) -> dict[str, str]:
        return {
            "receiver": self._digest("receiver", request.tenant_ref + "\0" + request.room_ref + "\0" + request.receiver_ref),
            "room": self._digest("room", request.tenant_ref + "\0" + request.room_ref),
            "tenant": self._digest("tenant", request.tenant_ref),
            "region": self._digest("region", request.region),
            "pool": self._digest("pool", request.region + "\0" + request.pool_id),
        }

    def _digest(self, domain: str, value: str) -> str:
        return hmac.new(self._secret, f"turn-quota-{domain}-v1\0{value}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _rank(layer: str) -> int:
        return {"none": 0, "low": 1, "medium": 2, "high": 3}[layer]

    @staticmethod
    def _deny(request: TurnAdmissionRequest, reason: str) -> TurnAdmissionResult:
        return TurnAdmissionResult(
            "relay_capacity_exhausted",
            reason,
            None,
            None,
            request.publisher_to_sfu_ingress_bps,
            request.sfu_to_turn_egress_bps,
        )


__all__ = [
    "InMemoryTurnQuotaReservationPort",
    "TurnAdmissionRequest",
    "TurnAdmissionResult",
    "TurnQuotaPolicyConfig",
    "TurnQuotaReservationPort",
    "TurnQuotaVector",
    "WebrtcTurnAdmissionError",
    "WebrtcTurnAdmissionPolicy",
]
