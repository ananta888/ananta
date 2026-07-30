from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import fcntl
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.config import settings

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_SOURCE_ROOT = _REPOSITORY_ROOT / "sources"
SCHEMA_FILE = _REPOSITORY_ROOT / "schemas" / "sources" / "source_descriptor.v1.json"
SOURCE_PACK_SCHEMA_FILE = _REPOSITORY_ROOT / "schemas" / "sources" / "source_pack.v1.json"
SOURCE_PACKS_DIR = _REPOSITORY_SOURCE_ROOT / "source-packs"
_REGISTRY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,120}$")
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_MUTATIONS = frozenset(
    {
        "create_source",
        "update_source",
        "disable_source",
        "create_source_pack",
        "update_source_pack",
        "register_source_pack",
        "register_source_pack_with_options",
    }
)


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _load_source_pack_schema() -> dict[str, Any]:
    return json.loads(SOURCE_PACK_SCHEMA_FILE.read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _canonical_registry_id(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field}_required")
    if _REGISTRY_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field}_invalid")
    return normalized


def _shared_registry_lock(root: Path) -> threading.RLock:
    key = str(root)
    with _REGISTRY_LOCKS_GUARD:
        lock = _REGISTRY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REGISTRY_LOCKS[key] = lock
        return lock


def _registry_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            with self._registry_file_lock(
                create_storage=method.__name__ in _REGISTRY_MUTATIONS
            ):
                return method(self, *args, **kwargs)

    return locked


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
        self._lock = _shared_registry_lock(self._root)
        self._file_lock_state = threading.local()

    @property
    def source_dir(self) -> Path:
        return self._source_dir

    @property
    def source_pack_dir(self) -> Path:
        return self._source_pack_dir

    def _path_for(self, source_id: str) -> Path:
        normalized_id = _canonical_registry_id(source_id, field="source_id")
        return self._safe_path(
            self._source_dir / f"{normalized_id}.json",
            allowed_root=self._source_dir,
            anchor=self._base,
        )

    def _read_descriptor(self, path: Path) -> dict[str, Any]:
        return self._read_json(
            self._existing_file(
                path,
                roots=(
                    (self._root, self._base),
                    (_REPOSITORY_SOURCE_ROOT, _REPOSITORY_ROOT),
                ),
            )
        )

    def _source_pack_path_for(self, source_pack_id: str) -> Path:
        normalized_id = _canonical_registry_id(source_pack_id, field="source_pack_id")
        return self._safe_path(
            self._source_pack_dir / f"{normalized_id}.source-pack.json",
            allowed_root=self._source_pack_dir,
            anchor=self._base,
        )

    def _builtin_source_pack_path_for(self, source_pack_id: str) -> Path:
        normalized_id = _canonical_registry_id(source_pack_id, field="source_pack_id")
        return self._safe_path(
            SOURCE_PACKS_DIR / f"{normalized_id}.source-pack.json",
            allowed_root=SOURCE_PACKS_DIR,
            anchor=_REPOSITORY_ROOT,
        )

    def _read_source_pack(self, path: Path) -> dict[str, Any]:
        return self._read_json(
            self._existing_file(
                path,
                roots=(
                    (self._source_pack_dir, self._base),
                    (SOURCE_PACKS_DIR, _REPOSITORY_ROOT),
                ),
            )
        )

    def _resolve_descriptor_path(self, descriptor_path: str) -> Path:
        raw_path = str(descriptor_path or "").strip()
        if not raw_path:
            raise ValueError("descriptor_path_not_found:")
        candidate = Path(raw_path)
        candidates = (
            (candidate,)
            if candidate.is_absolute()
            else (
                _REPOSITORY_ROOT / candidate,
                self._base / candidate,
            )
        )
        rejected: ValueError | None = None
        for resolved_candidate in candidates:
            if not resolved_candidate.exists() and not resolved_candidate.is_symlink():
                continue
            try:
                return self._existing_file(
                    resolved_candidate,
                    roots=(
                        (_REPOSITORY_SOURCE_ROOT, _REPOSITORY_ROOT),
                        (self._root, self._base),
                    ),
                )
            except ValueError as exc:
                rejected = exc
        if rejected is not None:
            raise rejected
        raise ValueError(f"descriptor_path_not_found:{descriptor_path}")

    @staticmethod
    def _safe_path(candidate: Path, *, allowed_root: Path, anchor: Path) -> Path:
        anchor_path = Path(os.path.abspath(str(anchor)))
        root_path = Path(os.path.abspath(str(allowed_root)))
        candidate_path = Path(os.path.abspath(str(candidate)))
        try:
            root_path.relative_to(anchor_path)
            candidate_path.relative_to(root_path)
        except ValueError as exc:
            raise ValueError("registry_path_outside_root") from exc

        current = anchor_path
        if current.is_symlink():
            raise ValueError("registry_symlink_not_allowed")
        for segment in candidate_path.relative_to(anchor_path).parts:
            current = current / segment
            if current.is_symlink():
                raise ValueError("registry_symlink_not_allowed")

        try:
            candidate_path.resolve(strict=False).relative_to(
                root_path.resolve(strict=False)
            )
        except ValueError as exc:
            raise ValueError("registry_path_outside_root") from exc
        return candidate_path

    @classmethod
    def _existing_file(
        cls,
        candidate: Path,
        *,
        roots: tuple[tuple[Path, Path], ...],
    ) -> Path:
        outside_error: ValueError | None = None
        for allowed_root, anchor in roots:
            try:
                safe_path = cls._safe_path(
                    candidate,
                    allowed_root=allowed_root,
                    anchor=anchor,
                )
            except ValueError as exc:
                if str(exc) == "registry_symlink_not_allowed":
                    raise
                outside_error = exc
                continue
            if not safe_path.is_file() or safe_path.is_symlink():
                raise ValueError("registry_file_invalid")
            return safe_path
        raise outside_error or ValueError("registry_path_outside_root")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("registry_file_invalid")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                payload = json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(payload, dict):
            raise ValueError("registry_payload_invalid")
        return payload

    def _ensure_storage_directory(self, directory: Path) -> Path:
        safe_directory = self._safe_path(
            directory,
            allowed_root=self._root,
            anchor=self._base,
        )
        safe_directory.mkdir(parents=True, exist_ok=True)
        safe_directory = self._safe_path(
            safe_directory,
            allowed_root=self._root,
            anchor=self._base,
        )
        if not safe_directory.is_dir() or safe_directory.is_symlink():
            raise ValueError("registry_directory_invalid")
        return safe_directory

    @contextmanager
    def _registry_file_lock(self, *, create_storage: bool):
        """Hold an OS lock across the complete read/CAS/write transaction."""

        depth = int(getattr(self._file_lock_state, "depth", 0))
        if depth:
            self._file_lock_state.depth = depth + 1
            try:
                yield
            finally:
                self._file_lock_state.depth -= 1
            return
        if not create_storage and not self._root.exists():
            yield
            return
        root = self._ensure_storage_directory(self._root)
        lock_path = self._safe_path(
            root / ".registry.lock",
            allowed_root=root,
            anchor=self._base,
        )
        if lock_path.is_symlink():
            raise ValueError("registry_lock_symlink_not_allowed")
        descriptor = os.open(
            lock_path,
            (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            ),
            0o600,
        )
        try:
            opened = os.fstat(descriptor)
            linked = os.lstat(lock_path)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino)
                != (linked.st_dev, linked.st_ino)
            ):
                raise ValueError("registry_lock_file_invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._file_lock_state.depth = 1
            self._fsync_directory(root)
            try:
                yield
            finally:
                self._file_lock_state.depth = 0
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        parent = self._ensure_storage_directory(path.parent)
        safe_path = self._safe_path(path, allowed_root=parent, anchor=self._base)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{safe_path.name}.",
            suffix=".tmp",
            dir=parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = -1
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, safe_path)
            temporary_name = ""
            self._fsync_directory(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @_registry_locked
    def create_source(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        source_id = _canonical_registry_id(
            str(descriptor.get("source_id") or ""),
            field="source_id",
        )
        existing = self._path_for(source_id)
        if existing.exists():
            raise ValueError("source_id_already_exists")
        return self.update_source(source_id=source_id, descriptor=descriptor, allow_create=True)

    @_registry_locked
    def update_source(self, *, source_id: str, descriptor: dict[str, Any], allow_create: bool = False) -> dict[str, Any]:
        normalized_id = _canonical_registry_id(source_id, field="source_id")
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
        self._atomic_write_json(path, payload)
        return payload

    @_registry_locked
    def disable_source(self, source_id: str) -> dict[str, Any]:
        descriptor = self.get_source(source_id)
        if descriptor is None:
            raise ValueError("source_not_found")
        descriptor["enabled"] = False
        return self.update_source(source_id=source_id, descriptor=descriptor, allow_create=False)

    @_registry_locked
    def get_source(self, source_id: str) -> dict[str, Any] | None:
        try:
            normalized_id = _canonical_registry_id(source_id, field="source_id")
            path = self._path_for(normalized_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            payload = self._read_descriptor(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None
        if str(payload.get("source_id") or "") != normalized_id:
            return None
        if validate_source_descriptor_payload(payload):
            return None
        return payload

    @_registry_locked
    def list_sources(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        if not self._source_dir.is_dir() or self._source_dir.is_symlink():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self._source_dir.glob("*.json")):
            try:
                source_id = _canonical_registry_id(path.stem, field="source_id")
                if path != self._path_for(source_id):
                    continue
                payload = self._read_descriptor(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
            if str(payload.get("source_id") or "") != source_id:
                continue
            if validate_source_descriptor_payload(payload):
                continue
            if not include_disabled and not bool(payload.get("enabled", True)):
                continue
            items.append(payload)
        return items

    @_registry_locked
    def create_source_pack(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_pack_id = _canonical_registry_id(
            str(payload.get("source_pack_id") or ""),
            field="source_pack_id",
        )
        path = self._source_pack_path_for(source_pack_id)
        if path.exists():
            raise ValueError("source_pack_id_already_exists")
        return self.update_source_pack(source_pack_id=source_pack_id, payload=payload, allow_create=True)

    @_registry_locked
    def update_source_pack(self, *, source_pack_id: str, payload: dict[str, Any], allow_create: bool = False) -> dict[str, Any]:
        normalized_id = _canonical_registry_id(source_pack_id, field="source_pack_id")
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
        self._atomic_write_json(path, pack)
        return pack

    @_registry_locked
    def get_source_pack(self, source_pack_id: str) -> dict[str, Any] | None:
        try:
            normalized_id = _canonical_registry_id(
                source_pack_id,
                field="source_pack_id",
            )
        except ValueError:
            return None
        for resolver in (
            self._source_pack_path_for,
            self._builtin_source_pack_path_for,
        ):
            try:
                path = resolver(normalized_id)
            except ValueError:
                continue
            if not path.is_file():
                continue
            try:
                payload = self._read_source_pack(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
            if str(payload.get("source_pack_id") or "") != normalized_id:
                continue
            if validate_source_pack_payload(payload):
                continue
            return payload
        return None

    @_registry_locked
    def list_source_packs(self) -> list[dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for directory in (SOURCE_PACKS_DIR, self._source_pack_dir):
            if not directory.is_dir() or directory.is_symlink():
                continue
            for path in sorted(directory.glob("*.source-pack.json")):
                try:
                    payload = self._read_source_pack(path)
                    source_pack_id = _canonical_registry_id(
                        str(payload.get("source_pack_id") or ""),
                        field="source_pack_id",
                    )
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    continue
                if path.name != f"{source_pack_id}.source-pack.json":
                    continue
                if validate_source_pack_payload(payload):
                    continue
                indexed[source_pack_id] = payload
        return [indexed[key] for key in sorted(indexed.keys())]

    @_registry_locked
    def register_source_pack(self, *, source_pack_id: str, overwrite_existing: bool = False) -> dict[str, Any]:
        return self.register_source_pack_with_options(
            source_pack_id=source_pack_id,
            overwrite_existing=overwrite_existing,
            include_optional=False,
        )

    @_registry_locked
    def register_source_pack_with_options(
        self,
        *,
        source_pack_id: str,
        overwrite_existing: bool = False,
        include_optional: bool = False,
    ) -> dict[str, Any]:
        normalized_pack_id = _canonical_registry_id(
            source_pack_id,
            field="source_pack_id",
        )
        pack = self.get_source_pack(normalized_pack_id)
        if pack is None:
            raise ValueError("source_pack_not_found")
        errors = validate_source_pack_payload(pack)
        if errors:
            raise ValueError(f"invalid_source_pack:{'; '.join(errors)}")
        seen_source_ids: set[str] = set()
        registered_source_ids: list[str] = []
        for item in list(pack.get("sources") or []):
            row = dict(item) if isinstance(item, dict) else {}
            source_id = _canonical_registry_id(
                str(row.get("source_id") or ""),
                field="source_id",
            )
            is_optional = bool(row.get("optional", False))
            if is_optional and not include_optional:
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

    @_registry_locked
    def rank_sources_for_query(self, *, source_pack_id: str, source_ids: list[str], query: str) -> list[str]:
        pack = self.get_source_pack(source_pack_id)
        if pack is None:
            raise ValueError("source_pack_not_found")
        rows = [dict(item) for item in list(pack.get("sources") or []) if isinstance(item, dict)]
        index = {str(item.get("source_id") or ""): item for item in rows}
        query_text = str(query or "").strip().lower()
        is_keycloak_question = any(token in query_text for token in ("oidc", "keycloak", "realm", "token", "client", "authorization"))
        is_eclipse_question = any(token in query_text for token in ("eclipse", "swt", "jdt", "pde", "plugin", "osgi", "equinox"))

        def _score(sid: str) -> int:
            row = index.get(str(sid) or "")
            if not isinstance(row, dict):
                return 0
            base = int(row.get("source_priority") or 0)
            trust = str(row.get("trust_level") or "").lower()
            if "official" in trust:
                base += 20
            if is_keycloak_question and str(row.get("source_id") or "") == "keycloak-official-docs":
                base += 200
            if is_eclipse_question and str(row.get("source_id") or "").startswith("eclipse-"):
                base += 120
            if str(row.get("source_id") or "") == "wikimedia-wikipedia-initial-dump" and (is_keycloak_question or is_eclipse_question):
                base -= 120
            return base

        ranked = sorted({str(item).strip() for item in list(source_ids or []) if str(item).strip()}, key=lambda sid: (-_score(sid), sid))
        return ranked
