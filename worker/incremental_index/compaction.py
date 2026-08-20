"""Compaction planning and lossless merge of layer chains."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from worker.incremental_index.effective_view import overlay_records


@dataclass
class CompactionCandidate:
    layer_ids: list[str]
    total_size_bytes: int
    fragment_count: int
    estimated_savings_bytes: int
    priority_score: float


@dataclass
class CompactionPlan:
    plan_id: str
    created_at: str
    profile_name: str
    candidates: list[CompactionCandidate]
    total_estimated_savings_bytes: int
    strategy: str

    def to_dict(self) -> dict[str, Any]:
        compacted = {
            "schema": "codecompass.compaction_plan.v1",
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "profile_name": self.profile_name,
            "candidates": [
                {
                    "layer_ids": item.layer_ids,
                    "total_size_bytes": item.total_size_bytes,
                    "fragment_count": item.fragment_count,
                    "estimated_savings_bytes": item.estimated_savings_bytes,
                    "priority_score": item.priority_score,
                }
                for item in self.candidates
            ],
            "total_estimated_savings_bytes": self.total_estimated_savings_bytes,
            "strategy": self.strategy,
        }


class CompactionPlanner:
    def __init__(self, layer_store_path: str | None = None) -> None:
        self.layer_store_path = layer_store_path

    def create_plan(self, profile_name: str, strategy: str = "balanced", delta_ids: list[str] | None = None) -> CompactionPlan:
        layers = list(delta_ids or [])
        candidate = CompactionCandidate(
            layer_ids=layers,
            total_size_bytes=len(layers) * 1024,
            fragment_count=len(layers),
            estimated_savings_bytes=max(0, (len(layers) - 1) * 256),
            priority_score=float(len(layers)),
        )
        plan_id = hashlib.sha256(f"{profile_name}:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16]
        return CompactionPlan(
            plan_id=plan_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            profile_name=profile_name,
            candidates=[candidate] if len(layers) > 1 else [],
            total_estimated_savings_bytes=candidate.estimated_savings_bytes if len(layers) > 1 else 0,
            strategy=strategy,
        )

    def compact_layers(self, layers: list[dict[str, Any]]) -> dict[str, Any]:
        records = overlay_records(*[list(layer.get("records") or []) for layer in layers])
        parent = None
        snapshot = ""
        if layers:
            parent = layers[0].get("parent_layer_id")
            snapshot = str(layers[-1].get("snapshot_revision") or layers[0].get("snapshot_revision") or "")
        return {
            "schema": "codecompass.artifact_layer.v1",
            "layer_kind": "base",
            "artifact_kind": str((layers[-1] if layers else {}).get("artifact_kind") or "records"),
            "parent_layer_id": parent,
            "snapshot_revision": snapshot,
            "source_revision": snapshot,
            "records": records,
            "record_count": len(records),
            "tombstone_count": 0,
            "build_status": "verified",
        }
        compacted["content_digest"] = hashlib.sha256(
            json.dumps(compacted, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return compacted
