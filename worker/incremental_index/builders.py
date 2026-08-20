"""Incremental builders that emit upsert/tombstone records for one ChangeSet."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from worker.incremental_index.snapshot_diff import FileChange


def _record_id(kind: str, path: str, extra: str = "") -> str:
    return hashlib.sha256(f"{kind}|{path}|{extra}".encode("utf-8")).hexdigest()[:24]


def records_from_changes(changes: list[FileChange], *, artifact_kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for change in changes:
        path = change.new_path or change.path
        if change.operation == "delete":
            rows.append(
                {
                    "id": _record_id(artifact_kind, change.path),
                    "path": change.path,
                    "artifact_type": artifact_kind,
                    "tombstone": True,
                    "operation": "tombstone",
                }
            )
            continue
        if change.operation == "rename":
            rows.append(
                {
                    "id": _record_id(artifact_kind, change.path),
                    "path": change.path,
                    "artifact_type": artifact_kind,
                    "tombstone": True,
                    "operation": "tombstone",
                }
            )
        rows.append(
            {
                "id": _record_id(artifact_kind, path),
                "path": path,
                "artifact_type": artifact_kind,
                "tombstone": False,
                "operation": "upsert",
                "content_hash": change.new_content_sha256 or "",
                "text": path,
            }
        )
    return rows


@dataclass
class IncrementalGraphBuildResult:
    changeset_id: str
    source_revision: str
    node_deltas: list[dict[str, Any]] = field(default_factory=list)
    edge_deltas: list[dict[str, Any]] = field(default_factory=list)
    files_parsed: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "source_revision": self.source_revision,
            "node_deltas": self.node_deltas,
            "edge_deltas": self.edge_deltas,
            "files_parsed": self.files_parsed,
            "files_skipped": self.files_skipped,
            "errors": self.errors,
            "stats": self.stats,
        }


class IncrementalGraphBuilder:
    def build_incremental(
        self,
        *,
        changeset_id: str,
        source_revision: str,
        changed_files: list[FileChange],
        skipped: list[str] | None = None,
    ) -> IncrementalGraphBuildResult:
        records = records_from_changes(changed_files, artifact_kind="graph")
        return IncrementalGraphBuildResult(
            changeset_id=changeset_id,
            source_revision=source_revision,
            node_deltas=records,
            files_parsed=[item.new_path or item.path for item in changed_files if item.operation != "delete"],
            files_skipped=list(skipped or []),
            stats={"nodes": len(records)},
        )


def build_artifact_layer(
    *,
    changeset_id: str,
    snapshot_revision: str,
    parent_layer_id: str | None,
    artifact_kind: str,
    changes: list[FileChange],
    compatibility_key: dict[str, Any] | None = None,
    force_base: bool = False,
) -> dict[str, Any]:
    records = records_from_changes(changes, artifact_kind=artifact_kind)
    layer = {
        "schema": "codecompass.artifact_layer.v1",
        "layer_kind": "base" if force_base or not parent_layer_id else "delta",
        "artifact_kind": artifact_kind,
        "compatibility_key": dict(compatibility_key or {}),
        "source_revision": snapshot_revision,
        "snapshot_revision": snapshot_revision,
        "changeset_id": changeset_id,
        "parent_layer_id": None if force_base else parent_layer_id,
        "record_count": sum(1 for item in records if not item.get("tombstone")),
        "tombstone_count": sum(1 for item in records if item.get("tombstone")),
        "coverage": {"paths": [item.get("path") for item in records]},
        "build_status": "verified",
        "records": records,
    }
    layer["content_digest"] = hashlib.sha256(
        json.dumps(layer, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return layer
