from __future__ import annotations

import pytest

from agent.services.source_control_projection_service import (
    SourceControlAggregateRecord,
    SourceControlPage,
    SourceControlPrincipal,
    SourceControlProjectionError,
    SourceControlProjectionService,
)


class _Data:
    def __init__(self, records) -> None:
        self.records = records

    def list_aggregates(self, **kwargs):
        return SourceControlPage(records=tuple(self.records), next_cursor=None)

    def get_aggregate(self, **kwargs):
        return next(
            (
                record
                for record in self.records
                if record.connection_id == kwargs["connection_id"]
                and record.tenant_id == kwargs["tenant_id"]
                and record.project_id == kwargs["project_id"]
            ),
            None,
        )


def _principal(**overrides) -> SourceControlPrincipal:
    values = {
        "subject_id": "user-example",
        "tenant_id": "tenant-example",
        "project_id": "project-example",
        "roles": frozenset({"member"}),
    }
    values.update(overrides)
    return SourceControlPrincipal(**values)


def _record(**overrides) -> SourceControlAggregateRecord:
    values = {
        "connection_id": "connection-example",
        "tenant_id": "tenant-example",
        "project_id": "project-example",
        "owner_id": "user-example",
        "version": 1,
        "connection": {"state": "active", "connector_type": "workspace"},
        "revision": {
            "source_revision_id": "revision-new",
            "revision_digest": "a" * 64,
        },
        "admission": {"state": "admitted"},
        "index": {
            "status": "completed",
            "active": False,
            "rollback_candidate": True,
        },
        "active_index": {"source_revision_id": "revision-old"},
        "grants": (),
        "health": {"status": "healthy"},
        "capabilities": frozenset(
            {"refresh", "index", "activate", "grant", "disable", "rollback"}
        ),
    }
    values.update(overrides)
    return SourceControlAggregateRecord(**values)


def test_projection_derives_stale_etag_and_allowed_actions() -> None:
    service = SourceControlProjectionService(_Data([_record()]))

    projection = service.get(
        principal=_principal(),
        connection_id="connection-example",
    )

    assert projection.stale is True
    assert len(projection.etag) == 64
    assert projection.next_actions == (
        "refresh",
        "index",
        "activate",
        "grant",
        "disable",
        "rollback",
    )
    assert projection.connection["project_id"] == "project-example"
    assert "tenant_id" not in projection.connection


def test_cross_tenant_project_and_non_owner_are_hidden_as_not_found() -> None:
    service = SourceControlProjectionService(_Data([_record()]))

    for principal in (
        _principal(tenant_id="other-tenant"),
        _principal(project_id="other-project"),
        _principal(subject_id="other-user"),
    ):
        with pytest.raises(SourceControlProjectionError, match="source_not_found"):
            service.get(
                principal=principal,
                connection_id="connection-example",
            )


def test_admin_or_explicit_viewer_can_read() -> None:
    service = SourceControlProjectionService(
        _Data(
            [
                _record(
                    owner_id="owner-example",
                    visible_subject_ids=frozenset({"viewer-example"}),
                )
            ]
        )
    )

    assert service.get(
        principal=_principal(
            subject_id="admin-example",
            roles=frozenset({"admin"}),
        ),
        connection_id="connection-example",
    )
    assert service.get(
        principal=_principal(subject_id="viewer-example"),
        connection_id="connection-example",
    )


def test_if_match_is_required_and_fail_closed() -> None:
    service = SourceControlProjectionService(_Data([_record()]))
    projection = service.get(
        principal=_principal(),
        connection_id="connection-example",
    )

    with pytest.raises(SourceControlProjectionError, match="if_match_required"):
        service.assert_if_match(projection, None)
    with pytest.raises(SourceControlProjectionError, match="version_conflict"):
        service.assert_if_match(projection, '"wrong"')
    service.assert_if_match(projection, f'"{projection.etag}"')


def test_cursor_limit_and_filter_are_bounded() -> None:
    service = SourceControlProjectionService(_Data([]))

    with pytest.raises(SourceControlProjectionError):
        service.list(principal=_principal(), limit=201)
    with pytest.raises(SourceControlProjectionError):
        service.list(principal=_principal(), cursor="../cursor")
    with pytest.raises(SourceControlProjectionError):
        service.list(
            principal=_principal(),
            filters={"raw_query": "secret"},
        )
