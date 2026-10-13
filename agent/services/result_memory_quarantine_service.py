"""Hub-owned Result-Memory lifecycle commands with derived-summary containment.

A quarantined worker result must not become reachable again through a
Memory-Tree leaf or a parent summary derived from it. This service therefore
mutates the scoped result entry first and then blocks every tree chunk that was
derived from that entry (``provenance_ref = memory_entry:<id>``).

Both ports authorize against current Hub grants. The caller is responsible for
binding a memory-access context that carries the ``hub-result-memory`` grant and
the ``hub-memory-tree`` grant; a missing grant fails closed instead of leaving a
derived summary readable.
"""

from __future__ import annotations

from typing import Protocol

from ananta_contracts.memory_access import MemoryScope


class ResultMemoryQuarantinePort(Protocol):
    def set_quarantine(
        self, entry_id: str, *, action: str, reason_code: str,
        scope: MemoryScope = MemoryScope.task,
    ) -> bool: ...


class DerivedMemoryTreeQuarantinePort(Protocol):
    def set_provenance_quarantine(
        self, provenance_ref: str, *, action: str, reason_code: str,
    ) -> bool: ...


class ResultMemoryQuarantineService:
    """Issue audited quarantine/release/revoke for result memory and its derivation."""

    PROVENANCE_PREFIX = "memory_entry:"

    def __init__(
        self,
        entries: ResultMemoryQuarantinePort,
        *,
        derived_tree: DerivedMemoryTreeQuarantinePort | None = None,
    ):
        self._entries = entries
        self._derived_tree = derived_tree

    def quarantine(self, entry_id: str, *, reason_code: str, scope: MemoryScope = MemoryScope.task) -> bool:
        return self._apply(entry_id, "quarantine", reason_code, scope=scope)

    def release(self, entry_id: str, *, reason_code: str, scope: MemoryScope = MemoryScope.task) -> bool:
        return self._apply(entry_id, "release", reason_code, scope=scope)

    def revoke(self, entry_id: str, *, reason_code: str, scope: MemoryScope = MemoryScope.task) -> bool:
        return self._apply(entry_id, "revoke", reason_code, scope=scope)

    def _apply(self, entry_id: str, action: str, reason_code: str, *, scope: MemoryScope) -> bool:
        if not self._entries.set_quarantine(entry_id, action=action, reason_code=reason_code, scope=scope):
            return False
        # The entry is committed before the derivation is contained, so a direct
        # read is blocked as soon as the command succeeds. Containing the derived
        # tree needs the ``hub-memory-tree`` grant; if it is missing the call
        # raises after the entry is already blocked. The command is idempotent, so
        # the operator fixes policy and retries: the second run contains the
        # derivation without producing a duplicate state change or audit event.
        if self._derived_tree is not None:
            self._derived_tree.set_provenance_quarantine(
                f"{self.PROVENANCE_PREFIX}{entry_id}", action=action, reason_code=reason_code,
            )
        return True
