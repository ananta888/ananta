"""Exactly-one-effective Hub scheduler for bounded SFU reconciliation jobs."""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from agent.services.sfu_broadcast_background_job_port import (
    SfuBroadcastBackgroundJobLease,
    SfuBroadcastBackgroundJobPort,
    SfuBroadcastBackgroundJobSpec,
)


class SfuBroadcastScheduledJobPort(Protocol):
    def run(self, context: "SfuBroadcastJobContext") -> str | None: ...


@dataclass(frozen=True, slots=True)
class SfuBroadcastJobContext:
    lease: SfuBroadcastBackgroundJobLease
    _lease_valid: Callable[[], bool]

    @property
    def batch_size_max(self) -> int:
        return self.lease.batch_size_max

    @property
    def resume_cursor(self) -> str | None:
        return self.lease.resume_cursor

    def require_lease(self) -> None:
        if not self._lease_valid():
            raise RuntimeError("sfu_background_job_lease_lost")


class CallableSfuBroadcastJob:
    def __init__(self, callback: Callable[[SfuBroadcastJobContext], str | None]) -> None:
        self._callback = callback

    def run(self, context: SfuBroadcastJobContext) -> str | None:
        context.require_lease()
        result = self._callback(context)
        context.require_lease()
        return result


class SfuBroadcastReconcilerScheduler:
    """Runs only in the Hub; runtimes and workers receive no scheduler surface."""

    def __init__(
        self,
        repository: SfuBroadcastBackgroundJobPort,
        jobs: Mapping[str, SfuBroadcastScheduledJobPort],
        specs: tuple[SfuBroadcastBackgroundJobSpec, ...],
        *,
        owner_id: str | None = None,
        clock=time.time,
        max_parallel_jobs: int = 4,
        tick_seconds: float = 0.25,
    ) -> None:
        self._repository = repository
        self._jobs = dict(jobs)
        self._specs = specs
        self._owner_id = owner_id or f"hub-sfu-scheduler-{uuid.uuid4().hex}"
        self._clock = clock
        self._tick_seconds = max(0.05, tick_seconds)
        self._executor = ThreadPoolExecutor(max_workers=max(1, min(max_parallel_jobs, 16)), thread_name_prefix="sfu-hub-job")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="sfu-hub-reconciler", daemon=True)
            self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)
        self._repository.release_owner(self._owner_id, now=float(self._clock()))
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._thread = None

    def run_once(self) -> dict[str, int]:
        leases: list[tuple[SfuBroadcastBackgroundJobLease, SfuBroadcastScheduledJobPort]] = []
        now = float(self._clock())
        for spec in self._specs:
            job = self._jobs.get(spec.name)
            if job is None:
                continue
            lease = self._repository.claim(spec, owner_id=self._owner_id, now=now)
            if lease is not None:
                leases.append((lease, job))
        futures = {
            self._executor.submit(self._run_job, lease, job): lease
            for lease, job in leases
        }
        if not futures:
            return {"claimed": 0, "completed": 0, "failed": 0}
        max_deadline = max(lease.runtime_deadline_ms for lease in futures.values()) / 1000.0
        done, pending = wait(futures, timeout=max_deadline)
        completed = failed = 0
        for future in done:
            completed += future.exception() is None
            failed += future.exception() is not None
        failed += len(pending)
        for future in pending:
            future.cancel()
        return {"claimed": len(futures), "completed": completed, "failed": failed}

    def _run_job(self, lease: SfuBroadcastBackgroundJobLease, job: SfuBroadcastScheduledJobPort) -> None:
        context = SfuBroadcastJobContext(
            lease=lease,
            _lease_valid=lambda: self._repository.lease_valid(lease, now=float(self._clock())),
        )
        status = "completed"
        reason = "accepted"
        cursor = lease.resume_cursor
        try:
            cursor = job.run(context)
        except Exception as exc:
            status = "failed"
            candidate = getattr(exc, "reason_code", None)
            reason = candidate if isinstance(candidate, str) and candidate.startswith("sfu_") else "sfu_background_job_failed"
        self._repository.finish(
            lease,
            status=status,
            reason_code=reason,
            resume_cursor=cursor,
            now=float(self._clock()),
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                pass
            self._stop.wait(self._tick_seconds)


def load_sfu_broadcast_background_specs(path: str | Path) -> tuple[SfuBroadcastBackgroundJobSpec, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("jobs"), list):
        raise ValueError("sfu_background_config_invalid")
    return tuple(SfuBroadcastBackgroundJobSpec(**item) for item in payload["jobs"])


__all__ = [
    "CallableSfuBroadcastJob",
    "SfuBroadcastJobContext",
    "SfuBroadcastReconcilerScheduler",
    "SfuBroadcastScheduledJobPort",
    "load_sfu_broadcast_background_specs",
]
