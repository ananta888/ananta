"""Lifecycle admission for chunks and their derived summaries."""

READABLE_LIFECYCLES = frozenset({"pending_extraction", "admitted", "buffered", "sealed"})
BLOCKED_LIFECYCLES = frozenset({"dropped", "quarantined", "revoked"})
