"""Build a ``chunks`` artifact layer for one ChangeSet from file contents.

The Worker receives the changed files' (already redacted) text and the ids
the effective view currently holds for those paths. It emits upsert records
for every chunk of an added or modified file and a tombstone for every prior
id that is not emitted again, so ``overlay_records`` over base + deltas
equals a full build of the new snapshot.

Record ids depend on path, symbol and occurrence only, never on the commit:
an unchanged chunk keeps its id across snapshots, and a full build and a
chain of deltas produce identical records.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable, Mapping

from worker.incremental_index.chunking import Chunk, chunk_file
from worker.incremental_index.snapshot_diff import FileChange

ARTIFACT_KIND = "chunks"
LAYER_SCHEMA = "codecompass.artifact_layer.v1"


def chunk_id(path: str, key: str, occurrence: int) -> str:
    return hashlib.sha256(f"{ARTIFACT_KIND}|{path}|{key}|{occurrence}".encode("utf-8")).hexdigest()[:32]


def chunk_records(path: str, text: str) -> list[dict[str, Any]]:
    """Upsert records for every chunk of one file; ids are stable per symbol."""
    file_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    seen: Counter[str] = Counter()
    records = []
    for chunk in chunk_file(path, text):
        key = f"{chunk.kind}:{chunk.symbol}" if chunk.symbol else f"{chunk.kind}:"
        occurrence = seen[key]
        seen[key] += 1
        records.append(_record(path, chunk, chunk_id(path, key, occurrence), file_sha))
    return records


def _record(path: str, chunk: Chunk, record_id: str, file_sha: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "artifact_type": ARTIFACT_KIND,
        "operation": "upsert",
        "tombstone": False,
        "path": path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "symbol": chunk.symbol,
        "kind": chunk.kind,
        "content": chunk.content,
        "content_hash": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
        "file_content_sha256": file_sha,
    }


def _tombstone(path: str, record_id: str) -> dict[str, Any]:
    return {"id": record_id, "artifact_type": ARTIFACT_KIND, "operation": "tombstone", "tombstone": True,
            "path": path}


class ChunkLayerBuilder:
    """Turns a ChangeSet plus file contents into one ``chunks`` layer."""

    def records(
        self,
        changes: Iterable[FileChange],
        content: Mapping[str, str],
        prior_ids_by_path: Mapping[str, Iterable[str]] | None = None,
    ) -> list[dict[str, Any]]:
        prior = {path: list(ids) for path, ids in (prior_ids_by_path or {}).items()}
        rows: list[dict[str, Any]] = []
        for change in changes:
            removed = [change.path] if change.operation in ("delete", "rename") else []
            target = change.new_path or change.path
            emitted: list[dict[str, Any]] = []
            if change.operation != "delete":
                text = content.get(target)
                if text is None:
                    raise ValueError(f"chunk_content_missing:{target}")
                emitted = chunk_records(target, text)
                removed.append(target)
            emitted_ids = {row["id"] for row in emitted}
            for path in removed:
                rows.extend(_tombstone(path, rid) for rid in prior.get(path, []) if rid not in emitted_ids)
            rows.extend(emitted)
        return rows

    def build(
        self,
        *,
        changes: Iterable[FileChange],
        content: Mapping[str, str],
        prior_ids_by_path: Mapping[str, Iterable[str]] | None,
        parent_layer_id: str | None,
        snapshot_revision: str,
        changeset_id: str,
        compatibility_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A base layer without ``parent_layer_id``, otherwise a delta on top of it."""
        records = self.records(changes, content, prior_ids_by_path)
        layer = {
            "schema": LAYER_SCHEMA,
            "layer_kind": "delta" if parent_layer_id else "base",
            "artifact_kind": ARTIFACT_KIND,
            "compatibility_key": dict(compatibility_key or {}),
            "source_revision": snapshot_revision,
            "snapshot_revision": snapshot_revision,
            "changeset_id": changeset_id,
            "parent_layer_id": parent_layer_id or None,
            "record_count": sum(1 for row in records if not row["tombstone"]),
            "tombstone_count": sum(1 for row in records if row["tombstone"]),
            "coverage": {"paths": sorted({row["path"] for row in records})},
            "build_status": "verified",
            "records": records,
        }
        layer["content_digest"] = hashlib.sha256(
            json.dumps(layer, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return layer
