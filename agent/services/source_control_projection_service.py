"""Canonical tenant-scoped Source Control Center read model."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


class SourceControlProjectionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SourceControlPrincipal:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]

    def __post_init__(self) -> None:
        for name in ("subject_id", "tenant_id", "project_id"):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, name) or "")):
                raise SourceControlProjectionError(f"{name}_invalid")


@dataclass(frozen=True)
class SourceControlAggregateRecord:
    connection_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    version: int
    connection: Mapping[str, Any]
    revision: Mapping[str, Any] | None
    admission: Mapping[str, Any] | None
    index: Mapping[str, Any] | None
    active_index: Mapping[str, Any] | None
    grants: Sequence[Mapping[str, Any]]
    health: Mapping[str, Any]
    capabilities: frozenset[str]
    visible_subject_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SourceControlPage:
    records: tuple[SourceControlAggregateRecord, ...]
    next_cursor: str | None


class SourceControlProjectionDataPort(Protocol):
    def list_aggregates(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> SourceControlPage: ...

    def get_aggregate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
    ) -> SourceControlAggregateRecord | None: ...


@dataclass(frozen=True)
class SourceControlProjection:
    schema: str
    connection_id: str
    etag: str
    connection: Mapping[str, Any]
    revision: Mapping[str, Any] | None
    admission: Mapping[str, Any] | None
    index: Mapping[str, Any] | None
    active_index: Mapping[str, Any] | None
    stale: bool
    grants: tuple[Mapping[str, Any], ...]
    health: Mapping[str, Any]
    next_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "connection_id": self.connection_id,
            "etag": self.etag,
            "connection": dict(self.connection),
            "revision": dict(self.revision) if self.revision is not None else None,
            "admission": dict(self.admission)
            if self.admission is not None
            else None,
            "index": dict(self.index) if self.index is not None else None,
            "active_index": dict(self.active_index)
            if self.active_index is not None
            else None,
            "stale": self.stale,
            "grants": [dict(grant) for grant in self.grants],
            "health": dict(self.health),
            "next_actions": list(self.next_actions),
        }


@dataclass(frozen=True)
class SourceControlProjectionPage:
    items: tuple[SourceControlProjection, ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.source-control.projection-page.v1",
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
        }


class SourceControlProjectionService:
    def __init__(self, data: SourceControlProjectionDataPort) -> None:
        self._data = data

    def list(
        self,
        *,
        principal: SourceControlPrincipal,
        cursor: str | None = None,
        limit: int = 50,
        filters: Mapping[str, str] | None = None,
    ) -> SourceControlProjectionPage:
        if cursor is not None and not _CURSOR.fullmatch(cursor):
            raise SourceControlProjectionError("cursor_invalid")
        if not isinstance(limit, int) or limit < 1 or limit > 200:
            raise SourceControlProjectionError("limit_invalid")
        normalized_filters = _normalize_filters(filters or {})
        page = self._data.list_aggregates(
            tenant_id=principal.tenant_id,
            project_id=principal.project_id,
            cursor=cursor,
            limit=limit,
            filters=normalized_filters,
        )
        visible = tuple(
            self._project(record, principal)
            for record in page.records
            if self._can_view(record, principal)
        )
        if page.next_cursor is not None and not _CURSOR.fullmatch(page.next_cursor):
            raise SourceControlProjectionError("repository_cursor_invalid")
        return SourceControlProjectionPage(
            items=visible,
            next_cursor=page.next_cursor,
        )

    def get(
        self,
        *,
        principal: SourceControlPrincipal,
        connection_id: str,
    ) -> SourceControlProjection:
        if not _OPAQUE_ID.fullmatch(str(connection_id or "")):
            raise SourceControlProjectionError("connection_id_invalid")
        record = self._data.get_aggregate(
            tenant_id=principal.tenant_id,
            project_id=principal.project_id,
            connection_id=connection_id,
        )
        if record is None or not self._can_view(record, principal):
            raise SourceControlProjectionError("source_not_found")
        return self._project(record, principal)

    @staticmethod
    def assert_if_match(
        projection: SourceControlProjection,
        if_match: str | None,
    ) -> None:
        if if_match is None:
            raise SourceControlProjectionError("if_match_required")
        normalized = if_match.strip().strip('"')
        if normalized != projection.etag:
            raise SourceControlProjectionError("version_conflict")

    @staticmethod
    def _can_view(
        record: SourceControlAggregateRecord,
        principal: SourceControlPrincipal,
    ) -> bool:
        if (
            record.tenant_id != principal.tenant_id
            or record.project_id != principal.project_id
        ):
            return False
        return bool(
            {"admin", "project_owner"} & principal.roles
            or principal.subject_id == record.owner_id
            or principal.subject_id in record.visible_subject_ids
        )

    def _project(
        self,
        record: SourceControlAggregateRecord,
        principal: SourceControlPrincipal,
    ) -> SourceControlProjection:
        if not self._can_view(record, principal):
            raise SourceControlProjectionError("source_not_found")
        stale = _derive_stale(record)
        next_actions = _derive_next_actions(
            record,
            principal=principal,
            stale=stale,
        )
        connection = {
            key: value
            for key, value in record.connection.items()
            if key != "tenant_id"
        }
        connection["project_id"] = record.project_id
        payload_for_etag = {
            "connection_id": record.connection_id,
            "connection": connection,
            "version": record.version,
            "revision": record.revision,
            "admission": record.admission,
            "index": record.index,
            "active_index": record.active_index,
            "grants": list(record.grants),
            "health": record.health,
            "next_actions": next_actions,
        }
        etag = hashlib.sha256(
            json.dumps(
                payload_for_etag,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return SourceControlProjection(
            schema="ananta.source-control.projection.v1",
            connection_id=record.connection_id,
            etag=etag,
            connection=connection,
            revision=record.revision,
            admission=record.admission,
            index=record.index,
            active_index=record.active_index,
            stale=stale,
            grants=tuple(record.grants),
            health=record.health,
            next_actions=next_actions,
        )


def _derive_stale(record: SourceControlAggregateRecord) -> bool:
    revision_id = str((record.revision or {}).get("source_revision_id") or "")
    active_revision_id = str(
        (record.active_index or {}).get("source_revision_id") or ""
    )
    policy_changed = bool((record.index or {}).get("policy_changed", False))
    return bool(
        revision_id
        and (
            not active_revision_id
            or active_revision_id != revision_id
            or policy_changed
        )
    )


def _derive_next_actions(
    record: SourceControlAggregateRecord,
    *,
    principal: SourceControlPrincipal,
    stale: bool,
) -> tuple[str, ...]:
    capabilities = record.capabilities
    mutator = bool(
        {"admin", "project_owner"} & principal.roles
        or principal.subject_id == record.owner_id
    )
    actions: list[str] = []
    connection_state = str(record.connection.get("state") or "")
    admission_state = str((record.admission or {}).get("state") or "")
    index_status = str((record.index or {}).get("status") or "")
    if "refresh" in capabilities and connection_state == "active":
        actions.append("refresh")
    if (
        "index" in capabilities
        and admission_state == "admitted"
        and (stale or index_status in {"", "failed"})
    ):
        actions.append("index")
    if (
        mutator
        and "activate" in capabilities
        and index_status == "completed"
        and not bool((record.index or {}).get("active", False))
    ):
        actions.append("activate")
    if mutator and "grant" in capabilities and record.revision is not None:
        actions.append("grant")
    if mutator and "disable" in capabilities and connection_state == "active":
        actions.append("disable")
    if mutator and "rollback" in capabilities and bool(
        (record.index or {}).get("rollback_candidate", False)
    ):
        actions.append("rollback")
    return tuple(actions)


def _normalize_filters(filters: Mapping[str, str]) -> dict[str, str]:
    allowed = {
        "connector_type",
        "state",
        "stale",
        "owner_id",
        "health",
    }
    if set(filters) - allowed:
        raise SourceControlProjectionError("filter_invalid")
    normalized: dict[str, str] = {}
    for key, value in filters.items():
        text = str(value).strip()
        if not text or len(text) > 128:
            raise SourceControlProjectionError("filter_value_invalid")
        normalized[key] = text
    return normalized
