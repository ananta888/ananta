from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from agent.config import settings


class ArtifactStore:
    """Filesystem-backed raw artifact storage."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or Path(settings.data_dir) / "artifacts").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def store_bytes(self, *, artifact_id: str, version_number: int, filename: str, content: bytes, media_type: str | None = None) -> dict:
        safe_filename = Path(filename or "artifact.bin").name or "artifact.bin"
        artifact_dir = self.base_dir / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        storage_name = f"v{version_number:04d}__{safe_filename}"
        storage_path = artifact_dir / storage_name
        storage_path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        detected_media_type = media_type or mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
        return {
            "storage_path": str(storage_path),
            "sha256": digest,
            "size_bytes": len(content),
            "media_type": detected_media_type,
            "filename": safe_filename,
        }


artifact_store = ArtifactStore()


def get_artifact_store() -> ArtifactStore:
    return artifact_store
