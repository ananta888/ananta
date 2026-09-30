"""Host resource sampling (RSS, file descriptors, I/O, energy, GPU) for the semantic-media program benchmark."""

from __future__ import annotations

import os
import platform
import resource
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.benchmark.semantic_media_program_measurements import (
    _measured,
    MetricState,
    ProgramBenchmarkExecutionError,
    _unavailable,
)


_NVIDIA_SMI = shutil.which("nvidia-smi")


@dataclass(frozen=True, slots=True)
class _ResourceStart:
    wall_ns: int
    cpu_ns: int
    io_bytes: int | None
    energy_uj: int | None
    gpu: tuple[int, int] | None


class ResourceMonitor:
    """Measure process resources behind one small sampling interface (SRP)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._maximum_rss = _rss_bytes()
        self._maximum_fds = _open_file_descriptors()
        self._maximum_vram = 0
        self._gpu_utilization_sum = 0
        self._gpu_samples = 0
        self._thread: threading.Thread | None = None
        self._start: _ResourceStart | None = None

    def start(self) -> None:
        if self._start is not None:
            raise ProgramBenchmarkExecutionError("program_benchmark_monitor_reused")
        self._start = _ResourceStart(
            wall_ns=time.perf_counter_ns(),
            cpu_ns=time.process_time_ns(),
            io_bytes=_process_io_bytes(),
            energy_uj=_energy_uj(),
            gpu=_gpu_snapshot(),
        )
        if self._start.gpu is not None:
            self._maximum_vram = self._start.gpu[1]
        self._thread = threading.Thread(target=self._sample, name="semantic-media-program-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> tuple[dict[str, int | None], dict[str, MetricState]]:
        if self._start is None:
            raise ProgramBenchmarkExecutionError("program_benchmark_monitor_not_started")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise ProgramBenchmarkExecutionError("program_benchmark_monitor_unbounded")
        cpu_micros = max(1, (time.process_time_ns() - self._start.cpu_ns) // 1000)
        end_io = _process_io_bytes()
        end_energy = _energy_uj()
        end_gpu = _gpu_snapshot()
        values: dict[str, int | None] = {
            "cpu_micros": cpu_micros,
            "ram_bytes": self._maximum_rss,
            "open_resources": self._maximum_fds,
            "disk_bytes": None,
            "energy_microwh": None,
            "gpu_micros": None,
            "vram_bytes": None,
        }
        availability = {
            "cpu_micros": _measured("process_time_ns"),
            "ram_bytes": _measured("proc_rss_sampler"),
            "open_resources": _measured("proc_fd_sampler"),
            "disk_bytes": _unavailable("proc_io_unavailable"),
            "energy_microwh": _unavailable("rapl_energy_counter_unavailable"),
            "gpu_micros": _unavailable("gpu_sampler_unavailable"),
            "vram_bytes": _unavailable("gpu_sampler_unavailable"),
        }
        if self._start.io_bytes is not None and end_io is not None:
            values["disk_bytes"] = max(0, end_io - self._start.io_bytes)
            availability["disk_bytes"] = _measured("proc_process_io")
        if self._start.energy_uj is not None and end_energy is not None and end_energy >= self._start.energy_uj:
            values["energy_microwh"] = (end_energy - self._start.energy_uj) * 1000 // 3600
            availability["energy_microwh"] = _measured("linux_rapl_energy_counter")
        if self._start.gpu is not None and end_gpu is not None:
            wall_micros = max(1, (time.perf_counter_ns() - self._start.wall_ns) // 1000)
            average_utilization = (
                self._gpu_utilization_sum // self._gpu_samples
                if self._gpu_samples
                else (self._start.gpu[0] + end_gpu[0]) // 2
            )
            values["gpu_micros"] = wall_micros * average_utilization // 100
            values["vram_bytes"] = max(self._maximum_vram, self._start.gpu[1], end_gpu[1])
            availability["gpu_micros"] = _measured("nvidia_smi_device_sampler")
            availability["vram_bytes"] = _measured("nvidia_smi_device_sampler")
        return values, availability

    def _sample(self) -> None:
        while not self._stop.wait(0.01):
            self._maximum_rss = max(self._maximum_rss, _rss_bytes())
            self._maximum_fds = max(self._maximum_fds, _open_file_descriptors())
            gpu = _gpu_snapshot()
            if gpu is not None:
                self._gpu_utilization_sum += gpu[0]
                self._gpu_samples += 1
                self._maximum_vram = max(self._maximum_vram, gpu[1])


def _hardware_descriptor() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "logical_cpus": os.cpu_count() or 1,
        "page_size": int(os.sysconf("SC_PAGE_SIZE")),
        "physical_memory_bytes": int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")),
        "gpu_sampler": _gpu_snapshot() is not None,
        "energy_sampler": _energy_uj() is not None,
    }


def _rss_bytes() -> int:
    try:
        fields = (Path("/proc/self/statm")).read_text(encoding="ascii").split()
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if platform.system() == "Darwin" else value * 1024)


def _open_file_descriptors() -> int:
    try:
        return len(tuple(Path("/proc/self/fd").iterdir()))
    except OSError:
        return 0


def _process_io_bytes() -> int | None:
    try:
        values = {
            key.rstrip(":"): int(value)
            for key, value in (line.split() for line in Path("/proc/self/io").read_text(encoding="ascii").splitlines())
        }
        return values["read_bytes"] + values["write_bytes"]
    except (OSError, ValueError, KeyError):
        return None


def _energy_uj() -> int | None:
    paths = sorted(Path("/sys/class/powercap").glob("**/energy_uj"))
    if not paths:
        return None
    try:
        return sum(int(path.read_text(encoding="ascii").strip()) for path in paths)
    except (OSError, ValueError):
        return None


def _gpu_snapshot() -> tuple[int, int] | None:
    if _NVIDIA_SMI is None:
        return None
    try:
        completed = subprocess.run(
            [
                _NVIDIA_SMI,
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        rows = [tuple(int(part.strip()) for part in line.split(",")) for line in completed.stdout.splitlines() if line]
    except ValueError:
        return None
    if not rows or any(len(row) != 2 for row in rows):
        return None
    utilization = sum(row[0] for row in rows) // len(rows)
    vram_bytes = sum(row[1] for row in rows) * 1024 * 1024
    return utilization, vram_bytes
