"""Filesystem policy and atomic publication helpers."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from .errors import BackupError

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_WINDOWS_MOUNT_ROOT = Path("/mnt")


@contextmanager
def private_umask() -> Iterator[None]:
    """Apply a private default mode for every artifact created in the scope."""

    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def require_absolute_directory(
    path: Path, label: str, *, writable: bool = True
) -> Path:
    """Resolve an explicit existing directory without accepting a symlink target."""

    if not path.is_absolute():
        raise BackupError(f"{label} must be an absolute path")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise BackupError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise BackupError(f"{label} must not be a symbolic link: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise BackupError(f"{label} is not a directory: {path}")
    resolved = path.resolve(strict=True)
    required_access = os.R_OK | os.X_OK | (os.W_OK if writable else 0)
    if not os.access(resolved, required_access):
        qualifier = "readable and writable" if writable else "readable"
        raise BackupError(f"{label} is not {qualifier}: {resolved}")
    return resolved


def require_private_wsl_directory(path: Path, label: str) -> Path:
    """Require an existing WSL target outside Windows interoperability mounts."""

    resolved = require_absolute_directory(path, label)
    try:
        common = os.path.commonpath(
            (str(_WINDOWS_MOUNT_ROOT), str(resolved))
        )
    except ValueError:
        common = ""
    if common == str(_WINDOWS_MOUNT_ROOT):
        raise BackupError(
            f"{label} must be on the private WSL filesystem, not below /mnt"
        )
    return resolved


def require_new_or_empty_target(path: Path, label: str) -> tuple[Path, bool]:
    """Validate a restore target and report whether an empty directory exists."""

    if not path.is_absolute():
        raise BackupError(f"{label} must be an absolute path")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir() or not os.access(parent, os.R_OK | os.W_OK | os.X_OK):
        raise BackupError(f"{label} parent is not readable and writable: {parent}")
    normalized = parent / path.name
    if not normalized.exists():
        return normalized, False
    metadata = normalized.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BackupError(f"{label} must be new or an empty real directory")
    if any(normalized.iterdir()):
        raise BackupError(f"{label} must be empty: {normalized}")
    return normalized, True


def ensure_disjoint(left: Path, right: Path, labels: tuple[str, str]) -> None:
    """Reject nesting which could make cleanup consume an input or output."""

    left_text = str(left.resolve(strict=False))
    right_text = str(right.resolve(strict=False))
    try:
        common = os.path.commonpath((left_text, right_text))
    except ValueError:
        return
    if common in {left_text, right_text}:
        raise BackupError(f"{labels[0]} and {labels[1]} must be disjoint")


def write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    """Create a private JSON file and fsync it before returning."""

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def load_json_object(path: Path, label: str) -> dict[str, object]:
    """Load a bounded JSON object."""

    try:
        descriptor = _open_regular_readonly(path)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if metadata.st_size > 1024 * 1024:
                raise BackupError(f"{label} is unexpectedly large")
            body = source.read(1024 * 1024 + 1)
            _require_stable_source(source.fileno(), metadata, len(body), path)
        if len(body) > 1024 * 1024:
            raise BackupError(f"{label} is unexpectedly large")
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"Cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BackupError(f"{label} must contain a JSON object")
    return payload


def sha256_file(path: Path) -> tuple[str, int]:
    """Hash one stable regular-file descriptor without following symlinks."""

    digest = hashlib.sha256()
    size = 0
    descriptor = _open_regular_readonly(path)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        _require_stable_source(source.fileno(), metadata, size, path)
    return digest.hexdigest(), size


def copy_exclusive(source: Path, destination: Path) -> None:
    """Copy a file privately, fsync it, and refuse overwrites."""

    copy_exclusive_and_hash(source, destination)


def copy_exclusive_and_hash(
    source: Path,
    destination: Path,
) -> tuple[str, int]:
    """Copy from one stable descriptor and return the copied content digest."""

    source_descriptor = _open_regular_readonly(source)
    try:
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except BaseException:
        os.close(source_descriptor)
        raise
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            os.fdopen(source_descriptor, "rb") as input_file,
            os.fdopen(destination_descriptor, "wb") as output_file,
        ):
            metadata = os.fstat(input_file.fileno())
            for block in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
                output_file.write(block)
            _require_stable_source(
                input_file.fileno(),
                metadata,
                size,
                source,
            )
            output_file.flush()
            os.fsync(output_file.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def _open_regular_readonly(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupError(f"Cannot open a regular file safely: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError(f"Expected a regular file: {path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _require_stable_source(
    descriptor: int,
    before: os.stat_result,
    bytes_read: int,
    path: Path,
) -> None:
    after = os.fstat(descriptor)
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if (
        bytes_read != before.st_size
        or any(getattr(before, field) != getattr(after, field) for field in stable_fields)
    ):
        raise BackupError(f"File changed while it was being read: {path}")


def rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish one directory while refusing every existing target."""

    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise BackupError(
            "Atomic no-clobber directory publication is unavailable"
        ) from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise BackupError(f"Backup package already exists: {destination.name}")
    raise BackupError(
        "Atomic no-clobber directory publication failed: "
        f"{os.strerror(error_number)}"
    )


def fsync_directory(path: Path) -> None:
    """Persist directory entries when the filesystem supports fsync."""

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def remove_private_tree(path: Path | None) -> None:
    """Remove only a caller-created temporary directory."""

    if path is not None and path.exists():
        shutil.rmtree(path)
