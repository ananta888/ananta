"""Tenant-scoped, content-addressed prompt-program artifact storage."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from ananta_contracts.dspy_optimization import PromptProgramV1, canonical_json, require_id


class DspyProgramArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, *, tenant_id: str, run_id: str, program: PromptProgramV1) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        run = require_id(run_id, "run_id")
        if program.tenant_id != tenant:
            raise PermissionError("dspy_artifact_tenant_mismatch")
        payload = canonical_json(program.to_dict()).encode()
        digest = program.digest
        tenant_dir = self._root / tenant
        target_candidate = tenant_dir / run
        if tenant_dir.is_symlink() or target_candidate.is_symlink():
            raise ValueError("dspy_artifact_symlink_denied")
        target_dir = target_candidate.resolve()
        if self._root not in target_dir.parents:
            raise ValueError("dspy_artifact_path_invalid")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest}.json"
        if target.exists():
            if target.read_bytes() != payload:
                raise RuntimeError("dspy_artifact_digest_collision")
        else:
            descriptor, temporary = tempfile.mkstemp(prefix=".dspy-", dir=target_dir)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {
            "schema": program.schema,
            "digest": digest,
            "size_bytes": len(payload),
            "media_type": "application/vnd.ananta.prompt-program+json",
            "producer_run_id": run,
            "tenant_id": tenant,
            "artifact_ref": f"dspy-program:{tenant}:{run}:{digest}",
        }


__all__ = ["DspyProgramArtifactStore"]
