from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from agent.sources.source_snapshot_store import SourceSnapshotStore


class WikimediaDownloader:
    def __init__(self, *, snapshot_store: SourceSnapshotStore | None = None, chunk_size: int = 1024 * 256) -> None:
        self.snapshot_store = snapshot_store or SourceSnapshotStore()
        self.chunk_size = int(chunk_size)

    def download(
        self,
        *,
        source_id: str,
        descriptor_hash: str,
        url: str,
        destination: Path,
        max_parallel: int = 1,
    ) -> dict[str, Any]:
        if int(max_parallel) < 1:
            raise ValueError("max_parallel_must_be_positive")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(destination.suffix + ".part")
        existing = temp_path.stat().st_size if temp_path.exists() else 0
        headers: dict[str, str] = {}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(url, headers=headers)
        resume_used = existing > 0
        status = "downloading"
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                content_range = str(response.headers.get("Content-Range") or "").lower()
                if existing > 0 and not content_range.startswith(f"bytes {existing}-"):
                    # Server ignored Range; restart deterministically from byte 0.
                    existing = 0
                    resume_used = False
                    temp_path.write_bytes(b"")
                mode = "ab" if resume_used else "wb"
                with temp_path.open(mode) as fh:
                    while True:
                        chunk = response.read(self.chunk_size)
                        if not chunk:
                            break
                        fh.write(chunk)
            os.replace(temp_path, destination)
            size = destination.stat().st_size
            snapshot = self.snapshot_store.build_snapshot(
                source_id=source_id,
                descriptor_hash=descriptor_hash,
                content_payload={"url": url, "destination": str(destination)},
                metadata_payload={"byte_size": size},
                status="indexed",
            )
            self.snapshot_store.save_snapshot(snapshot)
            return {
                "status": "indexed",
                "destination": str(destination),
                "byte_size": size,
                "resumed_from_bytes": existing if resume_used else 0,
                "snapshot": snapshot,
            }
        except Exception as exc:
            status = "failed"
            if temp_path.exists() and existing == 0:
                temp_path.unlink(missing_ok=True)
            snapshot = self.snapshot_store.build_snapshot(
                source_id=source_id,
                descriptor_hash=descriptor_hash,
                content_payload={"url": url, "destination": str(destination)},
                metadata_payload={"error": str(exc)},
                status=status,
                reason_code="download_failed",
                human_message=str(exc),
            )
            self.snapshot_store.save_snapshot(snapshot)
            raise

    def cleanup_failed_partials(self, *, directory: Path) -> int:
        removed = 0
        for path in directory.glob("*.part"):
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def ensure_disk_capacity(self, *, target: Path, required_bytes: int) -> bool:
        usage = shutil.disk_usage(target.parent if target.parent.exists() else Path("."))
        return int(usage.free) >= int(required_bytes)
