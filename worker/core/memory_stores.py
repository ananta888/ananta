"""Worker memory stores: session, project, and proposed long-term memory.

EW-T029: Separate stores for worker_session_memory, project_execution_memory,
          and proposed_long_term_memory.
          Worker can write session/project memory only when memory_write capability allows.
          Long-term memory updates are proposals unless Hub approval enables direct write.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Store kinds ───────────────────────────────────────────────────────────────

class MemoryStoreKind(str, Enum):
    session = "worker_session_memory"       # scoped to single execution; auto-discarded
    project = "project_execution_memory"    # scoped to project; persisted across tasks
    long_term = "proposed_long_term_memory" # proposal only; Hub must approve direct write


# ── MemoryEntry ───────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    key: str
    value: str
    source_task_id: str
    store_kind: MemoryStoreKind
    written_at: float = field(default_factory=time.time)
    is_proposal: bool = False    # True for long_term entries awaiting Hub approval
    approved: bool = False       # True only after Hub approval for long_term

    @property
    def entry_hash(self) -> str:
        return hashlib.sha256(f"{self.key}:{self.value}".encode()).hexdigest()[:12]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "store_kind": self.store_kind.value,
            "source_task_id": self.source_task_id,
            "written_at": self.written_at,
            "is_proposal": self.is_proposal,
            "approved": self.approved,
            "entry_hash": self.entry_hash,
            # value is included only for session/project — long_term proposals are Hub-controlled
        }


# ── MemoryWriteResult ─────────────────────────────────────────────────────────

@dataclass
class MemoryWriteResult:
    success: bool
    reason_code: str
    entry: MemoryEntry | None = None
    detail: str = ""


# ── MemoryStore ───────────────────────────────────────────────────────────────

class MemoryStore:
    """A single scoped memory store."""

    def __init__(self, kind: MemoryStoreKind) -> None:
        self.kind = kind
        self._entries: dict[str, MemoryEntry] = {}

    def write(
        self,
        key: str,
        value: str,
        *,
        task_id: str,
        hub_approved: bool = False,
    ) -> MemoryWriteResult:
        """Write an entry. Long-term writes require hub_approved=True or become proposals."""
        if not key or not key.strip():
            return MemoryWriteResult(False, "tool_schema_invalid", detail="key is empty")

        is_proposal = False
        if self.kind == MemoryStoreKind.long_term and not hub_approved:
            is_proposal = True

        entry = MemoryEntry(
            key=key.strip(),
            value=value,
            source_task_id=task_id,
            store_kind=self.kind,
            is_proposal=is_proposal,
            approved=hub_approved and self.kind == MemoryStoreKind.long_term,
        )
        self._entries[key.strip()] = entry
        reason = "memory_write_proposal" if is_proposal else "memory_write_ok"
        return MemoryWriteResult(True, reason, entry=entry)

    def read(self, key: str) -> MemoryEntry | None:
        return self._entries.get(key.strip())

    def search(self, query: str, *, max_results: int = 10) -> list[MemoryEntry]:
        """Simple substring search over keys and values."""
        q = query.lower()
        results = [
            e for e in self._entries.values()
            if q in e.key.lower() or q in e.value.lower()
        ]
        return sorted(results, key=lambda e: -e.written_at)[:max_results]

    def pending_proposals(self) -> list[MemoryEntry]:
        """Long-term entries awaiting Hub approval."""
        return [e for e in self._entries.values() if e.is_proposal and not e.approved]

    def approve_proposal(self, key: str) -> bool:
        """Mark a proposal as approved (called after Hub grants approval)."""
        entry = self._entries.get(key.strip())
        if entry and entry.is_proposal:
            self._entries[key.strip()] = MemoryEntry(
                key=entry.key,
                value=entry.value,
                source_task_id=entry.source_task_id,
                store_kind=entry.store_kind,
                written_at=entry.written_at,
                is_proposal=False,
                approved=True,
            )
            return True
        return False

    def discard(self) -> int:
        """Discard all entries (used for session cleanup). Returns count discarded."""
        count = len(self._entries)
        self._entries.clear()
        return count

    def snapshot(self) -> list[dict[str, Any]]:
        """Safe read-only snapshot of all entries."""
        return [e.as_dict() for e in self._entries.values()]


# ── WorkerMemoryStores ────────────────────────────────────────────────────────

class WorkerMemoryStores:
    """Container for all three memory stores per task execution. EW-T029."""

    def __init__(self) -> None:
        self.session = MemoryStore(MemoryStoreKind.session)
        self.project = MemoryStore(MemoryStoreKind.project)
        self.long_term = MemoryStore(MemoryStoreKind.long_term)
        self._store_map = {
            "worker_session_memory": self.session,
            "project_execution_memory": self.project,
            "proposed_long_term_memory": self.long_term,
        }

    def get_store(self, store_name: str) -> MemoryStore | None:
        return self._store_map.get(store_name)

    def write(
        self,
        store_name: str,
        key: str,
        value: str,
        *,
        task_id: str,
        has_memory_write_capability: bool,
        hub_approved: bool = False,
    ) -> MemoryWriteResult:
        """Unified write interface — enforces capability check."""
        store = self.get_store(store_name)
        if store is None:
            return MemoryWriteResult(
                False, "memory_store_not_found",
                detail=f"store {store_name!r} does not exist",
            )
        if not has_memory_write_capability:
            return MemoryWriteResult(
                False, "memory_write_requires_approval",
                detail="memory_write capability is not granted in this envelope",
            )
        return store.write(key, value, task_id=task_id, hub_approved=hub_approved)

    def discard_session(self) -> int:
        """Discard session memory at end of execution."""
        return self.session.discard()

    def all_proposals(self) -> list[MemoryEntry]:
        """All pending long-term memory proposals."""
        return self.long_term.pending_proposals()
