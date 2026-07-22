"""Idempotent, content-free accounting for signed SFU egress windows."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Callable, Literal, Mapping, Protocol


AccountingValueKind = Literal["actual", "estimated", "missing"]
_LAYERS = frozenset({"spatial_0", "spatial_1", "spatial_2", "spatial_3"})


@dataclass(frozen=True, slots=True)
class SfuEgressAccountingWindow:
    tenant_id: str
    room_id: str
    publication_id: str
    node_id: str
    boot_id: str
    sequence: int
    window_started_at_ms: int
    window_ended_at_ms: int
    route_epoch: int
    topology_epoch: int
    fencing_token: int
    actual_egress_bytes: int | None
    estimated_egress_bytes: int | None
    routed_egress_bytes: int | None
    delivered_layer_bytes: Mapping[str, int]
    shared_processing_bytes_saved: int
    receiver_count: int
    signature: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SfuEgressAccountingRecord:
    window_id: str
    window_digest: str
    value_kind: AccountingValueKind
    accounted_egress_bytes: int | None
    network_egress_bytes: int | None
    shared_processing_bytes_saved: int
    reconciliation_reason_codes: tuple[str, ...]
    window: SfuEgressAccountingWindow


@dataclass(frozen=True, slots=True)
class SfuEgressAccountingResult:
    status: Literal["accepted", "duplicate", "rejected"]
    reason_code: str
    record: SfuEgressAccountingRecord | None
    retryable: bool = False


class SfuEgressAccountingSignaturePort(Protocol):
    def verify(self, unsigned: Mapping[str, object], signature: Mapping[str, object]) -> bool: ...


class SfuEgressAccountingAlertPort(Protocol):
    def emit(self, *, tenant_id: str, room_id: str, node_id: str,
             reason_code: str, window_id: str) -> None: ...


class SfuEgressAccountingRepositoryPort(Protocol):
    def get(self, key: tuple[str, str, int, int, int]) -> SfuEgressAccountingRecord | None: ...
    def latest(self, node_id: str) -> SfuEgressAccountingRecord | None: ...
    def save(self, key: tuple[str, str, int, int, int], record: SfuEgressAccountingRecord) -> bool: ...


class InMemorySfuEgressAccountingRepository:
    """Bounded adapter; production may provide a durable fleet/accounting port."""

    def __init__(self, *, windows_max: int = 4096, nodes_max: int = 1024,
                 windows_per_node_max: int = 256) -> None:
        if not 1 <= windows_max <= 65_536 or not 1 <= nodes_max <= 4096 \
                or not 1 <= windows_per_node_max <= 1024:
            raise ValueError("sfu_accounting_repository_bounds_invalid")
        self._windows_max = windows_max
        self._nodes_max = nodes_max
        self._per_node = windows_per_node_max
        self._records: OrderedDict[tuple[str, str, int, int, int], SfuEgressAccountingRecord] = OrderedDict()
        self._latest: OrderedDict[str, SfuEgressAccountingRecord] = OrderedDict()

    def get(self, key: tuple[str, str, int, int, int]) -> SfuEgressAccountingRecord | None:
        return self._records.get(key)

    def latest(self, node_id: str) -> SfuEgressAccountingRecord | None:
        return self._latest.get(node_id)

    def save(self, key: tuple[str, str, int, int, int], record: SfuEgressAccountingRecord) -> bool:
        if record.window.node_id not in self._latest and len(self._latest) >= self._nodes_max:
            return False
        self._records[key] = record
        self._records.move_to_end(key)
        self._latest[record.window.node_id] = record
        self._latest.move_to_end(record.window.node_id)
        node_keys = [candidate for candidate, value in self._records.items()
                     if value.window.node_id == record.window.node_id]
        for stale in node_keys[:-self._per_node]:
            self._records.pop(stale, None)
        while len(self._records) > self._windows_max:
            self._records.popitem(last=False)
        return True


class SfuEgressAccountingService:
    def __init__(self, repository: SfuEgressAccountingRepositoryPort,
                 signatures: SfuEgressAccountingSignaturePort, *,
                 alerts: SfuEgressAccountingAlertPort | None = None,
                 accounting_tolerance_bytes: int = 4096,
                 accounting_tolerance_percent: int = 5,
                 clock: Callable[[], float] = time.time) -> None:
        if not 0 <= accounting_tolerance_bytes <= 1_048_576 \
                or not 0 <= accounting_tolerance_percent <= 100:
            raise ValueError("sfu_accounting_tolerance_invalid")
        self._repository = repository
        self._signatures = signatures
        self._alerts = alerts
        self._tolerance_bytes = accounting_tolerance_bytes
        self._tolerance_basis_points = accounting_tolerance_percent * 100
        self._clock = clock

    def ingest(self, window: SfuEgressAccountingWindow) -> SfuEgressAccountingResult:
        failure = self._validate(window)
        if failure is not None:
            return SfuEgressAccountingResult("rejected", failure, None)
        unsigned = asdict(window)
        signature = unsigned.pop("signature")
        if not self._signatures.verify(unsigned, signature):
            return SfuEgressAccountingResult("rejected", "sfu_accounting_signature_invalid", None)
        digest = _digest(unsigned)
        key = (window.node_id, window.boot_id, window.sequence,
               window.window_started_at_ms, window.window_ended_at_ms)
        existing = self._repository.get(key)
        if existing is not None:
            if existing.window_digest != digest:
                return SfuEgressAccountingResult("rejected", "sfu_accounting_window_conflict", None)
            return SfuEgressAccountingResult("duplicate", "sfu_accounting_duplicate", existing)
        previous = self._repository.latest(window.node_id)
        reasons: list[str] = []
        if previous is not None:
            predecessor = previous.window
            if predecessor.boot_id == window.boot_id:
                if window.sequence <= predecessor.sequence:
                    return SfuEgressAccountingResult("rejected", "sfu_accounting_counter_regression", None)
                if window.sequence > predecessor.sequence + 1:
                    reasons.append("sfu_accounting_counter_gap")
                if window.window_started_at_ms < predecessor.window_ended_at_ms:
                    return SfuEgressAccountingResult("rejected", "sfu_accounting_window_overlap", None)
            else:
                if window.sequence != 1:
                    return SfuEgressAccountingResult("rejected", "sfu_accounting_restart_sequence_invalid", None)
                reasons.append("sfu_accounting_node_restart")
        now_ms = int(self._clock() * 1000)
        if window.window_ended_at_ms < now_ms - 30_000:
            reasons.append("sfu_accounting_late_window")
        kind: AccountingValueKind
        accounted: int | None
        if window.actual_egress_bytes is not None:
            kind, accounted = "actual", window.actual_egress_bytes
        elif window.estimated_egress_bytes is not None:
            kind, accounted = "estimated", window.estimated_egress_bytes
            reasons.append("sfu_accounting_estimated_window")
        else:
            kind, accounted = "missing", None
            reasons.append("sfu_accounting_missing_window")
        layer_total = sum(window.delivered_layer_bytes.values())
        if accounted is not None and layer_total and self._outside_tolerance(accounted, layer_total):
            reasons.append("sfu_accounting_layer_delivery_mismatch")
        if window.actual_egress_bytes is not None and window.routed_egress_bytes is not None \
                and self._outside_tolerance(window.actual_egress_bytes, window.routed_egress_bytes):
            reasons.append("sfu_accounting_route_egress_divergence")
        window_id = "sfu-egress-" + digest[:32]
        record = SfuEgressAccountingRecord(
            window_id, digest, kind, accounted, window.actual_egress_bytes,
            window.shared_processing_bytes_saved, tuple(sorted(set(reasons))), window,
        )
        if not self._repository.save(key, record):
            return SfuEgressAccountingResult("rejected", "sfu_accounting_cardinality_exceeded", None, True)
        for reason in record.reconciliation_reason_codes:
            if self._alerts is not None:
                self._alerts.emit(
                    tenant_id=window.tenant_id, room_id=window.room_id,
                    node_id=window.node_id, reason_code=reason, window_id=window_id,
                )
        primary = record.reconciliation_reason_codes[0] if record.reconciliation_reason_codes else "sfu_accounting_accepted"
        return SfuEgressAccountingResult("accepted", primary, record)

    def _outside_tolerance(self, actual: int, comparison: int) -> bool:
        delta = abs(actual - comparison)
        basis_points = delta * 10_000 // max(1, actual)
        return delta > self._tolerance_bytes and basis_points > self._tolerance_basis_points

    def _validate(self, window: SfuEgressAccountingWindow) -> str | None:
        for value in (window.tenant_id, window.room_id, window.publication_id,
                      window.node_id, window.boot_id):
            if not _handle(value):
                return "sfu_accounting_scope_invalid"
        integers = (
            window.sequence, window.window_started_at_ms, window.window_ended_at_ms,
            window.route_epoch, window.topology_epoch, window.fencing_token,
            window.shared_processing_bytes_saved, window.receiver_count,
        )
        if any(type(value) is not int or value < 0 for value in integers) \
                or window.sequence < 1 or window.route_epoch < 1 \
                or window.topology_epoch < 1 or window.fencing_token < 1:
            return "sfu_accounting_bounds_invalid"
        duration = window.window_ended_at_ms - window.window_started_at_ms
        now_ms = int(self._clock() * 1000)
        if not 100 <= duration <= 60_000 or window.window_ended_at_ms > now_ms + 5000:
            return "sfu_accounting_window_invalid"
        byte_values = (window.actual_egress_bytes, window.estimated_egress_bytes, window.routed_egress_bytes)
        if any(value is not None and (type(value) is not int or not 0 <= value <= 9_007_199_254_740_991)
               for value in byte_values):
            return "sfu_accounting_bytes_invalid"
        if len(window.delivered_layer_bytes) > 4 or set(window.delivered_layer_bytes) - _LAYERS \
                or any(type(value) is not int or value < 0 for value in window.delivered_layer_bytes.values()):
            return "sfu_accounting_layer_breakdown_invalid"
        if not isinstance(window.signature, Mapping) or len(window.signature) > 3:
            return "sfu_accounting_signature_invalid"
        return None


def _handle(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 128 \
        and not any(ord(char) <= 0x20 or ord(char) == 0x7f for char in value)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


__all__ = [
    "InMemorySfuEgressAccountingRepository", "SfuEgressAccountingAlertPort",
    "SfuEgressAccountingRecord", "SfuEgressAccountingRepositoryPort",
    "SfuEgressAccountingResult", "SfuEgressAccountingService",
    "SfuEgressAccountingSignaturePort", "SfuEgressAccountingWindow",
]
