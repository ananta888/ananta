"""Explicit Hub-owned memory lifecycle commands through the scoped SQL port."""

from typing import Protocol


class MemoryQuarantinePort(Protocol):
    def set_chunk_quarantine(self, chunk_id_val: str, *, action: str, reason_code: str) -> bool: ...


class MemoryQuarantineService:
    def __init__(self, store: MemoryQuarantinePort):
        self._store = store

    def quarantine(self, chunk_id: str, *, reason_code: str) -> bool:
        return self._store.set_chunk_quarantine(chunk_id, action="quarantine", reason_code=reason_code)

    def release(self, chunk_id: str, *, reason_code: str) -> bool:
        return self._store.set_chunk_quarantine(chunk_id, action="release", reason_code=reason_code)

    def revoke(self, chunk_id: str, *, reason_code: str) -> bool:
        return self._store.set_chunk_quarantine(chunk_id, action="revoke", reason_code=reason_code)
