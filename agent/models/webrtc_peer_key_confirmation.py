"""Error contract of the opaque WebRTC peer key-confirmation store.

Dependency-free so Hub routes and services can handle confirmation conflicts
without importing ``agent.repositories``;
``agent.repositories.webrtc_peer_key_repository`` re-exports it unchanged.
"""

from __future__ import annotations


class WebrtcPeerKeyRepositoryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


__all__ = ["WebrtcPeerKeyRepositoryError"]
