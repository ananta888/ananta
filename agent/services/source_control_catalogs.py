"""Read-only, scope-safe catalogs for source-control v1 selections."""

from __future__ import annotations

import base64
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
)


class SourceControlCatalogError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class ScopedRegisteredWorkspaceCatalog:
    """Server registration catalog; local roots never cross the API boundary."""

    def __init__(
        self, workspaces: Sequence[RegisteredWorkspace] = ()
    ) -> None:
        self._lock = threading.RLock()
        self._values: dict[
            tuple[str, str, str, str], RegisteredWorkspace
        ] = {}
        for workspace in workspaces:
            self.upsert(workspace)

    def upsert(self, workspace: RegisteredWorkspace) -> None:
        with self._lock:
            self._values[
                (
                    workspace.tenant_id,
                    workspace.project_id,
                    str(getattr(workspace, "owner_id", None) or ""),
                    workspace.workspace_id,
                )
            ] = workspace

    def get(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None = None,
    ) -> RegisteredWorkspace | None:
        with self._lock:
            matches = tuple(
                value
                for (tenant, project, owner, identifier), value
                in self._values.items()
                if tenant == tenant_id
                and project == project_id
                and identifier == workspace_id
                and (
                    owner_id is None
                    or not owner
                    or owner == owner_id
                )
            )
            return matches[0] if len(matches) == 1 else None

    def resolve_registered_workspace(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> RegisteredWorkspace | None:
        return self.get(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
        )

    def list_registered(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None = None,
    ) -> tuple[RegisteredWorkspace, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        value
                        for (tenant, project, owner, _), value
                        in self._values.items()
                        if tenant == tenant_id and project == project_id
                        and (
                            owner_id is None
                            or not owner
                            or owner == owner_id
                        )
                    ),
                    key=lambda value: value.workspace_id,
                )
            )


_CATALOG_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_WORKSPACE_TYPES = frozenset({"registered_workspace", "local_directory"})
_HOST_PATH_FIELDS = frozenset(
    {"absolute_path", "host_path", "local_path", "path", "root", "workspace_root"}
)


class SourceRegistryRegisteredWorkspaceCatalog:
    """Join path-free SourceRegistry bindings to server-owned workspace roots."""

    def __init__(self, *, registry: object, registrations: object) -> None:
        self._registry = registry
        self._registrations = registrations

    def get(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None = None,
    ) -> RegisteredWorkspace | None:
        matches = tuple(
            item
            for item in self._records(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
            )
            if item.workspace_id == workspace_id
        )
        return matches[0] if len(matches) == 1 else None

    def resolve_registered_workspace(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> RegisteredWorkspace | None:
        return self.get(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
        )

    def list_registered(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None = None,
    ) -> tuple[RegisteredWorkspace, ...]:
        return self._records(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
        )

    def _records(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[RegisteredWorkspace, ...]:
        list_sources = getattr(self._registry, "list_sources", None)
        resolve_workspace = getattr(
            self._registrations, "resolve_workspace", None
        )
        if not callable(list_sources) or not callable(resolve_workspace):
            return ()
        indexed: dict[tuple[str, str], RegisteredWorkspace] = {}
        for descriptor in list_sources(include_disabled=True):
            if not isinstance(descriptor, Mapping):
                continue
            extensions = descriptor.get("extensions")
            raw_binding = (
                extensions.get("source_control")
                if isinstance(extensions, Mapping)
                else None
            )
            binding = (
                dict(raw_binding)
                if isinstance(raw_binding, Mapping)
                else dict(descriptor)
            )
            connector_type = str(
                binding.get("connector_type")
                or descriptor.get("source_type")
                or ""
            )
            if connector_type not in _WORKSPACE_TYPES:
                continue
            if any(name in binding for name in _HOST_PATH_FIELDS):
                continue
            coordinates = {
                name: str(binding.get(name) or "").strip()
                for name in (
                    "tenant_id",
                    "project_id",
                    "owner_id",
                    "workspace_id",
                )
            }
            if any(
                _CATALOG_ID.fullmatch(value) is None
                for value in coordinates.values()
            ):
                continue
            if (
                coordinates["tenant_id"] != tenant_id
                or coordinates["project_id"] != project_id
                or (
                    owner_id is not None
                    and coordinates["owner_id"] != owner_id
                )
            ):
                continue
            enabled = bool(
                descriptor.get("enabled", True)
                and binding.get("enabled", True)
            )
            read_only = binding.get("read_only") is True
            if not read_only:
                continue
            registration = resolve_workspace(coordinates["workspace_id"])
            root = getattr(registration, "root", None)
            try:
                root_path = Path(root).expanduser()
                if root_path.is_symlink():
                    continue
                root_path = root_path.resolve(strict=True)
            except (OSError, TypeError, ValueError):
                continue
            if not root_path.is_dir():
                continue
            record = RegisteredWorkspace(
                workspace_id=coordinates["workspace_id"],
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=coordinates["owner_id"],
                root=root_path,
                enabled=enabled,
                read_only=True,
            )
            indexed[(record.owner_id or "", record.workspace_id)] = record
        return tuple(
            indexed[key]
            for key in sorted(indexed, key=lambda item: (item[1], item[0]))
        )


@dataclass(frozen=True)
class SourceControlReadCatalogService:
    workspaces: object
    remotes: object
    index_profiles: object

    def list_workspaces(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        roles: frozenset[str],
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        method = getattr(self.workspaces, "list_registered", None)
        values = (
            method(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=None if "admin" in roles else actor_id,
            )
            if callable(method)
            else ()
        )
        items = [
            {
                "workspace_id": item.workspace_id,
                "enabled": bool(item.enabled),
                "read_only": bool(item.read_only),
                "capabilities": {
                    "selection_only": True,
                    "source_types": [
                        "registered_workspace",
                        "local_directory",
                    ],
                    "raw_path_exposed": False,
                },
            }
            for item in values
            if (
                not filters.get("enabled")
                or str(bool(item.enabled)).lower()
                == filters["enabled"].lower()
            )
        ]
        return self._page(
            items=items,
            id_field="workspace_id",
            cursor=cursor,
            limit=limit,
            query=filters.get("q"),
            project_id=project_id,
        )

    def list_registered_remotes(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        roles: frozenset[str],
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        method = getattr(self.remotes, "list_authorizations", None)
        values = (
            method(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=None if "admin" in roles else actor_id,
            )
            if callable(method)
            else ()
        )
        items: list[dict[str, object]] = []
        for item in values:
            if (
                filters.get("state")
                and item.authorization_state != filters["state"]
            ):
                continue
            kind = (
                "github"
                if item.authorization_kind.startswith("github_")
                else "git"
            )
            if filters.get("kind") and kind != filters["kind"]:
                continue
            items.append(
                {
                    "remote_id": item.connection_ref,
                    "kind": kind,
                    "repository": item.repository,
                    "state": item.authorization_state,
                    "capabilities": {
                        "selection_only": True,
                        "connector_type": kind,
                        "granted_scopes": sorted(item.granted_scopes),
                        "remote_url_exposed": False,
                        "credential_exposed": False,
                    },
                }
            )
        return self._page(
            items=items,
            id_field="remote_id",
            cursor=cursor,
            limit=limit,
            query=filters.get("q"),
            project_id=project_id,
        )

    def list_index_profiles(
        self,
        *,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        method = getattr(self.index_profiles, "list_profiles", None)
        raw_items = method() if callable(method) else []
        items: list[dict[str, object]] = []
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            profile_id = str(raw.get("name") or "").strip()
            if not profile_id:
                continue
            source = str(raw.get("source") or "")
            if filters.get("source") and source != filters["source"]:
                continue
            flags = raw.get("flags")
            items.append(
                {
                    "profile_id": profile_id,
                    "label": str(raw.get("label") or profile_id),
                    "description": str(raw.get("description") or ""),
                    "is_default": bool(raw.get("is_default")),
                    "capabilities": {
                        "selection_only": True,
                        "task_kinds": list(
                            raw.get("task_kinds") or []
                        ),
                        "retrieval_intents": list(
                            raw.get("retrieval_intents") or []
                        ),
                        "incremental": bool(
                            flags.get("incremental")
                            if isinstance(flags, Mapping)
                            else False
                        ),
                        "resume": bool(
                            flags.get("resume")
                            if isinstance(flags, Mapping)
                            else False
                        ),
                        "progress": bool(
                            flags.get("progress")
                            if isinstance(flags, Mapping)
                            else False
                        ),
                        "config_path_exposed": False,
                    },
                }
            )
        return self._page(
            items=items,
            id_field="profile_id",
            cursor=cursor,
            limit=limit,
            query=filters.get("q"),
            project_id=project_id,
        )

    def require_index_profile(
        self, *, project_id: str, profile_id: str
    ) -> None:
        page = self.list_index_profiles(
            project_id=project_id,
            cursor=None,
            limit=200,
            filters={},
        )
        if not any(
            item.get("profile_id") == profile_id
            for item in page["items"]
        ):
            raise SourceControlCatalogError(
                "index_profile_not_found", status_code=404
            )

    @staticmethod
    def _page(
        *,
        items: Sequence[Mapping[str, object]],
        id_field: str,
        cursor: str | None,
        limit: int,
        query: str | None,
        project_id: str,
    ) -> Mapping[str, object]:
        if not 1 <= limit <= 200:
            raise SourceControlCatalogError("catalog_limit_invalid")
        after = _decode_cursor(cursor)
        needle = str(query or "").strip().lower()
        ordered = sorted(items, key=lambda item: str(item[id_field]))
        visible_candidates = [
            dict(item)
            for item in ordered
            if (after is None or str(item[id_field]) > after)
            and (
                not needle
                or needle
                in " ".join(str(value) for value in item.values()).lower()
            )
        ]
        selected = visible_candidates[: limit + 1]
        visible = selected[:limit]
        return {
            "items": visible,
            "next_cursor": (
                _encode_cursor(str(visible[-1][id_field]))
                if len(selected) > limit and visible
                else None
            ),
            "capabilities": {
                "read_only": True,
                "selection_mode": "server_ids_only",
                "project_id": project_id,
            },
        }


def _encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode(
        "ascii"
    ).rstrip("=")


def _decode_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlCatalogError("catalog_cursor_invalid") from exc
    if not decoded:
        raise SourceControlCatalogError("catalog_cursor_invalid")
    return decoded


__all__ = [
    "ScopedRegisteredWorkspaceCatalog",
    "SourceRegistryRegisteredWorkspaceCatalog",
    "SourceControlCatalogError",
    "SourceControlReadCatalogService",
]
