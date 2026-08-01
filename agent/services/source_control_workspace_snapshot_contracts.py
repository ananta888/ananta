"""Closed contracts for browser-folder workspace snapshot uploads."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Mapping, Protocol, runtime_checkable


MAX_SNAPSHOT_FILES = 2_000
MAX_SNAPSHOT_FILE_BYTES = 20 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 200 * 1024 * 1024
MAX_SNAPSHOT_RELATIVE_PATH_BYTES = 512
MAX_SNAPSHOT_DEPTH = 64
MAX_SNAPSHOT_DISPLAY_NAME_CHARS = 80

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_WORKSPACE_ID = re.compile(r"^ws_[A-Za-z0-9_-]{32,96}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)
_PORTABLE_FORBIDDEN = frozenset('<>:"|?*')
_RESERVED_PATH_COMPONENTS = frozenset({".git", ".hg", ".svn", ".ananta"})


class WorkspaceSnapshotContractError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkspaceSnapshotLimits:
    max_files: int = MAX_SNAPSHOT_FILES
    max_file_bytes: int = MAX_SNAPSHOT_FILE_BYTES
    max_total_bytes: int = MAX_SNAPSHOT_TOTAL_BYTES
    max_relative_path_bytes: int = MAX_SNAPSHOT_RELATIVE_PATH_BYTES
    max_depth: int = MAX_SNAPSHOT_DEPTH

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (
                self.max_files,
                self.max_file_bytes,
                self.max_total_bytes,
                self.max_relative_path_bytes,
                self.max_depth,
            )
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_limits_invalid"
            )


@runtime_checkable
class WorkspaceSnapshotUploadFile(Protocol):
    filename: str
    stream: BinaryIO


@dataclass(frozen=True, repr=False)
class BrowserFolderSnapshotRequest:
    display_name: str
    idempotency_key: str

    @classmethod
    def from_values(
        cls,
        *,
        display_name: object,
        idempotency_key: object,
    ) -> "BrowserFolderSnapshotRequest":
        name = _validated_component(
            display_name,
            reason_code="workspace_snapshot_display_name_invalid",
        )
        if (
            len(name) > MAX_SNAPSHOT_DISPLAY_NAME_CHARS
            or len(name.encode("utf-8")) > 237
            or name.casefold().startswith(".ananta-snapshot-")
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_display_name_invalid"
            )
        key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY_KEY.fullmatch(key) is None:
            raise WorkspaceSnapshotContractError(
                "idempotency_key_required"
            )
        return cls(display_name=name, idempotency_key=key)

    def __repr__(self) -> str:
        return "BrowserFolderSnapshotRequest(<redacted>)"


@dataclass(frozen=True, repr=False)
class BrowserSnapshotRelativePath:
    browser_root: str
    relative_path: str
    parts: tuple[str, ...]
    collision_key: str

    @classmethod
    def parse(
        cls,
        filename: object,
        *,
        limits: WorkspaceSnapshotLimits,
    ) -> "BrowserSnapshotRelativePath":
        if not isinstance(filename, str):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_relative_path_invalid"
            )
        raw = filename
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_relative_path_invalid"
            ) from exc
        if (
            not raw
            or len(encoded) > limits.max_relative_path_bytes
            or "\\" in raw
            or "\x00" in raw
            or raw.startswith("/")
            or raw.endswith("/")
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_relative_path_invalid"
            )
        raw_parts = raw.split("/")
        if (
            len(raw_parts) < 2
            or len(raw_parts) - 1 > limits.max_depth
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_relative_path_invalid"
            )
        normalized = tuple(
            _validated_component(
                part,
                reason_code="workspace_snapshot_relative_path_invalid",
            )
            for part in raw_parts
        )
        if any(
            part.casefold() in _RESERVED_PATH_COMPONENTS
            for part in normalized
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_reserved_path_denied"
            )
        pure = PurePosixPath(*normalized)
        if pure.is_absolute():
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_relative_path_invalid"
            )
        relative_parts = normalized[1:]
        relative_path = "/".join(relative_parts)
        return cls(
            browser_root=normalized[0],
            relative_path=relative_path,
            parts=relative_parts,
            collision_key="/".join(part.casefold() for part in relative_parts),
        )

    def __repr__(self) -> str:
        return "BrowserSnapshotRelativePath(<redacted>)"


@dataclass(frozen=True, repr=False)
class StagedSnapshotFile:
    relative_path: str
    byte_size: int
    content_digest: str
    file_type: str

    def __post_init__(self) -> None:
        if (
            not self.relative_path
            or self.byte_size < 1
            or _DIGEST.fullmatch(self.content_digest) is None
            or re.fullmatch(r"[a-z0-9+_-]{1,32}|unknown", self.file_type)
            is None
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_manifest_invalid"
            )

    def __repr__(self) -> str:
        return (
            "StagedSnapshotFile(path=<redacted>, "
            f"byte_size={self.byte_size})"
        )


@dataclass(frozen=True, repr=False)
class StagedSnapshotManifest:
    files: tuple[StagedSnapshotFile, ...]
    total_bytes: int
    manifest_digest: str

    def __post_init__(self) -> None:
        if (
            not self.files
            or self.total_bytes != sum(item.byte_size for item in self.files)
            or _DIGEST.fullmatch(self.manifest_digest) is None
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_manifest_invalid"
            )

    @property
    def file_count(self) -> int:
        return len(self.files)

    def __repr__(self) -> str:
        return (
            "StagedSnapshotManifest(files=<redacted>, "
            f"file_count={self.file_count}, total_bytes={self.total_bytes})"
        )


@dataclass(frozen=True)
class WorkspaceSnapshotResult:
    workspace_id: str
    state: str
    file_count: int
    total_bytes: int
    replayed: bool

    def __post_init__(self) -> None:
        if (
            _WORKSPACE_ID.fullmatch(self.workspace_id) is None
            or self.state not in {"active", "disabled"}
            or self.file_count < 1
            or self.total_bytes < 1
            or not isinstance(self.replayed, bool)
        ):
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_result_invalid",
                status_code=500,
            )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        replayed: bool | None = None,
    ) -> "WorkspaceSnapshotResult":
        if set(payload) != {
            "workspace_id",
            "state",
            "file_count",
            "total_bytes",
            "replayed",
        }:
            raise WorkspaceSnapshotContractError(
                "workspace_snapshot_result_invalid",
                status_code=500,
            )
        return cls(
            workspace_id=str(payload["workspace_id"]),
            state=str(payload["state"]),
            file_count=_strict_int(payload["file_count"]),
            total_bytes=_strict_int(payload["total_bytes"]),
            replayed=(
                replayed
                if replayed is not None
                else _strict_bool(payload["replayed"])
            ),
        )

    def to_public(self) -> Mapping[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "state": self.state,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "replayed": self.replayed,
        }


def _validated_component(value: object, *, reason_code: str) -> str:
    if not isinstance(value, str):
        raise WorkspaceSnapshotContractError(reason_code)
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized != value
        or normalized in {".", ".."}
        or normalized[-1] in {" ", "."}
        or "/" in normalized
        or "\\" in normalized
        or any(character in _PORTABLE_FORBIDDEN for character in normalized)
        or _WINDOWS_RESERVED.fullmatch(normalized) is not None
        or len(normalized.encode("utf-8")) > 255
        or any(
            not character.isprintable()
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in normalized
        )
    ):
        raise WorkspaceSnapshotContractError(reason_code)
    return normalized


def _strict_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkspaceSnapshotContractError(
            "workspace_snapshot_result_invalid",
            status_code=500,
        )
    return value


def _strict_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise WorkspaceSnapshotContractError(
            "workspace_snapshot_result_invalid",
            status_code=500,
        )
    return value


__all__ = [
    "BrowserFolderSnapshotRequest",
    "BrowserSnapshotRelativePath",
    "MAX_SNAPSHOT_DISPLAY_NAME_CHARS",
    "MAX_SNAPSHOT_FILES",
    "MAX_SNAPSHOT_FILE_BYTES",
    "MAX_SNAPSHOT_RELATIVE_PATH_BYTES",
    "MAX_SNAPSHOT_TOTAL_BYTES",
    "StagedSnapshotFile",
    "StagedSnapshotManifest",
    "WorkspaceSnapshotContractError",
    "WorkspaceSnapshotLimits",
    "WorkspaceSnapshotResult",
    "WorkspaceSnapshotUploadFile",
]
