"""Small authority and immutable-storage seams for Hub Native artifact ingress."""

from typing import Any, Protocol


class NativeArtifactDispatchPort(Protocol):
    def require_current_digest(self, *, session: Any, task: Any, command: Any, now: float) -> str:
        """Authenticate the exact current dispatch lease under the caller transaction."""
        ...


class NativeArtifactStoragePort(Protocol):
    def store_immutable_bytes(
        self, *, artifact_id: str, version_number: int, filename: str, content: bytes,
        expected_sha256: str, media_type: str,
    ) -> dict: ...
