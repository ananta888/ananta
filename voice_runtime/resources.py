from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .config import VoiceRuntimeConfig


@dataclass(frozen=True)
class BackendResourceRequirement:
    """Conservative resources reserved before a backend may start."""

    ram_bytes: int = 0
    vram_bytes: int = 0
    concurrency_slots: int = 1

    def __post_init__(self) -> None:
        if self.ram_bytes < 0 or self.vram_bytes < 0 or self.concurrency_slots <= 0:
            raise ValueError("voice backend resource requirement is invalid")


@dataclass(frozen=True)
class VoiceResourceBudget:
    max_ram_bytes: int
    max_vram_bytes: int
    max_concurrent_backends: int
    max_audio_ms: int
    max_queue_depth: int

    def __post_init__(self) -> None:
        if (
            self.max_ram_bytes < 0
            or self.max_vram_bytes < 0
            or self.max_concurrent_backends <= 0
            or self.max_audio_ms <= 0
            or self.max_queue_depth <= 0
        ):
            raise ValueError("voice resource budget is invalid")

    def narrowed_by(self, requested: VoiceResourceBudget | None) -> VoiceResourceBudget:
        """Return an immutable intersection; callers can never expand runtime limits."""

        if requested is None:
            return self
        return VoiceResourceBudget(
            max_ram_bytes=min(self.max_ram_bytes, requested.max_ram_bytes),
            max_vram_bytes=min(self.max_vram_bytes, requested.max_vram_bytes),
            max_concurrent_backends=min(
                self.max_concurrent_backends,
                requested.max_concurrent_backends,
            ),
            max_audio_ms=min(self.max_audio_ms, requested.max_audio_ms),
            max_queue_depth=min(self.max_queue_depth, requested.max_queue_depth),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "max_ram_bytes": self.max_ram_bytes,
            "max_vram_bytes": self.max_vram_bytes,
            "max_concurrent_backends": self.max_concurrent_backends,
            "max_audio_ms": self.max_audio_ms,
            "max_queue_depth": self.max_queue_depth,
        }


def resource_budget_from_config(config: VoiceRuntimeConfig) -> VoiceResourceBudget:
    """Build the immutable process ceiling once at the composition boundary."""

    return VoiceResourceBudget(
        max_ram_bytes=config.resource_max_ram_mb * 1024 * 1024,
        max_vram_bytes=config.resource_max_vram_mb * 1024 * 1024,
        max_concurrent_backends=min(
            config.resource_max_concurrent_backends,
            config.max_queue_depth,
        ),
        max_audio_ms=min(
            config.resource_max_audio_seconds,
            config.max_audio_duration_sec,
        )
        * 1000,
        max_queue_depth=min(
            config.resource_max_queue_depth,
            config.max_queue_depth,
        ),
    )


class ResourceLease:
    def __init__(
        self,
        controller: ResourceAdmissionController,
        requirement: BackendResourceRequirement,
        audio_ms: int,
    ) -> None:
        self._controller = controller
        self._requirement = requirement
        self._audio_ms = audio_ms
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._controller._release(self._requirement, self._audio_ms)

    def __enter__(self) -> ResourceLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class ResourceAdmissionController:
    """Thread-safe, non-blocking admission for local backend execution."""

    def __init__(self, runtime_budget: VoiceResourceBudget) -> None:
        self._runtime_budget = runtime_budget
        self._lock = threading.Lock()
        self._ram_bytes = 0
        self._vram_bytes = 0
        self._concurrency_slots = 0
        self._audio_ms = 0

    @property
    def runtime_budget(self) -> VoiceResourceBudget:
        return self._runtime_budget

    def effective_budget(
        self,
        requested: VoiceResourceBudget | None,
    ) -> VoiceResourceBudget:
        return self._runtime_budget.narrowed_by(requested)

    def try_acquire(
        self,
        requirement: BackendResourceRequirement,
        *,
        audio_ms: int,
        requested_budget: VoiceResourceBudget | None = None,
    ) -> ResourceLease | None:
        if audio_ms < 0:
            raise ValueError("voice admission audio duration is invalid")
        budget = self.effective_budget(requested_budget)
        with self._lock:
            if (
                self._ram_bytes + requirement.ram_bytes > budget.max_ram_bytes
                or self._vram_bytes + requirement.vram_bytes > budget.max_vram_bytes
                or self._concurrency_slots + requirement.concurrency_slots
                > budget.max_concurrent_backends
                or self._audio_ms + audio_ms > budget.max_audio_ms
            ):
                return None
            self._ram_bytes += requirement.ram_bytes
            self._vram_bytes += requirement.vram_bytes
            self._concurrency_slots += requirement.concurrency_slots
            self._audio_ms += audio_ms
        return ResourceLease(self, requirement, audio_ms)

    def _release(
        self,
        requirement: BackendResourceRequirement,
        audio_ms: int,
    ) -> None:
        with self._lock:
            self._ram_bytes -= requirement.ram_bytes
            self._vram_bytes -= requirement.vram_bytes
            self._concurrency_slots -= requirement.concurrency_slots
            self._audio_ms -= audio_ms


def backend_resource_requirement(backend: object) -> BackendResourceRequirement:
    provider = getattr(backend, "resource_requirements", None)
    if not callable(provider):
        return BackendResourceRequirement()
    raw = provider()
    if isinstance(raw, BackendResourceRequirement):
        return raw
    if isinstance(raw, Mapping):
        return BackendResourceRequirement(
            ram_bytes=int(raw.get("ram_bytes") or 0),
            vram_bytes=int(raw.get("vram_bytes") or 0),
            concurrency_slots=int(raw.get("concurrency_slots") or 1),
        )
    raise ValueError("voice backend resource requirement has an invalid contract")
