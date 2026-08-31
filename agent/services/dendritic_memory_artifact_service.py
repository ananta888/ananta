"""Bounded, content-addressed and symlink-safe Memory Pack storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ananta_contracts.dendritic_memory import DendriticMemoryPackManifestV1, canonical_json


class DendriticMemoryArtifactService:
    def __init__(self, root: str | Path, *, max_pack_bytes: int) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_pack_bytes = max_pack_bytes

    def put(
        self, *, manifest: DendriticMemoryPackManifestV1, files: Mapping[str, bytes]
    ) -> dict[str, Any]:
        expected = {str(item["name"]): dict(item) for item in manifest.files}
        if set(files) != set(expected):
            raise ValueError("dendritic_artifact_file_set_mismatch")
        total = sum(len(value) for value in files.values())
        if total > self._max_pack_bytes:
            raise ValueError("dendritic_artifact_size_exceeded")
        for name, content in files.items():
            if name not in {"weights.safetensors", "report.json"} or not isinstance(content, bytes) or not content:
                raise ValueError("dendritic_artifact_file_invalid")
            metadata = expected[name]
            if metadata["size_bytes"] != len(content) or metadata["sha256"] != hashlib.sha256(content).hexdigest():
                raise ValueError("dendritic_artifact_digest_mismatch")
        if manifest.executable:
            _validate_safetensors(files["weights.safetensors"])
        tenant_dir = self._root / manifest.tenant_id
        target_candidate = tenant_dir / manifest.digest
        if tenant_dir.is_symlink() or target_candidate.is_symlink():
            raise ValueError("dendritic_artifact_symlink_denied")
        target = target_candidate.resolve()
        if self._root not in target.parents:
            raise ValueError("dendritic_artifact_path_invalid")
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            self._atomic_write(target / name, content)
        manifest_bytes = canonical_json(manifest.to_dict()).encode()
        self._atomic_write(target / "manifest.json", manifest_bytes)
        return {
            "artifact_ref": f"dendritic-pack:{manifest.tenant_id}:{manifest.digest}",
            "pack_digest": manifest.digest,
            "size_bytes": total + len(manifest_bytes),
            "experimental": True,
            "production_eligible": False,
            "claims_verified": False,
        }

    def delete(self, *, manifest: DendriticMemoryPackManifestV1) -> dict[str, Any]:
        """Remove only the closed file set belonging to one content-addressed pack."""
        target = (self._root / manifest.tenant_id / manifest.digest).resolve()
        if self._root not in target.parents or target.is_symlink():
            raise ValueError("dendritic_artifact_path_invalid")
        removed = 0
        for name in ("weights.safetensors", "report.json", "manifest.json"):
            candidate = target / name
            if candidate.is_symlink():
                raise ValueError("dendritic_artifact_symlink_denied")
            if candidate.exists():
                candidate.unlink()
                removed += 1
        if target.exists():
            try:
                target.rmdir()
                target.parent.rmdir()
            except OSError:
                pass
        return {
            "pack_digest": manifest.digest,
            "removed_files": removed,
            "human_intervention_required": False,
        }

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        if target.exists():
            if target.is_symlink() or target.read_bytes() != content:
                raise RuntimeError("dendritic_artifact_immutable_conflict")
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".dendritic-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


__all__ = ["DendriticMemoryArtifactService"]


def _validate_safetensors(payload: bytes) -> None:
    if len(payload) < 9:
        raise ValueError("dendritic_safetensors_invalid")
    header_length = int.from_bytes(payload[:8], byteorder="little", signed=False)
    if not 2 <= header_length <= 1_048_576 or 8 + header_length > len(payload):
        raise ValueError("dendritic_safetensors_invalid")

    def reject_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("dendritic_safetensors_duplicate_key")
            value[key] = item
        return value

    try:
        header = json.loads(payload[8 : 8 + header_length], object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("dendritic_safetensors_invalid") from exc
    if not isinstance(header, dict) or not header:
        raise ValueError("dendritic_safetensors_invalid")
    data_size = len(payload) - 8 - header_length
    ranges: list[tuple[int, int]] = []
    for name, tensor in header.items():
        if name == "__metadata__":
            if not isinstance(tensor, dict):
                raise ValueError("dendritic_safetensors_metadata_invalid")
            continue
        if not isinstance(name, str) or not name.startswith("memory.") or not isinstance(tensor, dict):
            raise ValueError("dendritic_safetensors_tensor_invalid")
        if set(tensor) != {"dtype", "shape", "data_offsets"}:
            raise ValueError("dendritic_safetensors_tensor_invalid")
        offsets = tensor["data_offsets"]
        shape = tensor["shape"]
        if (
            not isinstance(tensor["dtype"], str)
            or not isinstance(shape, list)
            or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in shape)
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(not isinstance(item, int) or isinstance(item, bool) for item in offsets)
            or not 0 <= offsets[0] <= offsets[1] <= data_size
        ):
            raise ValueError("dendritic_safetensors_tensor_invalid")
        ranges.append((offsets[0], offsets[1]))
    if not ranges or any(left[1] > right[0] for left, right in zip(sorted(ranges), sorted(ranges)[1:])):
        raise ValueError("dendritic_safetensors_offsets_invalid")
