"""Lazy, exactly-once model lifecycle for the isolated inference worker."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterator, Protocol

from agent.services.restricted_inference_model_manifest import VerifiedModelSnapshot
from worker.runtime.restricted_inference_resources import (
    ResourceLeaseManager,
    RestrictedInferenceResourceError,
)


class ModelLifecycleState(str, Enum):
    DECLARED = "declared"
    VERIFYING = "verifying"
    LOADING = "loading"
    READY = "ready"
    DEGRADED = "degraded"
    LOADED = "loaded"
    IDLE = "idle"
    UNLOADING = "unloading"
    EVICTING = "evicting"
    EVICTED = "evicted"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


_TRANSITIONS: dict[ModelLifecycleState, frozenset[ModelLifecycleState]] = {
    ModelLifecycleState.DECLARED: frozenset({ModelLifecycleState.VERIFYING}),
    ModelLifecycleState.VERIFYING: frozenset({ModelLifecycleState.LOADING, ModelLifecycleState.FAILED}),
    ModelLifecycleState.LOADING: frozenset(
        {
            ModelLifecycleState.READY,
            ModelLifecycleState.DEGRADED,
            ModelLifecycleState.FAILED,
            ModelLifecycleState.UNAVAILABLE,
        }
    ),
    ModelLifecycleState.READY: frozenset(
        {ModelLifecycleState.LOADED, ModelLifecycleState.IDLE, ModelLifecycleState.FAILED}
    ),
    ModelLifecycleState.DEGRADED: frozenset(
        {ModelLifecycleState.LOADED, ModelLifecycleState.IDLE, ModelLifecycleState.FAILED}
    ),
    ModelLifecycleState.LOADED: frozenset({ModelLifecycleState.IDLE, ModelLifecycleState.FAILED}),
    ModelLifecycleState.IDLE: frozenset(
        {
            ModelLifecycleState.LOADED,
            ModelLifecycleState.UNLOADING,
            ModelLifecycleState.EVICTING,
            ModelLifecycleState.FAILED,
        }
    ),
    ModelLifecycleState.UNLOADING: frozenset({ModelLifecycleState.EVICTED, ModelLifecycleState.FAILED}),
    ModelLifecycleState.EVICTING: frozenset({ModelLifecycleState.EVICTED, ModelLifecycleState.FAILED}),
    ModelLifecycleState.EVICTED: frozenset({ModelLifecycleState.VERIFYING}),
    ModelLifecycleState.FAILED: frozenset({ModelLifecycleState.VERIFYING, ModelLifecycleState.UNAVAILABLE}),
    ModelLifecycleState.UNAVAILABLE: frozenset({ModelLifecycleState.VERIFYING}),
}


class AdapterFactory(Protocol):
    def __call__(self, snapshot: VerifiedModelSnapshot, *, device: str) -> Any: ...


class ModelLifecycleError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass
class _ModelRecord:
    snapshot: VerifiedModelSnapshot
    state: ModelLifecycleState = ModelLifecycleState.DECLARED
    adapter: Any = None
    active_leases: int = 0
    last_used_ns: int = 0
    failed_at_ns: int = 0
    failure_code: str = ""
    loaded_device: str = ""


@dataclass(frozen=True)
class ModelStatus:
    manifest_id: str
    manifest_digest: str
    model_id: str
    engine: str
    state: ModelLifecycleState
    active_leases: int
    loaded_device: str
    failure_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest,
            "model_id": self.model_id,
            "engine": self.engine,
            "state": self.state.value,
            "active_leases": self.active_leases,
            "loaded_device": self.loaded_device,
            "failure_code": self.failure_code,
        }


class LazyModelRegistry:
    """Load each verified digest once and keep active models non-evictable."""

    def __init__(
        self,
        *,
        adapter_factory: AdapterFactory,
        resources: ResourceLeaseManager,
        failure_retry_seconds: float = 5.0,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        self._factory = adapter_factory
        self._resources = resources
        self._retry_ns = max(0, int(failure_retry_seconds * 1_000_000_000))
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._condition = threading.Condition(threading.RLock())
        self._records: dict[str, _ModelRecord] = {}

    @contextmanager
    def lease(
        self,
        snapshot: VerifiedModelSnapshot,
        *,
        deadline_epoch_ms: int,
        epoch_ms: Callable[[], int] | None = None,
        allow_cpu_fallback: bool = False,
    ) -> Iterator[Any]:
        adapter = self._acquire(
            snapshot,
            deadline_epoch_ms=deadline_epoch_ms,
            epoch_ms=epoch_ms,
            allow_cpu_fallback=allow_cpu_fallback,
        )
        try:
            yield adapter
        except BaseException as exc:
            if _is_out_of_memory(exc):
                self.mark_failed(snapshot.manifest_digest, "out_of_memory")
            raise
        finally:
            self._release(snapshot.manifest_digest)

    def preload(
        self,
        snapshot: VerifiedModelSnapshot,
        *,
        deadline_epoch_ms: int,
        allow_cpu_fallback: bool = False,
    ) -> ModelStatus:
        """Load one admitted snapshot and leave it idle for later leases."""

        with self.lease(
            snapshot,
            deadline_epoch_ms=deadline_epoch_ms,
            allow_cpu_fallback=allow_cpu_fallback,
        ):
            pass
        return next(status for status in self.statuses() if status.manifest_digest == snapshot.manifest_digest)

    def _acquire(
        self,
        snapshot: VerifiedModelSnapshot,
        *,
        deadline_epoch_ms: int,
        epoch_ms: Callable[[], int] | None,
        allow_cpu_fallback: bool,
    ) -> Any:
        clock = epoch_ms or (lambda: time.time_ns() // 1_000_000)
        digest = snapshot.manifest_digest
        should_load = False
        with self._condition:
            record = self._records.get(digest)
            if record is None:
                record = _ModelRecord(snapshot=snapshot)
                self._records[digest] = record
            while record.state in {ModelLifecycleState.VERIFYING, ModelLifecycleState.LOADING}:
                remaining_ms = deadline_epoch_ms - clock()
                if remaining_ms <= 0:
                    raise ModelLifecycleError("timeout", "model load deadline expired")
                self._condition.wait(timeout=min(remaining_ms / 1000.0, 0.25))
            if record.state in {ModelLifecycleState.FAILED, ModelLifecycleState.UNAVAILABLE}:
                elapsed = self._monotonic_ns() - record.failed_at_ns
                if elapsed < self._retry_ns:
                    raise ModelLifecycleError(record.failure_code or "model_failed", "model is in retry cooldown")
            if record.state in {
                ModelLifecycleState.DECLARED,
                ModelLifecycleState.EVICTED,
                ModelLifecycleState.FAILED,
                ModelLifecycleState.UNAVAILABLE,
            }:
                self._transition(record, ModelLifecycleState.VERIFYING)
                self._transition(record, ModelLifecycleState.LOADING)
                should_load = True
            elif record.state is ModelLifecycleState.IDLE:
                self._transition(record, ModelLifecycleState.LOADED)
            if not should_load:
                record.active_leases += 1
                record.last_used_ns = self._monotonic_ns()
                return record.adapter

        requested_device = snapshot.manifest.device if snapshot.manifest is not None else ""
        try:
            adapter, loaded_device = self._load(snapshot, allow_cpu_fallback=allow_cpu_fallback)
        except BaseException as exc:
            reason = _load_error_code(exc)
            self._resources.release_model(digest)
            with self._condition:
                record = self._records[digest]
                target = (
                    ModelLifecycleState.UNAVAILABLE
                    if reason in {"adapter_unavailable", "model_factory_unavailable"}
                    else ModelLifecycleState.FAILED
                )
                self._transition(record, target)
                record.failure_code = reason
                record.failed_at_ns = self._monotonic_ns()
                record.adapter = None
                self._condition.notify_all()
            if isinstance(exc, (ModelLifecycleError, RestrictedInferenceResourceError)):
                raise
            raise ModelLifecycleError(reason, "model could not be loaded") from exc

        with self._condition:
            record = self._records[digest]
            record.adapter = adapter
            record.loaded_device = loaded_device
            record.failure_code = ""
            record.active_leases = 1
            record.last_used_ns = self._monotonic_ns()
            self._transition(
                record,
                ModelLifecycleState.DEGRADED if loaded_device != requested_device else ModelLifecycleState.READY,
            )
            self._transition(record, ModelLifecycleState.LOADED)
            self._condition.notify_all()
            return adapter

    def _load(self, snapshot: VerifiedModelSnapshot, *, allow_cpu_fallback: bool) -> tuple[Any, str]:
        manifest = snapshot.manifest
        if manifest is None:
            raise ModelLifecycleError(
                "manifest_metadata_missing", "verified snapshot has no manifest metadata", retryable=False
            )
        device = manifest.device
        try:
            self._reserve_with_eviction(
                snapshot.manifest_digest,
                ram_bytes=manifest.ram_bytes,
                vram_bytes=manifest.vram_bytes,
                device=device,
            )
            adapter = self._factory(snapshot, device=device)
            _require_ready(adapter)
            return adapter, device
        except BaseException as exc:
            self._resources.release_model(snapshot.manifest_digest)
            fallback_allowed = allow_cpu_fallback and manifest.allow_cpu_fallback and device != "cpu"
            if not fallback_allowed or not _is_capacity_or_oom(exc):
                raise
            self._reserve_with_eviction(
                snapshot.manifest_digest,
                ram_bytes=manifest.ram_bytes,
                vram_bytes=0,
                device="cpu",
            )
            adapter = self._factory(snapshot, device="cpu")
            _require_ready(adapter)
            return adapter, "cpu"

    def _reserve_with_eviction(self, digest: str, *, ram_bytes: int, vram_bytes: int, device: str) -> None:
        try:
            self._resources.reserve_model(digest, ram_bytes=ram_bytes, vram_bytes=vram_bytes, device=device)
        except RestrictedInferenceResourceError:
            if not self.evict_one_idle(exclude_digest=digest):
                raise
            self._resources.reserve_model(digest, ram_bytes=ram_bytes, vram_bytes=vram_bytes, device=device)

    def _release(self, digest: str) -> None:
        with self._condition:
            record = self._records.get(digest)
            if record is None or record.active_leases <= 0:
                return
            record.active_leases -= 1
            record.last_used_ns = self._monotonic_ns()
            if record.active_leases == 0 and record.state is ModelLifecycleState.LOADED:
                self._transition(record, ModelLifecycleState.IDLE)
            self._condition.notify_all()

    def mark_failed(self, digest: str, reason_code: str) -> None:
        adapter: Any = None
        with self._condition:
            record = self._records.get(digest)
            if record is None:
                return
            adapter = record.adapter
            record.adapter = None
            record.failure_code = reason_code
            record.failed_at_ns = self._monotonic_ns()
            record.active_leases = 0
            if record.state in {ModelLifecycleState.LOADED, ModelLifecycleState.IDLE}:
                self._transition(record, ModelLifecycleState.FAILED)
            self._condition.notify_all()
        _close_adapter(adapter)
        self._resources.release_model(digest)

    def evict_one_idle(self, *, exclude_digest: str = "") -> bool:
        with self._condition:
            candidates = [
                (digest, record)
                for digest, record in self._records.items()
                if digest != exclude_digest and record.state is ModelLifecycleState.IDLE and record.active_leases == 0
            ]
            if not candidates:
                return False
            digest, record = min(candidates, key=lambda item: (item[1].last_used_ns, item[0]))
            self._transition(record, ModelLifecycleState.UNLOADING)
            adapter = record.adapter
            record.adapter = None
        try:
            _close_adapter(adapter)
        except Exception:
            with self._condition:
                record = self._records[digest]
                self._transition(record, ModelLifecycleState.FAILED)
                record.failure_code = "unload_failed"
                record.failed_at_ns = self._monotonic_ns()
                self._condition.notify_all()
            self._resources.release_model(digest)
            return False
        self._resources.release_model(digest)
        with self._condition:
            record = self._records[digest]
            self._transition(record, ModelLifecycleState.EVICTED)
            record.loaded_device = ""
            self._condition.notify_all()
        return True

    def evict(self, digest: str) -> bool:
        """Explicitly unload one idle digest; active leases fail closed."""
        adapter: Any = None
        with self._condition:
            record = self._records.get(digest)
            if record is None or record.state is ModelLifecycleState.EVICTED:
                return False
            if record.active_leases or record.state is not ModelLifecycleState.IDLE:
                raise ModelLifecycleError("model_in_use", "active model cannot be evicted")
            self._transition(record, ModelLifecycleState.UNLOADING)
            adapter = record.adapter
            record.adapter = None
        try:
            _close_adapter(adapter)
        except Exception as exc:
            with self._condition:
                record = self._records[digest]
                self._transition(record, ModelLifecycleState.FAILED)
                record.failure_code = "unload_failed"
                record.failed_at_ns = self._monotonic_ns()
                self._condition.notify_all()
            self._resources.release_model(digest)
            raise ModelLifecycleError("unload_failed", "model unload failed") from exc
        self._resources.release_model(digest)
        with self._condition:
            record = self._records[digest]
            self._transition(record, ModelLifecycleState.EVICTED)
            record.loaded_device = ""
            self._condition.notify_all()
        return True

    def evict_idle(self, *, older_than_seconds: float = 0.0) -> int:
        threshold_ns = self._monotonic_ns() - max(0, int(older_than_seconds * 1_000_000_000))
        evicted = 0
        while True:
            with self._condition:
                eligible = any(
                    record.state is ModelLifecycleState.IDLE
                    and record.active_leases == 0
                    and record.last_used_ns <= threshold_ns
                    for record in self._records.values()
                )
            if not eligible or not self.evict_one_idle():
                return evicted
            evicted += 1

    def statuses(self) -> list[ModelStatus]:
        with self._condition:
            return [
                ModelStatus(
                    manifest_id=record.snapshot.manifest_id,
                    manifest_digest=digest,
                    model_id=record.snapshot.model_id,
                    engine=record.snapshot.engine,
                    state=record.state,
                    active_leases=record.active_leases,
                    loaded_device=record.loaded_device,
                    failure_code=record.failure_code,
                )
                for digest, record in sorted(self._records.items())
            ]

    @staticmethod
    def _transition(record: _ModelRecord, target: ModelLifecycleState) -> None:
        if target not in _TRANSITIONS[record.state]:
            raise ModelLifecycleError(
                "invalid_lifecycle_transition",
                f"cannot transition from {record.state.value} to {target.value}",
                retryable=False,
            )
        record.state = target


def _require_ready(adapter: Any) -> None:
    status = adapter.status()
    if getattr(status, "status", "") != "ready":
        raise ModelLifecycleError("adapter_unavailable", "adapter did not become ready")


def _close_adapter(adapter: Any) -> None:
    if adapter is None:
        return
    close = getattr(adapter, "close", None)
    if callable(close):
        close()


def _is_out_of_memory(exc: BaseException) -> bool:
    return (
        isinstance(exc, MemoryError)
        or "outofmemory" in type(exc).__name__.lower()
        or "out of memory" in str(exc).lower()
    )


def _is_capacity_or_oom(exc: BaseException) -> bool:
    return isinstance(exc, RestrictedInferenceResourceError) or _is_out_of_memory(exc)


def _load_error_code(exc: BaseException) -> str:
    if isinstance(exc, (ModelLifecycleError, RestrictedInferenceResourceError)):
        return exc.reason_code
    if _is_out_of_memory(exc):
        return "out_of_memory"
    return "model_load_failed"
