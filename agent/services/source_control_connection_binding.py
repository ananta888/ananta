"""Internal, secret-free binding from a connection to one source selector."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,191}$")
_CONNECTION_ID = re.compile(r"^conn_[0-9a-f]{64}$")
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_IMPLEMENTATION_CONNECTORS: Mapping[str, str] = {
    "registered_workspace": "registered_workspace",
    "local_directory": "local_directory",
    "git": "generic_git",
    "github": "github_repository",
}


class SourceControlConnectionBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def implementation_connector_type(connector_type: str) -> str:
    try:
        return _IMPLEMENTATION_CONNECTORS[str(connector_type)]
    except KeyError:
        raise SourceControlConnectionBindingError(
            "connector_type_not_registered"
        ) from None


def normalize_workspace_relative_path(value: object) -> str:
    raw = str(value if value is not None else ".").strip() or "."
    if "\x00" in raw or "\\" in raw or len(raw) > 512:
        raise SourceControlConnectionBindingError(
            "workspace_relative_path_invalid"
        )
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or len(path.parts) > 32
        or any(part in {"", ".."} for part in path.parts)
    ):
        raise SourceControlConnectionBindingError(
            "workspace_relative_path_invalid"
        )
    return "/".join(part for part in path.parts if part != ".") or "."


@dataclass(frozen=True)
class SourceConnectionSelectorBinding:
    connection_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    public_connector_type: str
    implementation_connector_type: str
    selector_kind: str
    selector_id: str
    relative_path: str | None = None
    repository_identifier: str | None = None

    def __post_init__(self) -> None:
        if _CONNECTION_ID.fullmatch(self.connection_id) is None:
            raise SourceControlConnectionBindingError("connection_id_invalid")
        if any(
            _IDENTIFIER.fullmatch(value) is None
            for value in (self.tenant_id, self.project_id, self.owner_id)
        ):
            raise SourceControlConnectionBindingError(
                "source_connection_scope_invalid"
            )
        expected_implementation = implementation_connector_type(
            self.public_connector_type
        )
        if self.implementation_connector_type != expected_implementation:
            raise SourceControlConnectionBindingError(
                "implementation_connector_type_mismatch"
            )
        if _IDENTIFIER.fullmatch(self.selector_id) is None:
            raise SourceControlConnectionBindingError("selector_id_invalid")
        if self.public_connector_type in {
            "registered_workspace",
            "local_directory",
        }:
            if self.selector_kind != "workspace":
                raise SourceControlConnectionBindingError(
                    "selector_kind_mismatch"
                )
            normalized = normalize_workspace_relative_path(
                self.relative_path
            )
            if normalized != self.relative_path:
                raise SourceControlConnectionBindingError(
                    "workspace_relative_path_invalid"
                )
            if self.repository_identifier is not None:
                raise SourceControlConnectionBindingError(
                    "workspace_repository_forbidden"
                )
        else:
            if self.selector_kind != "remote" or self.relative_path is not None:
                raise SourceControlConnectionBindingError(
                    "selector_kind_mismatch"
                )
            if self.public_connector_type == "github":
                if (
                    self.repository_identifier is None
                    or _REPOSITORY.fullmatch(self.repository_identifier) is None
                ):
                    raise SourceControlConnectionBindingError(
                        "github_repository_invalid"
                    )
            elif self.repository_identifier is not None:
                raise SourceControlConnectionBindingError(
                    "generic_git_repository_forbidden"
                )

    @property
    def binding_digest(self) -> str:
        return _digest(self.coordinates())

    def coordinates(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "owner_id": self.owner_id,
            "public_connector_type": self.public_connector_type,
            "implementation_connector_type": (
                self.implementation_connector_type
            ),
            "selector_kind": self.selector_kind,
            "selector_id": self.selector_id,
            "relative_path": self.relative_path,
            "repository_identifier": self.repository_identifier,
        }

    def descriptor(
        self,
        *,
        display_name: str,
        enabled: bool,
    ) -> dict[str, object]:
        source_control = {
            "connection_id": self.connection_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "owner_id": self.owner_id,
            "connector_type": self.public_connector_type,
            "implementation_connector_type": (
                self.implementation_connector_type
            ),
            "binding_digest": self.binding_digest,
        }
        descriptor: dict[str, object] = {
            "source_id": f"source-control:{self.connection_id}",
            "source_type": self.implementation_connector_type,
            "display_name": str(display_name)[:200],
            "enabled": bool(enabled),
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "owner_id": self.owner_id,
            "extensions": {"source_control": source_control},
        }
        if self.selector_kind == "workspace":
            descriptor.update(
                {
                    "workspace_id": self.selector_id,
                    "relative_path": self.relative_path or ".",
                }
            )
        elif self.public_connector_type == "github":
            descriptor.update(
                {
                    "github_authorization_ref": self.selector_id,
                    "repository": self.repository_identifier,
                    "ref": "HEAD",
                }
            )
        else:
            descriptor.update(
                {"remote_id": self.selector_id, "ref": "HEAD"}
            )
        return descriptor


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


__all__ = [
    "SourceConnectionSelectorBinding",
    "SourceControlConnectionBindingError",
    "implementation_connector_type",
    "normalize_workspace_relative_path",
]
