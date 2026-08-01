"""Closed API DTOs and internal path-redacted workspace records."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from agent.sources.git_source_connector_common import GitSourceScope

_FOLDER_HANDLE = re.compile(r"^fld_[A-Za-z0-9_-]{32,96}$")
_VALIDATION_HANDLE = re.compile(r"^wsv1_[A-Za-z0-9_-]{32,96}$")
_WORKSPACE_ID = re.compile(r"^ws_[A-Za-z0-9_-]{32,96}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class SourceControlWorkspaceContractError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


def _closed_text(
    payload: Mapping[str, object],
    *,
    fields: frozenset[str],
    field: str,
) -> str:
    if set(payload) != fields:
        raise SourceControlWorkspaceContractError(
            "workspace_registration_payload_fields_invalid"
        )
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SourceControlWorkspaceContractError(
            "workspace_registration_payload_invalid"
        )
    return value.strip()


@dataclass(frozen=True, repr=False)
class WorkspaceFolderSnapshot:
    folder_handle: str
    display_name: str
    root: Path
    root_fingerprint: str
    manifest_digest: str
    item_count: int

    def __post_init__(self) -> None:
        if (
            _FOLDER_HANDLE.fullmatch(self.folder_handle) is None
            or not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 80
            or "/" in self.display_name
            or "\\" in self.display_name
            or _DIGEST.fullmatch(self.root_fingerprint) is None
            or _DIGEST.fullmatch(self.manifest_digest) is None
            or self.item_count < 0
        ):
            raise SourceControlWorkspaceContractError(
                "workspace_folder_snapshot_invalid"
            )

    def __repr__(self) -> str:
        return (
            "WorkspaceFolderSnapshot("
            f"folder_handle={self.folder_handle!r}, root=<redacted>)"
        )


@dataclass(frozen=True)
class WorkspaceFolderSelection:
    folder_handle: str

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> "WorkspaceFolderSelection":
        handle = _closed_text(
            payload,
            fields=frozenset({"folder_handle"}),
            field="folder_handle",
        )
        if _FOLDER_HANDLE.fullmatch(handle) is None:
            raise SourceControlWorkspaceContractError(
                "workspace_folder_handle_invalid"
            )
        return cls(folder_handle=handle)


@dataclass(frozen=True)
class WorkspaceCreateSelection:
    validation_handle: str

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> "WorkspaceCreateSelection":
        handle = _closed_text(
            payload,
            fields=frozenset({"validation_handle"}),
            field="validation_handle",
        )
        if _VALIDATION_HANDLE.fullmatch(handle) is None:
            raise SourceControlWorkspaceContractError(
                "workspace_validation_handle_invalid"
            )
        return cls(validation_handle=handle)

    @property
    def handle_digest(self) -> str:
        return hashlib.sha256(
            self.validation_handle.encode("ascii")
        ).hexdigest()


@dataclass(frozen=True)
class WorkspaceValidationBinding:
    scope: GitSourceScope
    folder_handle: str
    root_fingerprint: str
    manifest_digest: str

    def __post_init__(self) -> None:
        if (
            _FOLDER_HANDLE.fullmatch(self.folder_handle) is None
            or _DIGEST.fullmatch(self.root_fingerprint) is None
            or _DIGEST.fullmatch(self.manifest_digest) is None
        ):
            raise SourceControlWorkspaceContractError(
                "workspace_validation_binding_invalid"
            )


@dataclass(frozen=True)
class WorkspaceRegistrationRecord:
    workspace_id: str
    binding: WorkspaceValidationBinding
    registration_state: str
    read_only: bool
    lock_version: int
    created_at_epoch: float
    updated_at_epoch: float

    def __post_init__(self) -> None:
        if (
            _WORKSPACE_ID.fullmatch(self.workspace_id) is None
            or self.registration_state not in {"active", "disabled"}
            or self.read_only is not True
            or self.lock_version < 1
        ):
            raise SourceControlWorkspaceContractError(
                "workspace_registration_record_invalid"
            )

    @property
    def etag(self) -> str:
        return f'"workspace-v1:{self.lock_version}"'


__all__ = [
    "SourceControlWorkspaceContractError",
    "WorkspaceCreateSelection",
    "WorkspaceFolderSelection",
    "WorkspaceFolderSnapshot",
    "WorkspaceRegistrationRecord",
    "WorkspaceValidationBinding",
]
