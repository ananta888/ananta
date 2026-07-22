"""Hub-owned graceful drain and rolling-upgrade state machine."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Protocol


class SfuNodeDrainError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SfuNodeDrainState(str, Enum):
    REQUESTED = "requested"
    ADMISSION_STOPPED = "admission_stopped"
    DRAINING = "draining"
    DRAINED = "drained"
    FORCED = "forced"
    CANCELLED = "cancelled"


class SfuExistingRoomPolicy(str, Enum):
    HOLD = "hold"
    CONTROLLED_REJOIN = "controlled_rejoin"
    PARENT_FALLBACK = "parent_fallback"


@dataclass(frozen=True, slots=True)
class SfuNodeVersionSet:
    contract_version: str
    adapter_name: str
    adapter_version: str
    e2ee_version: str
    route_version: str


@dataclass(frozen=True, slots=True)
class SfuNodeDrainPolicy:
    deadline_seconds: float
    max_parallel: int
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class SfuNodeDrainRecord:
    tenant_id: str
    cluster_id: str
    node_id: str
    state: SfuNodeDrainState
    room_policy: SfuExistingRoomPolicy
    reason_code: str
    requested_at: float
    deadline_at: float
    cooldown_until: float
    active_rooms: int
    version: int
    fencing_token: int


@dataclass(frozen=True, slots=True)
class SfuRoomDrainResult:
    remaining_rooms: int
    control_path_stable: bool
    transcript_path_stable: bool
    stale_access_revoked: bool


class SfuNodeDrainRepositoryPort(Protocol):
    def get(self, tenant_id: str, cluster_id: str, node_id: str) -> SfuNodeDrainRecord | None: ...
    def compare_and_swap(
        self, record: SfuNodeDrainRecord, *, expected_version: int
    ) -> SfuNodeDrainRecord: ...
    def count_in_progress(self, tenant_id: str, cluster_id: str) -> int: ...


class SfuNodeAdmissionDrainPort(Protocol):
    def stop_admission(self, record: SfuNodeDrainRecord, operation_id: str) -> bool: ...
    def resume_admission(self, record: SfuNodeDrainRecord, operation_id: str) -> bool: ...


class SfuExistingRoomDrainPort(Protocol):
    def apply(
        self,
        record: SfuNodeDrainRecord,
        policy: SfuExistingRoomPolicy,
        operation_id: str,
    ) -> SfuRoomDrainResult: ...


@dataclass(frozen=True, slots=True)
class SfuVersionCompatibilityMatrix:
    policy: SfuNodeDrainPolicy
    compatible_sets: tuple[SfuNodeVersionSet, ...]

    def allows(self, versions: SfuNodeVersionSet) -> bool:
        return versions in self.compatible_sets

    @classmethod
    def from_file(cls, path: str | Path) -> "SfuVersionCompatibilityMatrix":
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SfuNodeDrainError("sfu_drain_version_matrix_unavailable") from exc
        if set(document) != {"schema", "policy", "compatible_sets"}:
            raise SfuNodeDrainError("sfu_drain_version_matrix_invalid")
        if document["schema"] != "ananta.sfu-broadcast-version-compatibility.v1":
            raise SfuNodeDrainError("sfu_drain_version_matrix_unknown")
        raw_policy = document["policy"]
        if not isinstance(raw_policy, dict) or set(raw_policy) != {
            "deadline_seconds", "max_parallel", "cooldown_seconds"
        }:
            raise SfuNodeDrainError("sfu_drain_policy_invalid")
        policy = SfuNodeDrainPolicy(
            deadline_seconds=float(raw_policy["deadline_seconds"]),
            max_parallel=int(raw_policy["max_parallel"]),
            cooldown_seconds=float(raw_policy["cooldown_seconds"]),
        )
        if policy.deadline_seconds <= 0 or policy.max_parallel < 1 or policy.cooldown_seconds < 0:
            raise SfuNodeDrainError("sfu_drain_policy_invalid")
        rows = document["compatible_sets"]
        if not isinstance(rows, list) or not rows:
            raise SfuNodeDrainError("sfu_drain_version_matrix_empty")
        required = {
            "contract_version", "adapter_name", "adapter_version",
            "e2ee_version", "route_version",
        }
        parsed = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != required:
                raise SfuNodeDrainError("sfu_drain_version_matrix_invalid")
            if not all(isinstance(row[name], str) and row[name] for name in required):
                raise SfuNodeDrainError("sfu_drain_version_matrix_invalid")
            parsed.append(SfuNodeVersionSet(**row))
        if len(set(parsed)) != len(parsed):
            raise SfuNodeDrainError("sfu_drain_version_matrix_duplicate")
        return cls(policy=policy, compatible_sets=tuple(parsed))


class InMemorySfuNodeDrainRepository:
    """Thread-safe test adapter; production can substitute durable CAS storage."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], SfuNodeDrainRecord] = {}
        self._lock = threading.Lock()

    def get(self, tenant_id: str, cluster_id: str, node_id: str) -> SfuNodeDrainRecord | None:
        with self._lock:
            return self._records.get((tenant_id, cluster_id, node_id))

    def compare_and_swap(
        self, record: SfuNodeDrainRecord, *, expected_version: int
    ) -> SfuNodeDrainRecord:
        key = (record.tenant_id, record.cluster_id, record.node_id)
        with self._lock:
            current = self._records.get(key)
            actual = 0 if current is None else current.version
            if actual != expected_version or record.version != expected_version + 1:
                raise SfuNodeDrainError("sfu_drain_version_conflict")
            self._records[key] = record
            return record

    def count_in_progress(self, tenant_id: str, cluster_id: str) -> int:
        active = {
            SfuNodeDrainState.REQUESTED,
            SfuNodeDrainState.ADMISSION_STOPPED,
            SfuNodeDrainState.DRAINING,
        }
        with self._lock:
            return sum(
                record.state in active
                for record in self._records.values()
                if record.tenant_id == tenant_id and record.cluster_id == cluster_id
            )


class SfuNodeDrainService:
    """Coordinates admission and room effects in a strictly ordered Hub flow."""

    def __init__(
        self,
        repository: SfuNodeDrainRepositoryPort,
        admission: SfuNodeAdmissionDrainPort,
        rooms: SfuExistingRoomDrainPort,
        compatibility: SfuVersionCompatibilityMatrix,
        *,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self._admission = admission
        self._rooms = rooms
        self._compatibility = compatibility
        self._clock = clock

    def request(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        versions: SfuNodeVersionSet,
        room_policy: SfuExistingRoomPolicy,
        reason_code: str,
        active_rooms: int,
    ) -> SfuNodeDrainRecord:
        now = float(self._clock())
        if not self._compatibility.allows(versions):
            raise SfuNodeDrainError("sfu_drain_version_incompatible")
        current = self._repository.get(tenant_id, cluster_id, node_id)
        if current is not None and current.state not in {
            SfuNodeDrainState.DRAINED,
            SfuNodeDrainState.FORCED,
            SfuNodeDrainState.CANCELLED,
        }:
            return current
        if current is not None and now < current.cooldown_until:
            raise SfuNodeDrainError("sfu_drain_cooldown_active")
        if self._repository.count_in_progress(tenant_id, cluster_id) >= self._compatibility.policy.max_parallel:
            raise SfuNodeDrainError("sfu_drain_parallel_limit")
        if active_rooms < 0 or not reason_code:
            raise SfuNodeDrainError("sfu_drain_request_invalid")
        expected = 0 if current is None else current.version
        record = SfuNodeDrainRecord(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            state=SfuNodeDrainState.REQUESTED,
            room_policy=room_policy,
            reason_code=reason_code,
            requested_at=now,
            deadline_at=now + self._compatibility.policy.deadline_seconds,
            cooldown_until=0.0,
            active_rooms=active_rooms,
            version=expected + 1,
            fencing_token=(0 if current is None else current.fencing_token) + 1,
        )
        return self._repository.compare_and_swap(record, expected_version=expected)

    def advance(self, record: SfuNodeDrainRecord) -> SfuNodeDrainRecord:
        now = float(self._clock())
        current = self._repository.get(record.tenant_id, record.cluster_id, record.node_id)
        if current is None or current.version != record.version:
            raise SfuNodeDrainError("sfu_drain_version_conflict")
        operation_id = f"drain:{current.node_id}:{current.version + 1}"
        if current.state is SfuNodeDrainState.REQUESTED:
            if not self._admission.stop_admission(current, operation_id):
                raise SfuNodeDrainError("sfu_drain_admission_stop_failed")
            return self._transition(current, SfuNodeDrainState.ADMISSION_STOPPED, now)
        if current.state in {SfuNodeDrainState.ADMISSION_STOPPED, SfuNodeDrainState.DRAINING}:
            if now >= current.deadline_at:
                forced = self._rooms.apply(
                    current, SfuExistingRoomPolicy.PARENT_FALLBACK, operation_id
                )
                if not self._safe_room_result(forced):
                    raise SfuNodeDrainError("sfu_drain_forced_fallback_failed")
                return self._transition(
                    current,
                    SfuNodeDrainState.FORCED,
                    now,
                    active_rooms=forced.remaining_rooms,
                    reason_code="sfu_drain_deadline_forced",
                )
            result = self._rooms.apply(current, current.room_policy, operation_id)
            if not self._safe_room_result(result):
                raise SfuNodeDrainError("sfu_drain_room_path_unstable")
            target = (
                SfuNodeDrainState.DRAINED
                if result.remaining_rooms == 0
                else SfuNodeDrainState.DRAINING
            )
            return self._transition(
                current, target, now, active_rooms=result.remaining_rooms
            )
        return current

    def cancel(self, record: SfuNodeDrainRecord, *, reason_code: str) -> SfuNodeDrainRecord:
        current = self._repository.get(record.tenant_id, record.cluster_id, record.node_id)
        if current is None or current.version != record.version:
            raise SfuNodeDrainError("sfu_drain_version_conflict")
        if current.state not in {
            SfuNodeDrainState.REQUESTED,
            SfuNodeDrainState.ADMISSION_STOPPED,
            SfuNodeDrainState.DRAINING,
        }:
            raise SfuNodeDrainError("sfu_drain_cancel_forbidden")
        now = float(self._clock())
        operation_id = f"drain-cancel:{current.node_id}:{current.version + 1}"
        if not self._admission.resume_admission(current, operation_id):
            raise SfuNodeDrainError("sfu_drain_admission_resume_failed")
        return self._transition(
            current,
            SfuNodeDrainState.CANCELLED,
            now,
            reason_code=reason_code,
            cooldown_until=now + self._compatibility.policy.cooldown_seconds,
        )

    def _transition(
        self,
        current: SfuNodeDrainRecord,
        state: SfuNodeDrainState,
        now: float,
        *,
        active_rooms: int | None = None,
        reason_code: str | None = None,
        cooldown_until: float | None = None,
    ) -> SfuNodeDrainRecord:
        updated = replace(
            current,
            state=state,
            active_rooms=current.active_rooms if active_rooms is None else active_rooms,
            reason_code=current.reason_code if reason_code is None else reason_code,
            cooldown_until=(
                current.cooldown_until if cooldown_until is None else cooldown_until
            ),
            version=current.version + 1,
            fencing_token=current.fencing_token + 1,
        )
        return self._repository.compare_and_swap(updated, expected_version=current.version)

    @staticmethod
    def _safe_room_result(result: SfuRoomDrainResult) -> bool:
        return (
            result.remaining_rooms >= 0
            and result.control_path_stable
            and result.transcript_path_stable
            and result.stale_access_revoked
        )


__all__ = [
    "InMemorySfuNodeDrainRepository",
    "SfuExistingRoomPolicy",
    "SfuNodeDrainError",
    "SfuNodeDrainPolicy",
    "SfuNodeDrainRecord",
    "SfuNodeDrainService",
    "SfuNodeDrainState",
    "SfuNodeVersionSet",
    "SfuRoomDrainResult",
    "SfuVersionCompatibilityMatrix",
]
