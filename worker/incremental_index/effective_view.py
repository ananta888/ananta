"""Resolve newest-wins effective records across base + deltas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TOMBSTONE = "__tombstone__"


def overlay_records(*layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for record in layer:
            record_id = str(record.get("id") or record.get("record_id") or "")
            if not record_id:
                continue
            if record.get("operation") == "tombstone" or record.get("tombstone") is True:
                merged.pop(record_id, None)
                continue
            merged[record_id] = dict(record)
    return [merged[key] for key in sorted(merged)]


@dataclass
class EffectiveArtifact:
    artifact_id: str
    artifact_type: str
    content_hash: str
    layer_id: str
    layer_priority: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "content_hash": self.content_hash,
            "layer_id": self.layer_id,
            "layer_priority": self.layer_priority,
            "metadata": self.metadata,
        }


@dataclass
class EffectiveView:
    head_name: str
    head_generation: int
    layer_chain: list[str]
    artifacts: dict[str, EffectiveArtifact]
    total_artifacts: int
    layers_scanned: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "head_name": self.head_name,
            "head_generation": self.head_generation,
            "layer_chain": list(self.layer_chain),
            "artifacts": {key: value.to_dict() for key, value in self.artifacts.items()},
            "total_artifacts": self.total_artifacts,
            "layers_scanned": self.layers_scanned,
        }


class LayeredEffectiveViewResolver:
    def __init__(self, layer_store, head_registry, skip_corrupt: bool = True) -> None:
        self.layer_store = layer_store
        self.head_registry = head_registry
        self.skip_corrupt = skip_corrupt

    def resolve_effective_view(self, head_name: str, artifact_types: list[str] | None = None) -> EffectiveView:
        head = self.head_registry.get_head(head_name) or {}
        base = dict(head.get("base_layer_set") or {})
        chain = [str(value) for key, value in sorted(base.items()) if value]
        for delta in list(head.get("ordered_delta_sets") or []):
            normalized = dict(delta) if isinstance(delta, dict) else {"default": str(delta)}
            chain.extend(str(value) for key, value in sorted(normalized.items()) if value)
        artifacts: dict[str, EffectiveArtifact] = {}
        scanned = 0
        for priority, layer_id in enumerate(chain):
            try:
                layer = self.layer_store.get_layer(layer_id)
            except ValueError:
                if self.skip_corrupt:
                    continue
                raise
            if not layer:
                continue
            scanned += 1
            for record in list(layer.get("records") or []):
                record_id = str(record.get("id") or "")
                if not record_id:
                    continue
                kind = str(record.get("artifact_type") or record.get("kind") or "record")
                if artifact_types and kind not in artifact_types:
                    continue
                if record.get("tombstone") or record.get("operation") == "tombstone":
                    artifacts.pop(record_id, None)
                    continue
                artifacts[record_id] = EffectiveArtifact(
                    artifact_id=record_id,
                    artifact_type=kind,
                    content_hash=str(record.get("content_hash") or record.get("digest") or ""),
                    layer_id=layer_id,
                    layer_priority=priority,
                    metadata=dict(record),
                )
        return EffectiveView(
            head_name=head_name,
            head_generation=int(head.get("generation") or 0),
            layer_chain=chain,
            artifacts=artifacts,
            total_artifacts=len(artifacts),
            layers_scanned=scanned,
        )


def compute_effective_view_hash(view: EffectiveView) -> str:
    import hashlib
    import json

    payload = {
        "head": view.head_name,
        "generation": view.head_generation,
        "items": sorted((key, item.content_hash) for key, item in view.artifacts.items()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
