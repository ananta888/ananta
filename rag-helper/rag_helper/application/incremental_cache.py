from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

CACHE_VERSION = 2
SHARD_DIR_SUFFIX = ".d"
MANIFEST_NAME = "manifest.json"


def load_incremental_cache(path: Path) -> dict:
    shard_dir = _shard_dir(path)
    if shard_dir.exists():
        return _load_sharded_cache(path, shard_dir)
    if not path.exists():
        return {"version": CACHE_VERSION, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": CACHE_VERSION, "files": {}}
    if isinstance(data, dict):
        data.setdefault("version", 1)
        data.setdefault("files", {})
        return data
    return {"version": CACHE_VERSION, "files": {}}


def save_incremental_cache(
    path: Path,
    cache: dict,
    *,
    changed_extensions: set[str] | None = None,
) -> None:
    shard_dir = _shard_dir(path)
    shard_dir.mkdir(parents=True, exist_ok=True)
    grouped_files = _group_files_by_extension(cache.get("files", {}))
    active_extensions = sorted(grouped_files)
    manifest = {
        "version": CACHE_VERSION,
        "options_signature": cache.get("options_signature"),
        "shard_mode": "by_extension",
        "shard_dir": shard_dir.name,
        "extensions": {ext: len(entries) for ext, entries in grouped_files.items()},
    }
    _write_json_atomically(path, manifest)
    _write_json_atomically(shard_dir / MANIFEST_NAME, manifest)

    target_extensions = set(active_extensions if changed_extensions is None else changed_extensions)
    for ext in target_extensions:
        shard_payload = {
            "version": CACHE_VERSION,
            "options_signature": cache.get("options_signature"),
            "extension": ext,
            "files": grouped_files.get(ext, {}),
        }
        _write_json_atomically(shard_dir / _shard_filename(ext), shard_payload)


def invalidate_incremental_cache_paths(
    path: Path,
    paths: Iterable[str] | None,
) -> dict[str, object]:
    """Remove selected cached files while retaining unrelated type shards."""

    cache = load_incremental_cache(path)
    files = cache.get("files") if isinstance(cache.get("files"), dict) else {}
    requested = (
        set(files)
        if paths is None
        else {str(value).replace("\\", "/") for value in paths}
    )
    invalidated_extensions: set[str] = set()
    invalidated_paths: list[str] = []
    for relative_path in sorted(requested & set(files)):
        entry = files.pop(relative_path)
        invalidated_extensions.add(_detect_extension(relative_path, entry))
        invalidated_paths.append(relative_path)
    if invalidated_paths:
        cache["files"] = files
        save_incremental_cache(
            path,
            cache,
            changed_extensions=invalidated_extensions,
        )
    return {
        "strategy": "targeted_file_type_invalidation",
        "requested_path_count": len(requested),
        "invalidated_path_count": len(invalidated_paths),
        "retained_path_count": len(files),
        "invalidated_extensions": sorted(invalidated_extensions),
    }


def _load_sharded_cache(path: Path, shard_dir: Path) -> dict:
    manifest_path = shard_dir / MANIFEST_NAME
    manifest = _safe_load_json(manifest_path)
    if not isinstance(manifest, dict):
        manifest = _safe_load_json(path)
    extensions = manifest.get("extensions", {}) if isinstance(manifest, dict) else {}
    files: dict[str, dict] = {}
    for ext in extensions:
        shard_payload = _safe_load_json(shard_dir / _shard_filename(ext))
        shard_files = shard_payload.get("files", {}) if isinstance(shard_payload, dict) else {}
        if isinstance(shard_files, dict):
            files.update(shard_files)
    return {
        "version": manifest.get("version", CACHE_VERSION) if isinstance(manifest, dict) else CACHE_VERSION,
        "options_signature": manifest.get("options_signature") if isinstance(manifest, dict) else None,
        "files": files,
    }


def _group_files_by_extension(files: dict[str, dict]) -> dict[str, dict[str, dict]]:
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for rel_path, entry in files.items():
        ext = _detect_extension(rel_path, entry)
        grouped[ext][rel_path] = entry
    return dict(grouped)


def _detect_extension(rel_path: str, entry: dict) -> str:
    ext = str(entry.get("manifest", {}).get("ext") or Path(rel_path).suffix.lower().lstrip(".")).strip()
    return ext or "_noext"


def _shard_dir(path: Path) -> Path:
    return path.parent / f"{path.name}{SHARD_DIR_SUFFIX}"


def _shard_filename(ext: str) -> str:
    return f"{ext}.json"


def _safe_load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
