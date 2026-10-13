"""Worker memory stores: session, project, and proposed long-term memory.

EW-T029: Separate stores for worker_session_memory, project_execution_memory,
          and proposed_long_term_memory.
          Worker can write session/project memory only when memory_write capability allows.
          Long-term updates remain proposals; approval and promotion execute only in the Hub.
"""
from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any

from ananta_contracts.memory_access import MemoryOperation, MemoryScope, require_memory_identifier
from worker.core.memory_authorization import WorkerMemoryAuthorizationPort, require_worker_memory

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
    """Bounded RAM execution adapter, isolated by live Hub namespace."""

    def __init__(self, kind: MemoryStoreKind, *, authorization: WorkerMemoryAuthorizationPort | None = None):
        self.kind = kind
        self._authorization = authorization
        self._scope = {MemoryStoreKind.session: MemoryScope.run, MemoryStoreKind.project: MemoryScope.workspace,
                       MemoryStoreKind.long_term: MemoryScope.task}[kind]
        self._entries = {}
        self._lock = RLock()

    def _access(self, operation):
        return require_worker_memory(self._authorization, self._scope, operation)

    def _recheck(self, previous):
        current = self._access(previous.operation)
        if current != previous:
            raise ValueError("memory_policy_changed")

    def _reap(self):
        now = time.time()
        for key in [key for key, (_entry, expiry) in self._entries.items() if expiry <= now]:
            del self._entries[key]

    def write(self, key, value, *, task_id, hub_approved=False):
        if not isinstance(key, str) or not key.strip() or len(key) > 160 or not isinstance(value, str):
            return MemoryWriteResult(False, "tool_schema_invalid")
        try:
            require_memory_identifier(key.strip(), "key")
            value_bytes = len(value.encode("utf-8"))
            if value_bytes > 65536:
                return MemoryWriteResult(False, "memory_value_quota_exceeded")
            access = self._access(MemoryOperation.append)
            if task_id != access.namespace.task_id:
                return MemoryWriteResult(False, "memory_task_binding_mismatch")
            storage_key = (*access.namespace.key(self._scope), key.strip())
            with self._lock:
                self._reap()
                if storage_key in self._entries:
                    # An append grant never becomes an implicit update grant.
                    update = self._access(MemoryOperation.update)
                    if update.namespace != access.namespace:
                        return MemoryWriteResult(False, "memory_policy_changed")
                size = sum(len(entry.value.encode("utf-8")) for item_key, (entry, _expiry)
                           in self._entries.items() if item_key != storage_key)
                if (storage_key not in self._entries and len(self._entries) >= 256) or size + value_bytes > 262144:
                    return MemoryWriteResult(False, "memory_store_quota_exceeded")
                entry = MemoryEntry(key.strip(), value, task_id, self.kind,
                                    is_proposal=self.kind == MemoryStoreKind.long_term, approved=False)
                self._recheck(access)
                self._entries[storage_key] = (entry, access.expires_at)
                reason = "memory_write_proposal" if entry.is_proposal else "memory_write_ok"
                return MemoryWriteResult(True, reason, entry=deepcopy(entry))
        except (ValueError, RuntimeError):
            return MemoryWriteResult(False, "memory_hub_authorization_required")

    def _visible(self):
        try:
            access = self._access(MemoryOperation.read)
            namespace_key = access.namespace.key(self._scope)
            with self._lock:
                self._reap()
                result = [deepcopy(entry) for key, (entry, _expiry) in self._entries.items()
                          if key[:-1] == namespace_key]
                self._recheck(access)
                return result
        except (ValueError, RuntimeError):
            return []

    def read(self, key):
        return next((entry for entry in self._visible() if entry.key == key.strip()), None)

    def search(self, query, *, max_results=10):
        if type(max_results) is not int or not 1 <= max_results <= 100:
            return []
        results = [entry for entry in self._visible()
                   if query.lower() in entry.key.lower() or query.lower() in entry.value.lower()]
        return sorted(results, key=lambda entry: -entry.written_at)[:max_results]

    def pending_proposals(self):
        return [entry for entry in self._visible() if entry.is_proposal and not entry.approved]

    def approve_proposal(self, key):
        # Productive promotion belongs to the Hub. Historical boolean approval is inert.
        return False

    def discard(self):
        """Trusted local terminal cleanup; never a Hub/persistent-memory delete."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    def snapshot(self):
        return [entry.as_dict() for entry in self._visible()]


# ── WorkerMemoryStores ────────────────────────────────────────────────────────

class WorkerMemoryStores:
    """Container for all three memory stores per task execution. EW-T029."""

    def __init__(self, *, authorization: WorkerMemoryAuthorizationPort | None = None) -> None:
        self.session = MemoryStore(MemoryStoreKind.session, authorization=authorization)
        self.project = MemoryStore(MemoryStoreKind.project, authorization=authorization)
        self.long_term = MemoryStore(MemoryStoreKind.long_term, authorization=authorization)
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
