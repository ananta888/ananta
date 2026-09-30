"""Error contract of semantic relay persistence adapters.

Relay stores raise this reason-coded error for quota and conflict decisions.
It is a dependency-free value type so Hub routes can map it to HTTP status
codes without importing ``agent.repositories``;
``agent.repositories.semantic_relay_repository`` re-exports it unchanged.
"""

from __future__ import annotations


class SemanticRelayRepositoryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


__all__ = ["SemanticRelayRepositoryError"]
