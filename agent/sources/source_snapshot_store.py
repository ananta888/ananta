from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.config import settings

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "sources" / "source_snapshot.v1.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_source_snapshot_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


class SourceSnapshotStore:
    def __init__(self, *, root: Path | None = None) -> None:
        base = Path(root or settings.data_dir).expanduser().resolve()
        self._root = base / "sources" / "snapshots"
        self._root.mkdir(parents=True, exist_ok=True)

    def _source_dir(self, source_id: str) -> Path:
        target = self._root / str(source_id or "").strip()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _path_for(self, *, source_id: str, snapshot_id: str) -> Path:
        return self._source_dir(source_id) / f"{snapshot_id}.json"

    def build_snapshot(
        self,
        *,
        source_id: str,
        descriptor_hash: str,
        content_payload: Any,
        metadata_payload: Any,
        status: str,
        reason_code: str | None = None,
        human_message: str | None = None,
        retrieved_at: str | None = None,
    ) -> dict[str, Any]:
        created_at = _now_iso()
        content_hash = _sha256_json(content_payload)
        metadata_hash = _sha256_json(metadata_payload)
        snapshot_id = f"snap_{hashlib.sha1(f'{source_id}:{created_at}:{content_hash}'.encode('utf-8')).hexdigest()[:16]}"
        snapshot = {
            "schema": "source_snapshot.v1",
            "snapshot_id": snapshot_id,
            "source_id": str(source_id),
            "created_at": created_at,
            "retrieved_at": str(retrieved_at or created_at),
            "content_hash": content_hash,
            "metadata_hash": metadata_hash,
            "descriptor_hash": str(descriptor_hash),
            "byte_size": len(json.dumps(content_payload, ensure_ascii=False).encode("utf-8")),
            "item_count": len(content_payload) if isinstance(content_payload, list) else 1,
            "status": str(status),
            "reason_code": str(reason_code or ""),
            "human_message": str(human_message or ""),
            "extensions": {},
        }
        errors = validate_source_snapshot_payload(snapshot)
        if errors:
            raise ValueError(f"invalid_source_snapshot:{'; '.join(errors)}")
        return snapshot

    def save_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        source_id = str(snapshot.get("source_id") or "").strip()
        snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
        if not source_id or not snapshot_id:
            raise ValueError("snapshot_missing_ids")
        errors = validate_source_snapshot_payload(snapshot)
        if errors:
            raise ValueError(f"invalid_source_snapshot:{'; '.join(errors)}")
        path = self._path_for(source_id=source_id, snapshot_id=snapshot_id)
        if path.exists():
            raise ValueError("snapshot_immutable")
        if str(snapshot.get("status") or "") == "indexed":
            incoming_hash = str(snapshot.get("content_hash") or "")
            for existing in self.list_snapshots(source_id=source_id):
                if str(existing.get("status") or "") != "indexed":
                    continue
                if str(existing.get("content_hash") or "") != incoming_hash:
                    continue
                snapshot = dict(snapshot)
                snapshot["status"] = "duplicate"
                snapshot["reason_code"] = "duplicate_content_hash"
                snapshot["human_message"] = "content hash already indexed"
                break
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return snapshot

    def list_snapshots(self, *, source_id: str) -> list[dict[str, Any]]:
        target = self._source_dir(source_id)
        items: list[dict[str, Any]] = []
        for path in sorted(target.glob("snap_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(payload)
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def latest_indexed_snapshot(self, *, source_id: str) -> dict[str, Any] | None:
        for item in self.list_snapshots(source_id=source_id):
            if str(item.get("status") or "") == "indexed":
                return item
        return None

    def mark_superseded(self, *, source_id: str, keep_snapshot_id: str) -> int:
        changed = 0
        for snapshot in self.list_snapshots(source_id=source_id):
            if str(snapshot.get("snapshot_id") or "") == str(keep_snapshot_id):
                continue
            if str(snapshot.get("status") or "") != "indexed":
                continue
            snapshot["status"] = "superseded"
            path = self._path_for(source_id=source_id, snapshot_id=str(snapshot["snapshot_id"]))
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed += 1
        return changed
