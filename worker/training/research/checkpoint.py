"""Atomic hash-bound checkpoint workspace for one delegated Worker task."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from ananta_contracts.research_training import require_id


class ResearchCheckpointManager:
    def __init__(self, workspace: str | Path, *, max_checkpoint_bytes: int) -> None:
        self._workspace = Path(workspace).resolve()
        self._maximum = int(max_checkpoint_bytes)
        if not 1 <= self._maximum <= 1 << 50:
            raise ValueError("research_checkpoint_limit_invalid")
        self._workspace.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        *,
        stage_id: str,
        attempt_id: str,
        optimizer_step: int,
        content: bytes,
    ) -> dict[str, Any]:
        stage = require_id(stage_id, "stage_id")
        attempt = require_id(attempt_id, "attempt_id")
        if not isinstance(content, bytes) or not 1 <= len(content) <= self._maximum:
            raise ValueError("research_checkpoint_content_invalid")
        if not isinstance(optimizer_step, int) or isinstance(optimizer_step, bool) or optimizer_step < 1:
            raise ValueError("research_checkpoint_optimizer_step_invalid")
        digest = hashlib.sha256(content).hexdigest()
        relative = Path(stage) / f"{attempt}-{digest}.checkpoint"
        target = (self._workspace / relative).resolve()
        if self._workspace not in target.parents:
            raise PermissionError("research_checkpoint_path_escape")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, staging = tempfile.mkstemp(prefix=".checkpoint-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, target)
        finally:
            if os.path.exists(staging):
                os.unlink(staging)
        return {
            "schema": "ananta.research-training-checkpoint-receipt.v1",
            "stage_id": stage,
            "attempt_id": attempt,
            "optimizer_step": optimizer_step,
            "checkpoint_ref": relative.as_posix(),
            "checkpoint_digest": digest,
            "size_bytes": len(content),
        }

    def read(self, *, checkpoint_ref: str, expected_digest: str) -> bytes:
        target = (self._workspace / checkpoint_ref).resolve()
        if self._workspace not in target.parents or not target.is_file() or target.is_symlink():
            raise PermissionError("research_checkpoint_ref_invalid")
        content = target.read_bytes()
        if len(content) > self._maximum or hashlib.sha256(content).hexdigest() != expected_digest:
            raise ValueError("research_checkpoint_digest_mismatch")
        return content


__all__ = ["ResearchCheckpointManager"]
