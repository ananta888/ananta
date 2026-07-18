"""File-system scan and pre-scan helpers for project processing (SPLIT-033).

Extracted from project_processor.py to keep the orchestration module
focused on pipeline control. This module owns the responsibilities
documented in the SPLIT-033 plan:

  - Dateisystem-Scan, Gitignore-Handling, Datei-Filterung
  - Snapshot-Erstellung (rel_path, ext, text, size, sha1)
  - Pre-Scan fuer Java-/C#-Packages und Namespaces
  - Cache-Reusability-Pruefung (sha1 + options_signature)

No state lives here - every entry point is a pure function or a
frozen dataclass, so the project_processor can call into it without
side effects beyond the returned values.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rag_helper.extractors.base import JavaLikeExtractor
from rag_helper.filesystem.file_filters import (
    effective_extension,
    exclude_gitignored_files,
    should_include_file,
)
from rag_helper.filesystem.text_reader import read_text_file
from rag_helper.utils.ids import sha1_text


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    rel_path: str
    ext: str
    text: str | None
    size: int
    sha1: str | None


@dataclass(frozen=True)
class SourceFingerprint:
    digest: str
    file_count: int


def collect_files(
    root: Path,
    extensions: set[str],
    excludes: set[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    file_inclusion_predicate: Callable[[Path, str], bool] | None = None,
) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        try:
            if p.is_symlink() or not p.is_file():
                continue
        except OSError:
            continue
        if should_include_file(
            path=p,
            root=root,
            extensions=extensions,
            excluded_parts=excludes,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        ):
            relative_path = p.relative_to(root).as_posix()
            if file_inclusion_predicate is None or file_inclusion_predicate(p, relative_path):
                files.append(p)
    return sorted(exclude_gitignored_files(root, files))


def build_source_fingerprint(
    *,
    root: Path,
    extensions: set[str],
    excludes: set[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    file_inclusion_predicate: Callable[[Path, str], bool] | None = None,
    max_file_bytes: int | None = None,
) -> SourceFingerprint:
    """Hash the safe scan set without parsing or executing repository files."""

    files = collect_files(
        root=root,
        extensions=extensions,
        excludes=excludes,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        file_inclusion_predicate=file_inclusion_predicate,
    )
    digest = hashlib.sha256()
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        try:
            size = path.stat().st_size
            digest.update(str(size).encode("ascii"))
            digest.update(b"\0")
            if max_file_bytes is not None and size > max_file_bytes:
                digest.update(b"oversized\0")
                continue
            with path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
        except OSError:
            digest.update(b"unreadable")
        digest.update(b"\0")
    return SourceFingerprint(digest=digest.hexdigest(), file_count=len(files))


def build_file_snapshots(
    files: list[Path],
    root: Path,
    *,
    max_file_size_kb: int | None = None,
    max_file_size_bytes: int | None = None,
) -> list[FileSnapshot]:
    snapshots: list[FileSnapshot] = []
    ceilings = [
        value
        for value in (
            max_file_size_kb * 1024 if max_file_size_kb is not None else None,
            max_file_size_bytes,
        )
        if value is not None
    ]
    max_bytes = min(ceilings) if ceilings else None
    for path in files:
        rel_path = str(path.relative_to(root))
        ext = effective_extension(path)
        size = path.stat().st_size
        text = read_text_file(path, max_bytes=max_bytes)
        snapshots.append(
            FileSnapshot(
                path=path,
                rel_path=rel_path,
                ext=ext,
                text=text,
                size=size,
                sha1=sha1_text(text) if text is not None else None,
            )
        )
    return snapshots


def is_cache_entry_reusable(
    entry: dict | None, sha1: str | None, options_signature: str
) -> bool:
    return (
        bool(entry)
        and entry.get("sha1") == sha1
        and entry.get("options_signature") == options_signature
    )


def build_package_type_index(
    snapshots: Iterable[FileSnapshot],
    java_extractor: JavaLikeExtractor,
    csharp_extractor,
    reusable_cache_entries: dict[str, dict],
    cache_entries_out: dict[str, dict],
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[dict]]:
    known_package_types: dict[str, set[str]] = defaultdict(set)
    known_namespace_types: dict[str, set[str]] = defaultdict(set)
    scan_errors: list[dict] = []
    for snapshot in snapshots:
        if snapshot.ext not in {"java", "cs"}:
            continue
        if snapshot.text is None:
            continue
        cached_entry = reusable_cache_entries.get(snapshot.rel_path)
        cached_pre_scan = cached_entry.get("pre_scan") if cached_entry else None
        if cached_pre_scan:
            scan = cached_pre_scan
        else:
            try:
                scan = (
                    java_extractor.pre_scan_types(snapshot.rel_path, snapshot.text)
                    if snapshot.ext == "java"
                    else csharp_extractor.pre_scan_types(
                        snapshot.rel_path, snapshot.text
                    )
                )
            except Exception as exc:
                scan_errors.append(
                    {
                        "file": snapshot.rel_path,
                        "ext": snapshot.ext,
                        "stage": "pre_scan",
                        "error": str(exc),
                    }
                )
                continue
        try:
            if scan.get("package"):
                for tn in scan["type_names"]:
                    known_package_types[scan["package"]].add(tn)
            if scan.get("namespace"):
                for tn in scan["type_names"]:
                    known_namespace_types[scan["namespace"]].add(tn)
            cache_entry = cache_entries_out.setdefault(snapshot.rel_path, {})
            cache_entry["pre_scan"] = scan
        except Exception as exc:
            scan_errors.append(
                {
                    "file": snapshot.rel_path,
                    "ext": snapshot.ext,
                    "stage": "pre_scan",
                    "error": str(exc),
                }
            )
    return dict(known_package_types), dict(known_namespace_types), scan_errors
