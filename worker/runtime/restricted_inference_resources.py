"""Bounded resource and request leases for restricted inference workers."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


class RestrictedInferenceResourceError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True)
class ResourceBudget:
    max_ram_bytes: int = 8 * 1024 * 1024 * 1024
    max_vram_bytes: int = 0
    max_loaded_models: int = 2
    max_in_flight: int = 2
    max_queue: int = 8

    def __post_init__(self) -> None:
        for name in ("max_ram_bytes", "max_vram_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_loaded_models", "max_in_flight"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.max_queue, int) or isinstance(self.max_queue, bool) or self.max_queue < 0:
            raise ValueError("max_queue must be a non-negative integer")


@dataclass(frozen=True)
class DeviceAvailability:
    ram_bytes: int | None
    vram_bytes: int | None


class DeviceResourceProbe:
    """Observe free memory without importing optional ML packages on the hub."""

    def available(self, device: str) -> DeviceAvailability:
        ram = _available_ram_bytes()
        if not device.startswith("cuda"):
            return DeviceAvailability(ram_bytes=ram, vram_bytes=None)
        try:
            import torch  # type: ignore[import]

            index = 0
            if ":" in device:
                index = int(device.split(":", 1)[1])
            free, _total = torch.cuda.mem_get_info(index)
            return DeviceAvailability(ram_bytes=ram, vram_bytes=int(free))
        except Exception:
            return DeviceAvailability(ram_bytes=ram, vram_bytes=0)


def _available_ram_bytes() -> int | None:
    try:
        pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return pages * page_size
    except (OSError, TypeError, ValueError):
        return None


class ResourceLeaseManager:
    """Reserve model memory and bound concurrent/queued requests."""

    def __init__(
        self,
        budget: ResourceBudget | None = None,
        *,
        probe: DeviceResourceProbe | None = None,
    ) -> None:
        self.budget = budget or ResourceBudget()
        self._probe = probe or DeviceResourceProbe()
        self._condition = threading.Condition(threading.RLock())
        self._reservations: dict[str, tuple[int, int, str]] = {}
        self._active = 0
        self._waiting = 0

    def reserve_model(self, digest: str, *, ram_bytes: int, vram_bytes: int, device: str) -> None:
        with self._condition:
            if digest in self._reservations:
                return
            if len(self._reservations) >= self.budget.max_loaded_models:
                raise RestrictedInferenceResourceError("model_capacity_exhausted", "loaded model limit reached")
            reserved_ram = sum(item[0] for item in self._reservations.values())
            reserved_vram = sum(item[1] for item in self._reservations.values())
            if reserved_ram + ram_bytes > self.budget.max_ram_bytes:
                raise RestrictedInferenceResourceError("ram_budget_exhausted", "worker RAM budget exhausted")
            if vram_bytes and reserved_vram + vram_bytes > self.budget.max_vram_bytes:
                raise RestrictedInferenceResourceError("vram_budget_exhausted", "worker VRAM budget exhausted")
            available = self._probe.available(device)
            if available.ram_bytes is not None and ram_bytes > available.ram_bytes:
                raise RestrictedInferenceResourceError("ram_unavailable", "insufficient free RAM")
            if device.startswith("cuda") and (available.vram_bytes or 0) < vram_bytes:
                raise RestrictedInferenceResourceError("vram_unavailable", "insufficient free VRAM")
            self._reservations[digest] = (ram_bytes, vram_bytes, device)

    def release_model(self, digest: str) -> None:
        with self._condition:
            self._reservations.pop(digest, None)
            self._condition.notify_all()

    @contextmanager
    def execution(self, *, deadline_epoch_ms: int, epoch_ms: Callable[[], int] | None = None) -> Iterator[None]:
        clock = epoch_ms or (lambda: time.time_ns() // 1_000_000)
        admitted = False
        with self._condition:
            if self._active >= self.budget.max_in_flight:
                if self._waiting >= self.budget.max_queue:
                    raise RestrictedInferenceResourceError("queue_full", "restricted inference queue is full")
                self._waiting += 1
                try:
                    while self._active >= self.budget.max_in_flight:
                        remaining_ms = deadline_epoch_ms - clock()
                        if remaining_ms <= 0:
                            raise RestrictedInferenceResourceError("timeout", "queue deadline expired")
                        self._condition.wait(timeout=min(remaining_ms / 1000.0, 0.25))
                finally:
                    self._waiting -= 1
            if deadline_epoch_ms <= clock():
                raise RestrictedInferenceResourceError("timeout", "execution deadline expired")
            self._active += 1
            admitted = True
        try:
            yield
        finally:
            if admitted:
                with self._condition:
                    self._active -= 1
                    self._condition.notify()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "active": self._active,
                "queued": self._waiting,
                "loaded_models": len(self._reservations),
                "reserved_ram_bytes": sum(item[0] for item in self._reservations.values()),
                "reserved_vram_bytes": sum(item[1] for item in self._reservations.values()),
            }
