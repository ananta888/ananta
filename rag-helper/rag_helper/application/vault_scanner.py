"""Obsidian Vault Scanner & Manifest management (OBS-002)."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class VaultFile:
    rel_path: str
    abs_path: str
    ext: str
    mtime: float
    size_bytes: int
    sha256: str


@dataclass
class VaultDiff:
    new: list[VaultFile] = field(default_factory=list)
    changed: list[VaultFile] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[VaultFile] = field(default_factory=list)


SUPPORTED_EXTENSIONS = {".md", ".canvas"}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def scan(vault_profile) -> list[VaultFile]:
    """Scan a vault directory and return all processable files."""
    vault_root = Path(vault_profile.path)
    exclude_dirs = set(vault_profile.exclude_dirs or [])
    exclude_globs = list(vault_profile.exclude_glob_patterns or [])
    files: list[VaultFile] = []

    for dirpath, dirnames, filenames in os.walk(str(vault_root)):
        # Prune excluded dirs in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in exclude_dirs and not d.startswith(".")
        ]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            if ext == ".canvas" and not vault_profile.index_canvas_files:
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, str(vault_root)).replace("\\", "/")

            # Check glob excludes
            if any(fnmatch.fnmatch(rel_path, pat) for pat in exclude_globs):
                continue

            try:
                stat = os.stat(abs_path)
            except OSError:
                continue

            sha256 = _sha256_file(abs_path)
            files.append(VaultFile(
                rel_path=rel_path,
                abs_path=abs_path,
                ext=ext.lstrip("."),
                mtime=stat.st_mtime,
                size_bytes=stat.st_size,
                sha256=sha256,
            ))

    return sorted(files, key=lambda f: f.rel_path)


def load_manifest(path: Path) -> dict:
    """Load a VaultManifest JSON from disk. Returns empty dict on missing/corrupt."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_manifest(manifest: dict, path: Path) -> None:
    """Atomically save VaultManifest to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def compute_diff(files: list[VaultFile], manifest: dict) -> VaultDiff:
    """Compute which files are new, changed, deleted, or unchanged vs. the manifest."""
    manifest_keys = set(manifest.keys())
    file_map = {f.rel_path: f for f in files}
    current_keys = set(file_map.keys())

    diff = VaultDiff()
    diff.deleted = sorted(manifest_keys - current_keys)

    for rel_path, vf in file_map.items():
        if rel_path not in manifest:
            diff.new.append(vf)
        else:
            stored = manifest[rel_path]
            if stored.get("sha256") != vf.sha256:
                diff.changed.append(vf)
            else:
                diff.unchanged.append(vf)

    return diff


def update_manifest_entry(
    manifest: dict,
    vault_file: VaultFile,
    indexed_record_count: int = 0,
) -> dict:
    """Return an updated manifest entry for the given file."""
    manifest[vault_file.rel_path] = {
        "sha256": vault_file.sha256,
        "mtime": vault_file.mtime,
        "last_indexed_at": time.time(),
        "indexed_record_count": indexed_record_count,
    }
    return manifest
