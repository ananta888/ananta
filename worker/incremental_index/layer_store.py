"""Content-addressed immutable storage for artifact layers."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class LayerMetadata:
    """Metadata for a stored layer."""

    layer_id: str
    snapshot_revision: str
    profile_digest: str
    created_at: str
    size_bytes: int
    artifact_count: int
    file_path: str


class ArtifactLayerStore:
    """Content-addressed storage for artifact layers."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _compute_layer_id(self, layer_data: dict[str, Any]) -> str:
        body = {key: value for key, value in layer_data.items() if key not in {"layer_id", "created_at"}}
        return canonical_digest(body)

    def _layer_dir(self, layer_id: str) -> Path:
        return self.base_path / "layers" / layer_id[:2] / layer_id

    def _layer_path(self, layer_id: str) -> Path:
        return self._layer_dir(layer_id) / f"{layer_id}.json.gz"

    def _artifacts_path(self, layer_id: str) -> Path:
        return self._layer_dir(layer_id) / "artifacts"

    def store_layer(self, layer_data: dict[str, Any]) -> tuple[str, bool]:
        staging = dict(layer_data)
        layer_id = self._compute_layer_id(staging)
        staging["layer_id"] = layer_id
        staging.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        target = self._layer_path(layer_id)
        if target.exists():
            return layer_id, False
        staging_dir = self._layer_dir(layer_id).with_name(layer_id + ".staging")
        if staging_dir.exists():
            raise ValueError("partial_layer_upload")
        staging_dir.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(staging, sort_keys=True, separators=(",", ":")).encode("utf-8")
        staged_file = staging_dir / f"{layer_id}.json.gz"
        staged_file.write_bytes(gzip.compress(blob))
        read_back = json.loads(gzip.decompress(staged_file.read_bytes()))
        if self._compute_layer_id(read_back) != layer_id:
            raise ValueError("digest_mismatch")
        final_dir = self._layer_dir(layer_id)
        staging_dir.replace(final_dir)
        return layer_id, True

    def get_layer(self, layer_id: str) -> dict[str, Any] | None:
        path = self._layer_path(layer_id)
        if not path.exists():
            return None
        payload = json.loads(gzip.decompress(path.read_bytes()))
        expected = self._compute_layer_id(payload)
        if expected != layer_id:
            raise ValueError("digest_mismatch")
        return payload

    def has_layer(self, layer_id: str) -> bool:
        return self._layer_path(layer_id).exists()

    def delete_layer(self, layer_id: str) -> bool:
        path = self._layer_dir(layer_id)
        if not path.exists():
            return False
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            else:
                child.rmdir()
        path.rmdir()
        return True

    def list_layers(self) -> list[LayerMetadata]:
        rows: list[LayerMetadata] = []
        root = self.base_path / "layers"
        if not root.exists():
            return rows
        for path in sorted(root.glob("*/*/*.json.gz")):
            payload = json.loads(gzip.decompress(path.read_bytes()))
            rows.append(
                LayerMetadata(
                    layer_id=str(payload.get("layer_id") or path.stem.replace(".json", "")),
                    snapshot_revision=str(payload.get("snapshot_revision") or ""),
                    profile_digest=str(payload.get("profile_digest") or ""),
                    created_at=str(payload.get("created_at") or ""),
                    size_bytes=path.stat().st_size,
                    artifact_count=len(list(payload.get("records") or [])),
                    file_path=str(path),
                )
            )
        return rows

    def get_layer_artifact_path(self, layer_id: str, artifact_type: str) -> Path:
        return self._artifacts_path(layer_id) / artifact_type

    def get_store_statistics(self) -> dict[str, Any]:
        layers = self.list_layers()
        return {
            "layer_count": len(layers),
            "total_bytes": sum(item.size_bytes for item in layers),
        }
