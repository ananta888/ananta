from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaSlotDecision:
    status: str  # active|queued|rejected
    lease_id: str | None
    endpoint: str
    model: str
    reason_code: str
    queue_position: int | None = None


class OllamaParallelRuntimeService:
    """In-memory slot accounting per endpoint+model for hub-side scheduling decisions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], dict[str, float]] = {}
        self._queued: dict[tuple[str, str], list[str]] = {}
        self._rejected: dict[tuple[str, str], int] = {}
        self._completed: dict[tuple[str, str], int] = {}

    @staticmethod
    def _key(endpoint: str, model: str) -> tuple[str, str]:
        return ((endpoint or "").strip().lower(), (model or "").strip())

    def acquire_slot(
        self,
        *,
        endpoint: str,
        model: str,
        max_parallel_requests: int,
        queue_limit: int,
        lease_seconds: int,
        backpressure: str = "queue_then_reject",
    ) -> OllamaSlotDecision:
        key = self._key(endpoint, model)
        with self._lock:
            active = self._active.setdefault(key, {})
            queue = self._queued.setdefault(key, [])
            if len(active) < max_parallel_requests:
                lease_id = str(uuid.uuid4())
                active[lease_id] = time.time() + max(1, lease_seconds)
                return OllamaSlotDecision(
                    status="active",
                    lease_id=lease_id,
                    endpoint=key[0],
                    model=key[1],
                    reason_code="ollama_slot_acquired",
                )

            if backpressure == "reject_immediately":
                self._rejected[key] = self._rejected.get(key, 0) + 1
                return OllamaSlotDecision(
                    status="rejected",
                    lease_id=None,
                    endpoint=key[0],
                    model=key[1],
                    reason_code="ollama_model_parallel_capacity_exhausted",
                )

            if len(queue) >= queue_limit:
                self._rejected[key] = self._rejected.get(key, 0) + 1
                return OllamaSlotDecision(
                    status="rejected",
                    lease_id=None,
                    endpoint=key[0],
                    model=key[1],
                    reason_code="ollama_queue_full",
                )

            lease_id = str(uuid.uuid4())
            queue.append(lease_id)
            return OllamaSlotDecision(
                status="queued",
                lease_id=lease_id,
                endpoint=key[0],
                model=key[1],
                reason_code="ollama_queued",
                queue_position=len(queue),
            )

    def release_slot(self, *, endpoint: str, model: str, lease_id: str) -> None:
        key = self._key(endpoint, model)
        with self._lock:
            active = self._active.setdefault(key, {})
            queue = self._queued.setdefault(key, [])
            if lease_id in active:
                active.pop(lease_id, None)
                self._completed[key] = self._completed.get(key, 0) + 1
            elif lease_id in queue:
                queue.remove(lease_id)

    def cleanup_stale_leases(self, now_ts: float | None = None) -> int:
        now_value = float(now_ts or time.time())
        released = 0
        with self._lock:
            for key, active in self._active.items():
                stale = [lease_id for lease_id, deadline in active.items() if deadline < now_value]
                for lease_id in stale:
                    active.pop(lease_id, None)
                    self._completed[key] = self._completed.get(key, 0) + 1
                    released += 1
        return released

    def get_status(self) -> dict[str, dict[str, int]]:
        with self._lock:
            payload: dict[str, dict[str, int]] = {}
            keys = set(self._active.keys()) | set(self._queued.keys()) | set(self._rejected.keys()) | set(self._completed.keys())
            for key in keys:
                bucket_key = f"{key[0]}::{key[1]}"
                payload[bucket_key] = {
                    "active_count": len(self._active.get(key, {})),
                    "queued_count": len(self._queued.get(key, [])),
                    "rejected_count": int(self._rejected.get(key, 0)),
                    "completed_count": int(self._completed.get(key, 0)),
                }
            return payload


_ollama_parallel_runtime_service = OllamaParallelRuntimeService()


def get_ollama_parallel_runtime_service() -> OllamaParallelRuntimeService:
    return _ollama_parallel_runtime_service
