"""Phase-aware memory instrumentation for the Qdrant benchmark runner."""

from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

import psutil


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def memory_bytes(value: str) -> int | None:
    number = ""
    unit = ""
    for char in value.strip():
        if char.isdigit() or char == ".":
            number += char
        elif number and not char.isspace():
            unit += char
    factors = {
        "B": 1,
        "KB": 1000,
        "KIB": 1024,
        "MB": 1000**2,
        "MIB": 1024**2,
        "GB": 1000**3,
        "GIB": 1024**3,
    }
    try:
        return int(float(number) * factors[unit.upper()])
    except (KeyError, ValueError):
        return None


def container_memory(container: str | None) -> dict[str, Any]:
    if not container or shutil.which("docker") is None:
        return {"available": False, "reason": "container_memory_unavailable"}
    completed = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    raw = completed.stdout.strip().split("/", 1)[0].strip()
    measured = memory_bytes(raw) if completed.returncode == 0 else None
    return {
        "available": measured is not None,
        "bytes": measured,
        "raw": raw,
        "reason": "ok" if measured is not None else "container_memory_unavailable",
    }


@dataclass(frozen=True, slots=True)
class MemorySample:
    phase: str
    observed_at: str
    client_rss_bytes: int
    qdrant_container_rss_bytes: int | None


class MemoryRecorder:
    """Record client and Qdrant peaks across complete benchmark phases."""

    def __init__(
        self,
        container: str | None,
        *,
        observed_at: Callable[[], str] = _utc_now,
        sampling_interval_seconds: float = 1.0,
        client_rss_sampler: Callable[[], int] | None = None,
        container_memory_sampler: Callable[[str | None], Mapping[str, Any]] | None = None,
    ) -> None:
        self._container = container
        process = psutil.Process()
        self._client_rss_sampler = client_rss_sampler or (lambda: int(process.memory_info().rss))
        self._container_memory_sampler = container_memory_sampler or container_memory
        self._observed_at = observed_at
        self._sampling_interval_seconds = max(
            0.001,
            float(sampling_interval_seconds),
        )
        self._samples: list[MemorySample] = []
        self._lock = threading.RLock()
        self._sampling_complete = True

    def sample(self, phase: str) -> None:
        try:
            container = dict(self._container_memory_sampler(self._container))
            sample = MemorySample(
                phase=str(phase),
                observed_at=self._observed_at(),
                client_rss_bytes=int(self._client_rss_sampler()),
                qdrant_container_rss_bytes=(int(container["bytes"]) if container.get("available") else None),
            )
        except Exception:
            with self._lock:
                self._sampling_complete = False
            return
        with self._lock:
            self._samples.append(sample)

    def _sample_until_stopped(
        self,
        phase: str,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(self._sampling_interval_seconds):
            self.sample(f"{phase}:periodic")

    @contextmanager
    def phase(self, phase: str) -> Iterator[None]:
        """Sample start, periodically during the phase, and the final boundary."""

        stop = threading.Event()
        self.sample(f"{phase}:start")
        sampler = threading.Thread(
            target=self._sample_until_stopped,
            args=(phase, stop),
            name=f"qdrant-benchmark-memory-{phase}",
            daemon=True,
        )
        sampler.start()
        try:
            yield
        finally:
            stop.set()
            sampler.join(timeout=25.0)
            if sampler.is_alive():
                with self._lock:
                    self._sampling_complete = False
            self.sample(f"{phase}:end")

    def report(self) -> dict[str, Any]:
        with self._lock:
            samples = list(self._samples)
            sampling_complete = self._sampling_complete
        if not samples:
            return {
                "sampling_interval_seconds": self._sampling_interval_seconds,
                "sampling_complete": False,
                "client": {
                    "method": "psutil.Process.memory_info().rss",
                    "peak_bytes": None,
                    "peak_phase": None,
                    "observed_at": None,
                },
                "qdrant_container": {
                    "method": "docker stats --no-stream MemUsage",
                    "available": False,
                    "peak_bytes": None,
                    "peak_phase": None,
                    "observed_at": None,
                },
                "samples": [],
            }
        client_peak = max(samples, key=lambda item: item.client_rss_bytes)
        container_samples = [item for item in samples if item.qdrant_container_rss_bytes is not None]
        container_peak = (
            max(
                container_samples,
                key=lambda item: int(item.qdrant_container_rss_bytes or 0),
            )
            if container_samples
            else None
        )
        return {
            "sampling_interval_seconds": self._sampling_interval_seconds,
            "sampling_complete": sampling_complete,
            "client": {
                "method": "psutil.Process.memory_info().rss",
                "peak_bytes": client_peak.client_rss_bytes,
                "peak_phase": client_peak.phase,
                "observed_at": client_peak.observed_at,
            },
            "qdrant_container": {
                "method": "docker stats --no-stream MemUsage",
                "available": container_peak is not None,
                "peak_bytes": (container_peak.qdrant_container_rss_bytes if container_peak else None),
                "peak_phase": container_peak.phase if container_peak else None,
                "observed_at": container_peak.observed_at if container_peak else None,
            },
            "samples": [
                {
                    "phase": item.phase,
                    "observed_at": item.observed_at,
                    "client_rss_bytes": item.client_rss_bytes,
                    "qdrant_container_rss_bytes": item.qdrant_container_rss_bytes,
                }
                for item in samples
            ],
        }


__all__ = [
    "MemoryRecorder",
    "container_memory",
    "memory_bytes",
]
