from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class EnrichmentChangeSet:
    unchanged_record_ids: tuple[str, ...]
    enrich_record_ids: tuple[str, ...]
    tombstone_record_ids: tuple[str, ...]
    invalidation_reason: str = "content_changed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "unchanged_record_ids": list(self.unchanged_record_ids),
            "enrich_record_ids": list(self.enrich_record_ids),
            "tombstone_record_ids": list(self.tombstone_record_ids),
            "invalidation_reason": self.invalidation_reason,
        }


def plan_incremental_enrichment(
    *,
    previous_documents: Sequence[Mapping[str, Any]],
    current_documents: Sequence[Mapping[str, Any]],
    previous_profile_digest: str,
    current_profile_digest: str,
) -> EnrichmentChangeSet:
    previous = {
        str(item.get("record_id") or ""): str(item.get("document_hash") or "")
        for item in previous_documents
        if str(item.get("record_id") or "")
    }
    current = {
        str(item.get("record_id") or ""): str(item.get("document_hash") or "")
        for item in current_documents
        if str(item.get("record_id") or "")
    }
    if previous_profile_digest != current_profile_digest:
        return EnrichmentChangeSet(
            unchanged_record_ids=(),
            enrich_record_ids=tuple(sorted(current)),
            tombstone_record_ids=tuple(sorted(set(previous).difference(current))),
            invalidation_reason="profile_dependency_changed",
        )
    unchanged = tuple(sorted(record_id for record_id in current if previous.get(record_id) == current[record_id]))
    enrich = tuple(sorted(record_id for record_id in current if previous.get(record_id) != current[record_id]))
    tombstones = tuple(sorted(set(previous).difference(current)))
    return EnrichmentChangeSet(unchanged, enrich, tombstones)


class EnrichmentLayerStore:
    """Atomic base/delta artifact store; activation is pointer-last and idempotent."""

    def __init__(self, *, root: str | Path):
        self._root = Path(root)
        self._layers = self._root / "layers"
        self._active = self._root / "active.json"

    def write_layer(
        self,
        *,
        layer_kind: str,
        parent_layer_id: str,
        artifacts: Sequence[Mapping[str, Any]],
        tombstone_record_ids: Sequence[str],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        if layer_kind not in {"base", "delta"}:
            raise ValueError("sira_layer_kind_invalid")
        canonical = {
            "schema": "codecompass.sira-enrichment-layer.v1",
            "layer_kind": layer_kind,
            "parent_layer_id": str(parent_layer_id or ""),
            "binding": dict(binding),
            "artifacts": sorted(
                (dict(item) for item in artifacts),
                key=lambda item: str(item.get("source_chunk_id") or ""),
            ),
            "tombstone_record_ids": sorted({str(item) for item in tombstone_record_ids if str(item)}),
        }
        digest = self._digest(canonical)
        layer_id = f"sira-{layer_kind}-{digest[:24]}"
        payload = {**canonical, "layer_id": layer_id, "layer_digest": digest}
        path = self._layers / f"{layer_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("sira_layer_id_collision")
            return payload
        self._atomic_write(path, payload)
        return payload

    def activate(
        self,
        *,
        base_layer_id: str,
        delta_layer_ids: Sequence[str],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        layer_ids = [str(base_layer_id), *(str(item) for item in delta_layer_ids)]
        if not layer_ids[0] or any(not (self._layers / f"{layer_id}.json").is_file() for layer_id in layer_ids):
            raise ValueError("sira_activation_layer_missing")
        payload = {
            "schema": "codecompass.sira-active-layers.v1",
            "base_layer_id": layer_ids[0],
            "delta_layer_ids": layer_ids[1:],
            "binding": dict(binding),
        }
        payload["activation_digest"] = self._digest(payload)
        self._atomic_write(self._active, payload)
        return payload

    def active(self) -> dict[str, Any] | None:
        if not self._active.is_file():
            return None
        return dict(json.loads(self._active.read_text(encoding="utf-8")))

    def materialize_active(self) -> dict[str, Mapping[str, Any]]:
        active = self.active()
        if active is None:
            return {}
        result: dict[str, Mapping[str, Any]] = {}
        layer_ids = [active["base_layer_id"], *list(active.get("delta_layer_ids") or [])]
        for layer_id in layer_ids:
            layer = json.loads((self._layers / f"{layer_id}.json").read_text(encoding="utf-8"))
            for record_id in list(layer.get("tombstone_record_ids") or []):
                result.pop(str(record_id), None)
            for artifact in list(layer.get("artifacts") or []):
                result[str(artifact.get("source_chunk_id") or "")] = dict(artifact)
        return result

    def compact(self) -> dict[str, Any]:
        active = self.active()
        if active is None:
            raise ValueError("sira_active_layers_missing")
        artifacts = list(self.materialize_active().values())
        base = self.write_layer(
            layer_kind="base",
            parent_layer_id="",
            artifacts=artifacts,
            tombstone_record_ids=(),
            binding=dict(active.get("binding") or {}),
        )
        return self.activate(base_layer_id=str(base["layer_id"]), delta_layer_ids=(), binding=active["binding"])

    def diagnostics(self) -> dict[str, Any]:
        active = self.active()
        if active is None:
            return {"status": "degraded", "reason": "sira_active_layers_missing"}
        materialized = self.materialize_active()
        return {
            "status": "ready",
            "reason": "sira_layers_current",
            "base_layer_id": active["base_layer_id"],
            "delta_layer_ids": list(active.get("delta_layer_ids") or []),
            "artifact_count": len(materialized),
            "activation_digest": active["activation_digest"],
        }

    @staticmethod
    def _digest(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
