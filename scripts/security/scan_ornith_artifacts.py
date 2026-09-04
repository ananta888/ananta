#!/usr/bin/env python3
"""Fail-closed scanner for one already materialized Ornith artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.model_artifact_import_service import ModelSourceManifestLoader  # noqa: E402


def scan(manifest_path: Path, artifact_id: str, artifact_path: Path) -> dict[str, object]:
    manifest = ModelSourceManifestLoader().load(manifest_path)
    artifact = next((item for item in manifest.artifacts if item.artifact_id == artifact_id), None)
    if artifact is None:
        return {"status": "failed", "reason_code": "model_artifact_unknown"}
    if artifact_path.is_symlink() or not artifact_path.is_file():
        return {"status": "failed", "reason_code": "model_artifact_type_forbidden"}
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    with artifact_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if not prefix:
                prefix = chunk[:8]
            size += len(chunk)
            if size > artifact.size_bytes:
                return {"status": "failed", "reason_code": "model_artifact_size_mismatch"}
            digest.update(chunk)
    if size != artifact.size_bytes:
        return {"status": "failed", "reason_code": "model_artifact_size_mismatch"}
    actual = digest.hexdigest()
    if actual != artifact.sha256:
        return {"status": "failed", "reason_code": "model_artifact_digest_mismatch"}
    if artifact.format == "gguf" and prefix[:4] != b"GGUF":
        return {"status": "failed", "reason_code": "model_artifact_magic_invalid"}
    return {
        "status": "passed",
        "artifact_id": artifact.artifact_id,
        "sha256": actual,
        "size_bytes": size,
        "activation": artifact.activation,
        "license_status": artifact.license.status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    result = scan(args.manifest, args.artifact_id, args.artifact)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
