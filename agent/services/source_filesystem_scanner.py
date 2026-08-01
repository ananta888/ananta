"""Fail-closed filesystem scanner for materialized registered workspaces."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceInventoryEvidence,
    SourceScanEvidence,
)
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    WorkspaceFileManifestEntry,
    WorkspaceInventoryManifest,
)


_ARCHIVE_TYPES = frozenset(
    {"zip", "tar", "tgz", "gz", "bz2", "xz", "7z", "rar"}
)
_TEXT_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,255}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token)\b"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=:-]{12,}"
    ),
)
_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore (?:all |any )?(?:previous|prior) instructions\b"),
    re.compile(r"(?i)\b(?:reveal|print|repeat) (?:the )?system prompt\b"),
    re.compile(r"(?i)<\|(?:system|developer|assistant)\|>"),
    re.compile(r"(?i)\byou are (?:chatgpt|an? ai assistant)\b"),
    re.compile(r"(?i)\bdo not follow (?:the )?(?:system|developer) message\b"),
)


class SourceFilesystemScanError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class SourceFilesystemScanResult:
    inventory: SourceInventoryEvidence
    scan: SourceScanEvidence


class ProductionFilesystemSourceScanner:
    """Produce revision-bound, content-free evidence from one exact snapshot."""

    scanner_id = "ananta-filesystem-source-scanner"
    scanner_version = "1.0"

    def scan(
        self,
        *,
        workspace: RegisteredWorkspace,
        snapshot: WorkspaceInventoryManifest,
        budgets: SourceAdmissionBudgets,
    ) -> SourceFilesystemScanResult:
        self._validate_workspace(workspace, snapshot)
        entries = tuple(sorted(snapshot.entries, key=lambda item: item.relative_path))
        self._validate_snapshot(snapshot, entries)
        target = self._target(workspace.root, snapshot.relative_root)

        file_types = Counter(entry.file_type for entry in entries)
        largest = max((entry.byte_size for entry in entries), default=0)
        symlinks = 0
        hardlinks = 0
        sparse_files = 0
        archives = 0
        binaries = 0
        secrets = 0
        injections = 0
        rejected_types = 0
        malformed_archives = 0
        scan_errors = 0
        expansion_ratio = 1.0

        expected_paths = {entry.relative_path for entry in entries}
        actual_paths, enumeration_errors, discovered_symlinks = self._actual_paths(
            target,
            maximum=max(budgets.max_files, len(entries)) + 1,
        )
        scan_errors += enumeration_errors
        symlinks += discovered_symlinks
        if actual_paths != expected_paths:
            scan_errors += 1

        for entry in entries:
            if budgets.allowed_file_types and entry.file_type not in budgets.allowed_file_types:
                rejected_types += 1
            path = self._entry_path(target, entry.relative_path)
            try:
                before = path.lstat()
            except OSError:
                scan_errors += 1
                continue
            if stat.S_ISLNK(before.st_mode):
                symlinks += 1
                scan_errors += 1
                continue
            if not stat.S_ISREG(before.st_mode):
                scan_errors += 1
                continue
            if before.st_nlink != 1:
                hardlinks += 1
                scan_errors += 1
                continue
            allocated = int(getattr(before, "st_blocks", 0)) * 512
            if before.st_size >= 4096 and allocated < before.st_size:
                sparse_files += 1
                scan_errors += 1
                continue
            if before.st_size != entry.byte_size:
                scan_errors += 1
                continue
            if before.st_size > budgets.max_file_bytes:
                scan_errors += 1
                continue
            try:
                content, digest = self._read_exact(path, before)
            except (OSError, SourceFilesystemScanError):
                scan_errors += 1
                continue
            if digest != entry.content_digest:
                scan_errors += 1
                continue

            is_archive = self._is_archive(entry, content)
            if is_archive:
                archives += 1
                ratio, malformed = self._archive_ratio(
                    content,
                    entry=entry,
                    budgets=budgets,
                )
                expansion_ratio = max(expansion_ratio, ratio)
                malformed_archives += int(malformed)
                continue
            if self._is_binary(content):
                binaries += 1
                continue
            try:
                text = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                binaries += 1
                continue
            secrets += sum(bool(pattern.search(text)) for pattern in _SECRET_PATTERNS)
            injections += sum(
                bool(pattern.search(text)) for pattern in _INJECTION_PATTERNS
            )

        inventory = SourceInventoryEvidence(
            revision_digest=snapshot.revision_digest,
            manifest_digest=snapshot.manifest_digest,
            file_count=len(entries),
            total_bytes=sum(entry.byte_size for entry in entries),
            largest_file_bytes=largest,
            archive_expansion_ratio=expansion_ratio,
            file_type_counts=dict(sorted(file_types.items())),
            symlink_count=symlinks,
            hardlink_count=hardlinks,
            sparse_file_count=sparse_files,
            archive_count=archives,
            binary_count=binaries,
        )
        scan = SourceScanEvidence(
            revision_digest=snapshot.revision_digest,
            manifest_digest=snapshot.manifest_digest,
            scanner_id=self.scanner_id,
            scanner_version=self.scanner_version,
            completed=scan_errors == 0,
            secret_findings=secrets,
            injection_findings=injections,
            rejected_type_findings=rejected_types,
            malformed_archive_findings=malformed_archives,
            scan_error_count=scan_errors,
        )
        return SourceFilesystemScanResult(inventory=inventory, scan=scan)

    @staticmethod
    def _validate_workspace(
        workspace: RegisteredWorkspace,
        snapshot: WorkspaceInventoryManifest,
    ) -> None:
        if workspace.workspace_id != snapshot.workspace_id:
            raise SourceFilesystemScanError("workspace_snapshot_mismatch")
        if not workspace.enabled:
            raise SourceFilesystemScanError("workspace_disabled")
        if not workspace.read_only:
            raise SourceFilesystemScanError("workspace_read_only_required")
        if workspace.root.is_symlink():
            raise SourceFilesystemScanError("workspace_root_symlink_forbidden")

    @staticmethod
    def _validate_snapshot(
        snapshot: WorkspaceInventoryManifest,
        entries: tuple[WorkspaceFileManifestEntry, ...],
    ) -> None:
        paths = [entry.relative_path for entry in entries]
        if len(paths) != len(set(paths)):
            raise SourceFilesystemScanError("snapshot_duplicate_path")
        payload = [
            {
                "relative_path": entry.relative_path,
                "byte_size": entry.byte_size,
                "content_digest": entry.content_digest,
                "file_type": entry.file_type,
            }
            for entry in entries
        ]
        manifest_digest = _digest(payload)
        revision_digest = _digest(
            {
                "workspace_id": snapshot.workspace_id,
                "relative_root": snapshot.relative_root,
                "manifest_digest": manifest_digest,
            }
        )
        if manifest_digest != snapshot.manifest_digest:
            raise SourceFilesystemScanError("snapshot_manifest_digest_mismatch")
        if revision_digest != snapshot.revision_digest:
            raise SourceFilesystemScanError("snapshot_revision_digest_mismatch")
        if snapshot.total_bytes != sum(entry.byte_size for entry in entries):
            raise SourceFilesystemScanError("snapshot_total_bytes_mismatch")

    @staticmethod
    def _target(root_value: Path, relative_root: str) -> Path:
        pure = PurePosixPath(str(relative_root or "."))
        if pure.is_absolute() or any(part in {"", ".."} for part in pure.parts):
            raise SourceFilesystemScanError("workspace_relative_path_invalid")
        parts = tuple(part for part in pure.parts if part != ".")
        root = root_value.resolve(strict=True)
        cursor = root
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise SourceFilesystemScanError("workspace_symlink_forbidden")
        target = cursor.resolve(strict=True)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise SourceFilesystemScanError("workspace_path_escape") from exc
        if not target.is_dir():
            raise SourceFilesystemScanError("workspace_directory_required")
        return target

    @staticmethod
    def _entry_path(target: Path, relative_path: str) -> Path:
        pure = PurePosixPath(str(relative_path or ""))
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise SourceFilesystemScanError("snapshot_path_invalid")
        path = target.joinpath(*pure.parts)
        try:
            path.parent.resolve(strict=True).relative_to(target)
        except (OSError, ValueError) as exc:
            raise SourceFilesystemScanError("workspace_path_escape") from exc
        return path

    @staticmethod
    def _actual_paths(
        target: Path,
        *,
        maximum: int,
    ) -> tuple[set[str], int, int]:
        paths: set[str] = set()
        errors = 0
        symlinks = 0
        try:
            for directory, dirnames, filenames in os.walk(
                target,
                topdown=True,
                followlinks=False,
            ):
                base = Path(directory)
                for dirname in tuple(dirnames):
                    child = base / dirname
                    try:
                        if child.is_symlink():
                            symlinks += 1
                            dirnames.remove(dirname)
                    except OSError:
                        errors += 1
                        dirnames.remove(dirname)
                for filename in filenames:
                    path = base / filename
                    paths.add(path.relative_to(target).as_posix())
                    if len(paths) > maximum:
                        return paths, errors + 1, symlinks
        except OSError:
            errors += 1
        return paths, errors, symlinks

    @staticmethod
    def _read_exact(path: Path, before: os.stat_result) -> tuple[bytes, str]:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SourceFilesystemScanError("workspace_path_race")
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 128 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise SourceFilesystemScanError("workspace_path_race")
            return b"".join(chunks), digest.hexdigest()
        finally:
            os.close(descriptor)

    @staticmethod
    def _is_binary(content: bytes) -> bool:
        if not content:
            return False
        sample = content[:8192]
        return b"\x00" in sample or len(_TEXT_CONTROL.findall(sample)) > max(
            1,
            len(sample) // 100,
        )

    @staticmethod
    def _is_archive(entry: WorkspaceFileManifestEntry, content: bytes) -> bool:
        return (
            entry.file_type in _ARCHIVE_TYPES
            or content.startswith(b"PK\x03\x04")
            or content.startswith(b"\x1f\x8b")
            or content.startswith(b"ustar", 257)
        )

    @staticmethod
    def _archive_ratio(
        content: bytes,
        *,
        entry: WorkspaceFileManifestEntry,
        budgets: SourceAdmissionBudgets,
    ) -> tuple[float, bool]:
        total = 0
        count = 0
        try:
            if content.startswith(b"PK\x03\x04") or entry.file_type == "zip":
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for item in archive.infolist():
                        if item.is_dir():
                            continue
                        _validate_archive_member(item.filename)
                        count += 1
                        total += int(item.file_size)
                        if item.file_size > budgets.max_file_bytes:
                            return _ratio(total, len(content)), True
                        if count > budgets.max_files or total > budgets.max_total_bytes:
                            return _ratio(total, len(content)), True
            elif entry.file_type in {"tar", "tgz", "gz", "bz2", "xz"}:
                with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
                    for item in archive:
                        if item.isdir():
                            continue
                        if not item.isfile() or item.issym() or item.islnk():
                            return _ratio(total, len(content)), True
                        _validate_archive_member(item.name)
                        count += 1
                        total += int(item.size)
                        if item.size > budgets.max_file_bytes:
                            return _ratio(total, len(content)), True
                        if count > budgets.max_files or total > budgets.max_total_bytes:
                            return _ratio(total, len(content)), True
            else:
                return 1.0, True
        except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError):
            return 1.0, True
        return _ratio(total, len(content)), False


def _validate_archive_member(value: str) -> None:
    path = PurePosixPath(str(value or ""))
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        raise ValueError("archive_member_path_invalid")


def _ratio(expanded: int, compressed: int) -> float:
    return max(1.0, float(expanded) / float(max(1, compressed)))


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ProductionFilesystemSourceScanner",
    "SourceFilesystemScanError",
    "SourceFilesystemScanResult",
]
