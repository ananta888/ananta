"""Shared contracts for collaboration workspace persistence."""


class CollaborationStoreConflict(RuntimeError):
    """A requested collaboration write conflicts with persisted state."""


__all__ = ["CollaborationStoreConflict"]
