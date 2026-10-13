"""Bounded, process-local scratch storage; no files, SQL, vectors or promotion."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from ananta_contracts.memory_access import require_memory_identifier, require_memory_timestamp


class ScratchpadStorageError(ValueError):
    pass


@dataclass(frozen=True)
class ScratchpadEntry:
    value: str
    expires_at: float


class InMemoryScratchpadStore:
    """Serialize quota checks and mutations so concurrent appends cannot exceed limits."""

    def __init__(self, *, max_runs: int = 256, max_entries_per_run: int = 128,
                 max_bytes_per_run: int = 262144, max_value_bytes: int = 65536):
        limits = (max_runs, max_entries_per_run, max_bytes_per_run, max_value_bytes)
        if any(type(value) is not int or value <= 0 for value in limits):
            raise ScratchpadStorageError("scratchpad_limits_invalid")
        if max_runs > 4096 or max_entries_per_run > 4096 or max_bytes_per_run > 16 * 1024 * 1024:
            raise ScratchpadStorageError("scratchpad_limits_invalid")
        if max_value_bytes > max_bytes_per_run:
            raise ScratchpadStorageError("scratchpad_limits_invalid")
        self._max_runs, self._max_entries, self._max_bytes, self._max_value = limits
        self._runs: dict[tuple[str, ...], dict[str, ScratchpadEntry]] = {}
        self._lock = RLock()

    def read(self, namespace: tuple[str, ...], key: str, *, now: float) -> str | None:
        require_memory_identifier(key, "scratchpad_key")
        with self._lock:
            self._reap(now)
            entry = self._runs.get(namespace, {}).get(key)
            return entry.value if entry else None

    def append(self, namespace, key, value, *, expires_at, now) -> None:
        self._write(namespace, key, value, expires_at=expires_at, now=now, update=False)

    def update(self, namespace, key, value, *, expires_at, now) -> None:
        self._write(namespace, key, value, expires_at=expires_at, now=now, update=True)

    def _write(self, namespace, key, value, *, expires_at, now, update):
        require_memory_identifier(key, "scratchpad_key")
        require_memory_timestamp(expires_at, "scratchpad_expires_at")
        if not isinstance(value, str):
            raise ScratchpadStorageError("scratchpad_value_type_invalid")
        if len(value) > self._max_value:
            raise ScratchpadStorageError("scratchpad_value_quota_exceeded")
        try:
            encoded = value.encode("utf-8")
        except UnicodeError as exc:
            raise ScratchpadStorageError("scratchpad_value_encoding_invalid") from exc
        if len(encoded) > self._max_value:
            raise ScratchpadStorageError("scratchpad_value_quota_exceeded")
        with self._lock:
            self._reap(now)
            if expires_at <= now:
                raise ScratchpadStorageError("scratchpad_expired")
            entries = self._runs.get(namespace, {})
            if update and key not in entries:
                raise ScratchpadStorageError("scratchpad_entry_missing")
            if not update and key in entries:
                raise ScratchpadStorageError("scratchpad_entry_exists")
            if namespace not in self._runs and len(self._runs) >= self._max_runs:
                raise ScratchpadStorageError("scratchpad_run_quota_exceeded")
            if key not in entries and len(entries) >= self._max_entries:
                raise ScratchpadStorageError("scratchpad_entry_quota_exceeded")
            size = sum(len(k.encode("utf-8")) + len(v.value.encode("utf-8"))
                       for k, v in entries.items() if k != key)
            if size + len(key.encode("utf-8")) + len(value.encode("utf-8")) > self._max_bytes:
                raise ScratchpadStorageError("scratchpad_byte_quota_exceeded")
            self._runs.setdefault(namespace, {})[key] = ScratchpadEntry(value, expires_at)

    def delete(self, namespace, key, *, now) -> bool:
        require_memory_identifier(key, "scratchpad_key")
        with self._lock:
            self._reap(now)
            entries = self._runs.get(namespace, {})
            removed = entries.pop(key, None) is not None
            if not entries:
                self._runs.pop(namespace, None)
            return removed

    def discard(self, namespace: tuple[str, ...]) -> int:
        with self._lock:
            return len(self._runs.pop(namespace, {}))

    def discard_task(self, *, tenant_id: str, workspace_id: str, task_id: str) -> int:
        """Hub terminal hook; remove every run of this exact task, across goal changes."""
        for name, value in (("tenant_id", tenant_id), ("workspace_id", workspace_id), ("task_id", task_id)):
            require_memory_identifier(value, name)
        prefix = ("run", tenant_id, workspace_id, task_id)
        with self._lock:
            namespaces = [key for key in self._runs if key[:4] == prefix]
            return sum(len(self._runs.pop(key)) for key in namespaces)

    def reap(self, *, now: float) -> int:
        with self._lock:
            return self._reap(now)

    def _reap(self, now):
        require_memory_timestamp(now, "scratchpad_clock")
        removed = 0
        for namespace, entries in list(self._runs.items()):
            expired = [key for key, entry in entries.items() if now >= entry.expires_at]
            for key in expired:
                del entries[key]
            removed += len(expired)
            if not entries:
                del self._runs[namespace]
        return removed
