#!/usr/bin/env python3
"""Prepare writable local-development paths for unprivileged Ananta services."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

_DATA_NAMES = ("alpha", "beta", "frontend-angular-cache", "hub")
_MAX_ID = 2_147_483_647


class RuntimeOwnershipBootstrapError(RuntimeError):
    """Raised when a development runtime path cannot be prepared safely."""


def _validate_owner(owner_uid: int, owner_gid: int) -> None:
    if (
        owner_uid < 1
        or owner_gid < 1
        or owner_uid > _MAX_ID
        or owner_gid > _MAX_ID
    ):
        raise RuntimeOwnershipBootstrapError(
            "runtime owner UID and GID must be positive numeric IDs"
        )


def _validate_directory(path: Path, *, description: str) -> Path:
    if not path.is_absolute() or path == Path("/"):
        raise RuntimeOwnershipBootstrapError(
            f"{description} must be an absolute non-root directory"
        )
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeOwnershipBootstrapError(
            f"{description} is unavailable"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeOwnershipBootstrapError(
            f"{description} must be a real directory"
        )
    return path


def _assign_tree(path: Path, *, owner_uid: int, owner_gid: int) -> None:
    """Assign one mounted data tree without following symbolic links."""

    for directory, child_directories, filenames in os.walk(
        path,
        topdown=False,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in (*child_directories, *filenames):
            child = directory_path / name
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise RuntimeOwnershipBootstrapError(
                    "runtime data entry became unavailable"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeOwnershipBootstrapError(
                    "runtime data tree contains a symbolic link"
                )
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise RuntimeOwnershipBootstrapError(
                    "runtime data tree contains an unsupported entry"
                )
            if stat.S_ISREG(metadata.st_mode) and int(metadata.st_nlink) != 1:
                raise RuntimeOwnershipBootstrapError(
                    "runtime data tree contains a multiply linked file"
                )
            _assign_if_needed(
                child,
                metadata,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
        _assign_if_needed(
            directory_path,
            directory_path.lstat(),
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )


def _assign_if_needed(
    path: Path,
    metadata: os.stat_result,
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    if (int(metadata.st_uid), int(metadata.st_gid)) == (owner_uid, owner_gid):
        return
    if int(metadata.st_uid) not in {0, owner_uid} or int(metadata.st_gid) not in {
        0,
        owner_gid,
    }:
        raise RuntimeOwnershipBootstrapError(
            "runtime data entry belongs to an unexpected owner"
        )
    os.chown(path, owner_uid, owner_gid, follow_symlinks=False)


def prepare_runtime_ownership(
    data_root: Path,
    workspace_root: Path,
    credential_root: Path,
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Prepare fixed Compose mounts for Hub and Worker runtime users."""

    _validate_owner(owner_uid, owner_gid)
    normalized_data_root = _validate_directory(
        data_root,
        description="runtime data root",
    )
    normalized_workspace_root = _validate_directory(
        workspace_root,
        description="workspace root",
    )
    normalized_credential_root = _validate_directory(
        credential_root,
        description="workflow credential root",
    )

    for name in _DATA_NAMES:
        child = _validate_directory(
            normalized_data_root / name,
            description=f"{name} runtime data directory",
        )
        _assign_tree(child, owner_uid=owner_uid, owner_gid=owner_gid)

    # Existing workspaces may intentionally have their own ownership. Only the
    # mount root is prepared so new Hub-owned workspaces can be created.
    _assign_if_needed(
        normalized_workspace_root,
        normalized_workspace_root.lstat(),
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    # The credential bootstrap itself runs unprivileged. This init only
    # migrates ownership; the credential bootstrap validates all contents.
    _assign_tree(
        normalized_credential_root,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare local Compose data mounts for unprivileged Hub and Worker "
            "containers."
        )
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--credential-root", type=Path, required=True)
    parser.add_argument("--owner-uid", type=int, required=True)
    parser.add_argument("--owner-gid", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        prepare_runtime_ownership(
            args.data_root,
            args.workspace_root,
            args.credential_root,
            owner_uid=args.owner_uid,
            owner_gid=args.owner_gid,
        )
    except (OSError, RuntimeOwnershipBootstrapError) as exc:
        print(f"runtime ownership bootstrap failed: {exc}", file=os.sys.stderr)
        return 64
    print("development runtime ownership prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
