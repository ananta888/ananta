from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.config import settings

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "sources" / "source_descriptor.v1.json"
SOURCE_PACK_SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "sources" / "source_pack.v1.json"
SOURCE_PACKS_DIR = Path(__file__).resolve().parents[2] / "sources" / "source-packs"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _load_source_pack_schema() -> dict[str, Any]:
    return json.loads(SOURCE_PACK_SCHEMA_FILE.read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def validate_source_descriptor_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def validate_source_pack_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_source_pack_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


class SourceRegistry:
    def __init__(self, *, root: Path | None = None) -> None:
        base = Path(root or settings.data_dir).expanduser().resolve()
        self._base = base
        self._root = base / "sources"
        self._source_dir = self._root / "descriptors"
        self._source_pack_dir = self._root / "source-packs"
        self._source_dir.mkdir(parents=True, exist_ok=True)
        self._source_pack_dir.mkdir(parents=True, exist_ok=True)

    @property
    def source_dir(self) -> Path:
        return self._source_dir

    @property
    def source_pack_dir(self) -> Path:
        return self._source_pack_dir

    def _path_for(self, source_id: str) -> Path:
        return self._source_dir / f"{source_id}.json"

    def _read_descriptor(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _source_pack_path_for(self, source_pack_id: str) -> Path:
        return self._source_pack_dir / f"{source_pack_id}.source-pack.json"

    def _builtin_source_pack_path_for(self, source_pack_id: str) -> Path:
        return SOURCE_PACKS_DIR / f"{source_pack_id}.source-pack.json"

    def _read_source_pack(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _resolve_descriptor_path(self, descriptor_path: str) -> Path:
        candidate = Path(str(descriptor_path or "").strip())
        if candidate.is_absolute() and candidate.exists():
            return candidate
        repo_candidate = (Path(__file__).resolve().parents[2] / candidate).resolve()
        if repo_candidate.exists():
            return repo_candidate
        data_candidate = (self._base / candidate).resolve()
        if data_candidate.exists():
            return data_candidate
        raise ValueError(f"descriptor_path_not_found:{descriptor_path}")

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

    def create_source_pack(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_pack_id = str(payload.get("source_pack_id") or "").strip()
        if not source_pack_id:
            raise ValueError("source_pack_id_required")
        path = self._source_pack_path_for(source_pack_id)
        if path.exists():
            raise ValueError("source_pack_id_already_exists")
        return self.update_source_pack(source_pack_id=source_pack_id, payload=payload, allow_create=True)

    def update_source_pack(self, *, source_pack_id: str, payload: dict[str, Any], allow_create: bool = False) -> dict[str, Any]:
        normalized_id = str(source_pack_id or "").strip()
        if not normalized_id:
            raise ValueError("source_pack_id_required")
        pack = dict(payload or {})
        pack["source_pack_id"] = normalized_id
        pack.setdefault("schema", "source_pack.v1")
        pack.setdefault("enabled", True)
        errors = validate_source_pack_payload(pack)
        if errors:
            raise ValueError(f"invalid_source_pack:{'; '.join(errors)}")
        path = self._source_pack_path_for(normalized_id)
        if not allow_create and not path.exists():
            raise ValueError("source_pack_not_found")
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return pack

    def get_source_pack(self, source_pack_id: str) -> dict[str, Any] | None:
        normalized_id = str(source_pack_id or "").strip()
        if not normalized_id:
            return None
        for path in (self._source_pack_path_for(normalized_id), self._builtin_source_pack_path_for(normalized_id)):
            if not path.exists():
                continue
            payload = self._read_source_pack(path)
            if isinstance(payload, dict):
                return payload
        return None

    def list_source_packs(self) -> list[dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for directory in (SOURCE_PACKS_DIR, self._source_pack_dir):
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.source-pack.json")):
                try:
                    payload = self._read_source_pack(path)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                source_pack_id = str(payload.get("source_pack_id") or "").strip()
                if not source_pack_id:
                    continue
                indexed[source_pack_id] = payload
        return [indexed[key] for key in sorted(indexed.keys())]

    def register_source_pack(self, *, source_pack_id: str, overwrite_existing: bool = False) -> dict[str, Any]:
        pack = self.get_source_pack(source_pack_id)
        if pack is None:
            raise ValueError("source_pack_not_found")
        errors = validate_source_pack_payload(pack)
        if errors:
            raise ValueError(f"invalid_source_pack:{'; '.join(errors)}")
        seen_source_ids: set[str] = set()
        registered_source_ids: list[str] = []
        for item in list(pack.get("sources") or []):
            row = dict(item) if isinstance(item, dict) else {}
            source_id = str(row.get("source_id") or "").strip()
            if not source_id:
                continue
            if source_id in seen_source_ids:
                raise ValueError(f"duplicate_source_id_in_pack:{source_id}")
            seen_source_ids.add(source_id)
            descriptor_path = str(row.get("descriptor_path") or "").strip()
            descriptor = self._read_descriptor(self._resolve_descriptor_path(descriptor_path))
            if str(descriptor.get("source_id") or "").strip() != source_id:
                raise ValueError(f"source_id_mismatch:{source_id}")
            existing = self.get_source(source_id)
            if existing is not None and not overwrite_existing:
                raise ValueError(f"duplicate_source_id:{source_id}")
            descriptor["enabled"] = bool(row.get("enabled", True))
            extensions = dict(descriptor.get("extensions") or {})
            extensions["source_pack_id"] = str(pack.get("source_pack_id") or "")
            extensions["source_pack_version"] = str(pack.get("version") or "")
            extensions["activated_from_source_pack"] = True
            descriptor["extensions"] = extensions
            self.update_source(source_id=source_id, descriptor=descriptor, allow_create=True)
            registered_source_ids.append(source_id)
        return {
            "source_pack_id": str(pack.get("source_pack_id") or ""),
            "registered_source_ids": registered_source_ids,
            "count": len(registered_source_ids),
        }
