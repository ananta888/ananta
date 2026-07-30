from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceControlOperationDB,
    SourceRevisionDB,
)
from agent.services.context_policy_lifecycle import (
    ContextPolicyPreview,
    ContextPolicyVersion,
)
from agent.services.source_control_grant_admin import (
    GrantAdminActor,
    GrantCreateRequest,
    GrantRevokeRequest,
    SourceControlGrantAdminError,
    SourceControlGrantAdminService,
)
from ananta_contracts.source_control import DestinationDescriptor


_NOW = 1_800_000_000.0
_TENANT = "tenant-example"
_PROJECT = "project-example"
_CONNECTION_ID = "conn_" + hashlib.sha256(b"connection").hexdigest()
_SOURCE_REVISION_ID = "srev_" + hashlib.sha256(b"revision").hexdigest()
_POLICY_DIGEST = hashlib.sha256(b"policy").hexdigest()
_POLICY_ETAG = hashlib.sha256(b"policy-etag").hexdigest()


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _destination() -> DestinationDescriptor:
    return DestinationDescriptor.create(
        worker_id="worker-example",
        worker_kind="worker",
        runtime_id="runtime-example",
        runtime_kind="ollama",
        provider_id="ollama",
        model_id="code-model",
        model_class="code",
        provider_location="private_network",
        data_residency="eu",
    )


class _Destinations:
    def __init__(self, descriptor: DestinationDescriptor) -> None:
        self.descriptor = descriptor
        self.available = True
        self.calls: list[tuple[str, str, str]] = []

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> DestinationDescriptor | None:
        self.calls.append((tenant_id, project_id, destination_id))
        if (
            self.available
            and tenant_id == _TENANT
            and project_id == _PROJECT
            and destination_id == self.descriptor.destination_id
        ):
            return self.descriptor
        return None


class _Policies:
    def __init__(self) -> None:
        self.decision = "allow"
        self.calls: list[tuple[str, str, str]] = []
        self.policy = ContextPolicyVersion(
            policy_id="code-review",
            version=3,
            tenant_id=_TENANT,
            project_id=_PROJECT,
            state="active",
            document={"policy_id": "code-review"},
            policy_digest=_POLICY_DIGEST,
            etag=_POLICY_ETAG,
            created_by="policy-admin",
            created_at="2027-01-15T08:00:00Z",
        )

    def active(self, *, actor, policy_id: str) -> ContextPolicyVersion:
        self.calls.append(
            ("active", actor.tenant_id, actor.project_id)
        )
        if (
            actor.tenant_id != _TENANT
            or actor.project_id != _PROJECT
            or policy_id != self.policy.policy_id
        ):
            raise AssertionError("unscoped policy lookup")
        return self.policy

    def preview(
        self,
        *,
        actor,
        policy_id: str,
        version: int,
        source_revision_id: str,
        destination_id: str,
        operation,
        transformation,
    ) -> ContextPolicyPreview:
        self.calls.append(
            ("preview", actor.tenant_id, actor.project_id)
        )
        assert policy_id == self.policy.policy_id
        assert version == self.policy.version
        assert source_revision_id == _SOURCE_REVISION_ID
        assert destination_id == _destination().destination_id
        assert operation.value == "index"
        assert transformation.value == "redacted"
        return ContextPolicyPreview(
            decision=self.decision,
            reason_codes=("test_policy",),
            matched_rule_path=("allow_index",),
            approval_requirement=None,
            policy_digest=self.policy.policy_digest,
        )


def _seed(engine) -> None:
    with Session(engine) as db:
        db.add(
            SourceConnectionDB(
                connection_id=_CONNECTION_ID,
                tenant_id=_TENANT,
                project_id=_PROJECT,
                owner_id="source-owner",
                connector_type="git",
                connection_identity_digest=hashlib.sha256(
                    b"identity"
                ).hexdigest(),
                display_name="Example",
                sensitivity="internal",
                state="active",
                lock_version=1,
                created_at_epoch=_NOW - 100,
                updated_at_epoch=_NOW - 100,
            )
        )
        db.add(
            SourceRevisionDB(
                source_revision_id=_SOURCE_REVISION_ID,
                connection_id=_CONNECTION_ID,
                tenant_id=_TENANT,
                project_id=_PROJECT,
                owner_id="source-owner",
                connector_type="git",
                sensitivity="internal",
                revision_token="main",
                revision_digest=hashlib.sha256(b"content").hexdigest(),
                content_manifest_id="manifest_"
                + hashlib.sha256(b"manifest").hexdigest(),
                content_manifest_digest=hashlib.sha256(
                    b"manifest-content"
                ).hexdigest(),
                admission_state="admitted",
                captured_at_epoch=_NOW - 50,
            )
        )
        db.add(
            ContextPolicyVersionDB(
                record_id=hashlib.sha256(b"record").hexdigest(),
                tenant_id=_TENANT,
                project_id=_PROJECT,
                policy_id="code-review",
                version=3,
                state="active",
                document_json={"policy_id": "code-review"},
                policy_digest=_POLICY_DIGEST,
                etag=_POLICY_ETAG,
                created_by="policy-admin",
                created_at="2027-01-15T08:00:00Z",
                updated_by="policy-admin",
                updated_at="2027-01-15T08:00:00Z",
            )
        )
        db.commit()


def _actor() -> GrantAdminActor:
    return GrantAdminActor(
        subject_id="grant-admin",
        tenant_id=_TENANT,
        project_id=_PROJECT,
        roles=frozenset({"project_owner"}),
    )


def _request(duration_seconds: int = 3_600) -> GrantCreateRequest:
    return GrantCreateRequest(
        source_revision_id=_SOURCE_REVISION_ID,
        destination_id=_destination().destination_id,
        policy_id="code-review",
        preset_id="worker_index_redacted",
        duration_seconds=duration_seconds,
    )


def _service():
    engine = _engine()
    _seed(engine)
    destinations = _Destinations(_destination())
    policies = _Policies()
    service = SourceControlGrantAdminService(
        engine=engine,
        destinations=destinations,
        policies=policies,
        clock=lambda: _NOW,
    )
    return engine, destinations, policies, service


def test_create_resolves_scope_policy_and_replays_atomically() -> None:
    engine, destinations, policies, service = _service()

    created = service.create_grant(
        actor=_actor(),
        request=_request(),
        if_match=f'"{_POLICY_ETAG}"',
        idempotency_key="create-example",
    )
    destinations.available = False
    replay = service.create_grant(
        actor=_actor(),
        request=_request(),
        if_match=_POLICY_ETAG,
        idempotency_key="create-example",
    )

    assert created == replay
    assert created.grant_id.startswith("grant_")
    assert created.grant_family_id.startswith("grfam_")
    assert created.version == 1
    assert created.state == "active"
    assert created.operation == "index"
    assert created.transformation == "redacted"
    assert created.policy_version.startswith("cpv_")
    assert destinations.calls == [
        (_TENANT, _PROJECT, _destination().destination_id)
    ]
    assert policies.calls == [
        ("active", _TENANT, _PROJECT),
        ("preview", _TENANT, _PROJECT),
    ]
    with Session(engine) as db:
        assert len(list(db.exec(select(SourceAccessGrantDB)).all())) == 1
        assert (
            len(list(db.exec(select(SourceAccessGrantAuditDB)).all()))
            == 1
        )
        assert (
            len(list(db.exec(select(SourceControlOperationDB)).all()))
            == 1
        )


def test_create_rejects_security_coordinates_stale_policy_and_denial() -> None:
    _, _, policies, service = _service()
    with pytest.raises(
        SourceControlGrantAdminError, match="grant_admin_required"
    ):
        service.create_grant(
            actor=GrantAdminActor(
                subject_id="viewer",
                tenant_id=_TENANT,
                project_id=_PROJECT,
                roles=frozenset({"viewer"}),
            ),
            request=_request(),
            if_match=_POLICY_ETAG,
            idempotency_key="viewer-create",
        )
    with pytest.raises(
        SourceControlGrantAdminError,
        match="grant_create_fields_invalid",
    ):
        GrantCreateRequest.from_mapping(
            {
                "source_revision_id": _SOURCE_REVISION_ID,
                "destination_id": _destination().destination_id,
                "policy_id": "code-review",
                "preset_id": "worker_index_redacted",
                "duration_seconds": 3_600,
                "tenant_id": "client-asserted",
                "worker_id": "client-target",
            }
        )
    with pytest.raises(
        SourceControlGrantAdminError,
        match="grant_policy_version_conflict",
    ):
        service.create_grant(
            actor=_actor(),
            request=_request(),
            if_match=hashlib.sha256(b"stale").hexdigest(),
            idempotency_key="stale-policy",
        )
    policies.decision = "deny"
    with pytest.raises(
        SourceControlGrantAdminError, match="grant_policy_denied"
    ):
        service.create_grant(
            actor=_actor(),
            request=_request(),
            if_match=_POLICY_ETAG,
            idempotency_key="denied-policy",
        )


def test_list_is_scope_bound_and_cursor_is_opaque() -> None:
    engine, _, _, service = _service()
    first = service.create_grant(
        actor=_actor(),
        request=_request(),
        if_match=_POLICY_ETAG,
        idempotency_key="version-one",
    )
    second = service.create_grant(
        actor=_actor(),
        request=_request(duration_seconds=7_200),
        if_match=_POLICY_ETAG,
        idempotency_key="version-two",
    )
    with Session(engine) as db:
        db.add(
            SourceAccessGrantDB(
                grant_id="grant_" + hashlib.sha256(b"foreign").hexdigest(),
                grant_family_id="grfam_"
                + hashlib.sha256(b"foreign-family").hexdigest(),
                grant_version=1,
                tenant_id="other-tenant",
                project_id=_PROJECT,
                owner_id="foreign-admin",
                source_revision_id=_SOURCE_REVISION_ID,
                destination_id=_destination().destination_id,
                operation="index",
                transformation="redacted",
                purpose="knowledge_index",
                policy_version="cpv_"
                + hashlib.sha256(b"foreign-policy").hexdigest(),
                state="active",
                issued_at_epoch=_NOW,
                expires_at_epoch=_NOW + 3_600,
                lock_version=1,
                updated_at_epoch=_NOW,
            )
        )
        db.commit()

    page_one = service.list_grants(actor=_actor(), limit=1)
    page_two = service.list_grants(
        actor=_actor(), limit=1, cursor=page_one.next_cursor
    )

    assert page_one.next_cursor is not None
    assert page_two.next_cursor is None
    assert {page_one.items[0].grant_id, page_two.items[0].grant_id} == {
        first.grant_id,
        second.grant_id,
    }
    assert {page_one.items[0].state, page_two.items[0].state} == {
        "active",
        "superseded",
    }


def test_revoke_is_cas_protected_audited_and_idempotent() -> None:
    engine, _, _, service = _service()
    created = service.create_grant(
        actor=_actor(),
        request=_request(),
        if_match=_POLICY_ETAG,
        idempotency_key="create-for-revoke",
    )

    revoked = service.revoke_grant(
        actor=_actor(),
        grant_id=created.grant_id,
        request=GrantRevokeRequest(reason_code="access_no_longer_needed"),
        if_match=created.etag,
        idempotency_key="revoke-example",
    )
    replay = service.revoke_grant(
        actor=_actor(),
        grant_id=created.grant_id,
        request=GrantRevokeRequest(reason_code="access_no_longer_needed"),
        if_match=created.etag,
        idempotency_key="revoke-example",
    )

    assert revoked == replay
    assert revoked.state == "revoked"
    assert revoked.etag != created.etag
    with pytest.raises(
        SourceControlGrantAdminError, match="grant_not_active"
    ):
        service.revoke_grant(
            actor=_actor(),
            grant_id=created.grant_id,
            request=GrantRevokeRequest(
                reason_code="second_revoke_attempt"
            ),
            if_match=created.etag,
            idempotency_key="different-revoke",
        )
    with Session(engine) as db:
        actions = {
            row.action
            for row in db.exec(select(SourceAccessGrantAuditDB)).all()
        }
    assert actions == {"create", "revoke"}


def test_preset_catalog_is_read_only_and_requires_no_target_coordinates() -> None:
    _, _, _, service = _service()

    presets = service.list_presets(actor=_actor())

    assert isinstance(presets, tuple)
    assert {preset.preset_id for preset in presets} == {
        "worker_index_redacted",
        "chat_context_redacted",
        "tool_read_summary",
    }
    assert all(
        set(preset.to_dict())
        == {
            "schema",
            "preset_id",
            "label",
            "description",
            "operation",
            "transformation",
            "purpose",
            "max_duration_seconds",
        }
        for preset in presets
    )
    with pytest.raises(FrozenInstanceError):
        presets[0].purpose = "client_override"  # type: ignore[misc]
