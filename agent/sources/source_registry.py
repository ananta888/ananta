from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.config import settings

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "sources" / "source_descriptor.v1.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def validate_source_descriptor_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


class SourceRegistry:
    def __init__(self, *, root: Path | None = None) -> None:
        base = Path(root or settings.data_dir).expanduser().resolve()
        self._root = base / "sources"
        self._source_dir = self._root / "descriptors"
        self._source_dir.mkdir(parents=True, exist_ok=True)

    @property
    def source_dir(self) -> Path:
        return self._source_dir

    def _path_for(self, source_id: str) -> Path:
        return self._source_dir / f"{source_id}.json"

    def _read_descriptor(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def create_source(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        source_id = str(descriptor.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("source_id_required")
        existing = self._path_for(source_id)
        if existing.exists():
            raise ValueError("source_id_already_exists")
        return self.update_source(source_id=source_id, descriptor=descriptor, allow_create=True)

    def update_source(self, *, source_id: str, descriptor: dict[str, Any], allow_create: bool = False) -> dict[str, Any]:
        normalized_id = str(source_id or "").strip()
        if not normalized_id:
            raise ValueError("source_id_required")
        payload = dict(descriptor)
        payload["source_id"] = normalized_id
        if "schema" not in payload:
            payload["schema"] = "source_descriptor.v1"
        payload.setdefault("enabled", True)
        payload.setdefault("extensions", {})
        payload["extensions"] = dict(payload.get("extensions") or {})
        payload["extensions"]["descriptor_hash"] = _sha256_json(payload)
        payload["extensions"]["updated_at"] = _now_iso()
        errors = validate_source_descriptor_payload(payload)
        if errors:
            raise ValueError(f"invalid_source_descriptor:{'; '.join(errors)}")
        path = self._path_for(normalized_id)
        if not allow_create and not path.exists():
            raise ValueError("source_not_found")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def disable_source(self, source_id: str) -> dict[str, Any]:
        descriptor = self.get_source(source_id)
        if descriptor is None:
            raise ValueError("source_not_found")
        descriptor["enabled"] = False
        return self.update_source(source_id=source_id, descriptor=descriptor, allow_create=False)

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        path = self._path_for(str(source_id or "").strip())
        if not path.exists():
            return None
        payload = self._read_descriptor(path)
        if not isinstance(payload, dict):
            return None
        return payload

    def list_sources(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self._source_dir.glob("*.json")):
            try:
                payload = self._read_descriptor(path)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if not include_disabled and not bool(payload.get("enabled", True)):
                continue
            items.append(payload)
        return items

