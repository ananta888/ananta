"""Process-level primitives shared by the speech-adaptation SQL/CAS adapters.

All speech-adaptation writers (decision store, capacity leases, artifact
receipts and encrypted exports) serialize on one re-entrant process lock so
that SQLite development deployments keep the same write ordering as before
the adapters were split into separate modules.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

SPEECH_ADAPTATION_WRITE_LOCK = threading.RLock()


def file_sha256(path: Path) -> str:
    """Stream one CAS object and return its lowercase SHA-256 hex digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["SPEECH_ADAPTATION_WRITE_LOCK", "file_sha256"]
