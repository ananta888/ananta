from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.db_models import OrganizationTopologyPatchGrantDB
from agent.models.organization_models import canonical_sha256
from agent.services.organization_topology_apply_service import (
    OrganizationTopologyApplyService,
    OrganizationTopologyPatchError,
)
from tests.test_organization_topology_apply_service import _preview, _state


class _Rows:
    def __init__(self, value=None) -> None:
        self.value = value
        self.added = []

    def add(self, row):
        self.added.append(row)
        return row

    def get_scoped(self, *_args, **_kwargs):
        return self.value

    def get_by_issue_idempotency_key(self, *_args, **_kwargs):
        return self.value

    def get_by_idempotency_key(self, *_args, **_kwargs):
        return self.value

    def list_for_organization(self, *_args, **_kwargs):
        return list(self.value or [])


class _Uow:
    def __init__(self) -> None:
        self.session = SimpleNamespace()
        self.definitions = SimpleNamespace()
        self.memberships = _Rows([])
        self.admin_grants = _Rows()
        self.topology_patch_grants = _Rows()
        self.operations = _Rows()
        self.instances = _Rows()
        self.audit_outbox = _Rows()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def flush(self) -> None:
        return None


def _grant(preview, **overrides) -> OrganizationTopologyPatchGrantDB:
    values = {
        "grant_id": "topology-grant-a",
        "tenant_id": preview.tenant_id,
        "project_id": preview.project_id,
        "organization_id": preview.organization_id,
        "principal_id": preview.principal_id,
        "parent_admin_grant_id": "parent-admin-a",
        "patch_digest": preview.patch_digest,
        "policy_hash": preview.effective_policy_hash,
        "limit_hash": preview.effective_limit_profile_hash,
        "expected_revision": preview.expected_revision,
        "issue_idempotency_key": "issue-topology-grant-a",
        "granted_by": preview.principal_id,
        "expires_at": 180.0,
    }
    values.update(overrides)
    return OrganizationTopologyPatchGrantDB(**values)


def _service(uow: _Uow, *, clock=100.0) -> OrganizationTopologyApplyService:
    return OrganizationTopologyApplyService(
        reader=SimpleNamespace(),
        limit_profiles=SimpleNamespace(),
        uow_factory=lambda: uow,
        clock=lambda: clock,
    )


def test_generic_admin_grant_is_rejected_by_topology_apply() -> None:
    preview = _preview(
        _state(),
        [{"op": "reparent", "node_id": "team-a", "parent_id": "stream-b"}],
    )
    uow = _Uow()

    with pytest.raises(OrganizationTopologyPatchError) as caught:
        _service(uow).apply(
            preview=preview,
            tenant_id=preview.tenant_id,
            project_id=preview.project_id,
            organization_id=preview.organization_id,
            principal_id=preview.principal_id,
            expected_revision=preview.expected_revision,
            expected_patch_digest=preview.patch_digest,
            idempotency_key="apply-topology-a",
            topology_patch_grant_id="long-lived-organization-admin-grant",
        )

    assert caught.value.reason_code == "organization_patch_grant_invalid"


def test_parent_authority_must_be_an_exact_organization_admin_grant() -> None:
    preview = _preview(
        _state(),
        [{"op": "reparent", "node_id": "team-a", "parent_id": "stream-b"}],
    )
    uow = _Uow()
    uow.memberships.value = [
        SimpleNamespace(
            principal_id=preview.principal_id,
            membership_kind="organization_admin",
            expires_at=None,
        )
    ]
    uow.admin_grants.value = SimpleNamespace(
        principal_id=preview.principal_id,
        grant_kind="*",
        revoked_at=None,
        expires_at=None,
    )

    with pytest.raises(OrganizationTopologyPatchError) as caught:
        _service(uow).issue_grant(
            preview=preview,
            tenant_id=preview.tenant_id,
            project_id=preview.project_id,
            organization_id=preview.organization_id,
            principal_id=preview.principal_id,
            expected_revision=preview.expected_revision,
            expected_patch_digest=preview.patch_digest,
            issue_idempotency_key="issue-topology-a",
            parent_admin_grant_id="wildcard-parent",
        )

    assert caught.value.reason_code == "organization_patch_parent_grant_invalid"


def test_issue_creates_short_lived_digest_bound_one_shot_grant() -> None:
    state = _state()
    preview = _preview(
        state,
        [{"op": "reparent", "node_id": "team-a", "parent_id": "stream-b"}],
    )
    uow = _Uow()
    uow.memberships.value = [
        SimpleNamespace(
            principal_id=preview.principal_id,
            membership_kind="organization_admin",
            expires_at=None,
        )
    ]
    uow.admin_grants.value = SimpleNamespace(
        principal_id=preview.principal_id,
        grant_kind="organization_admin",
        revoked_at=None,
        expires_at=None,
    )
    service = _service(uow)
    service._authoritative_preview = lambda *_args, **_kwargs: (preview, state)  # type: ignore[method-assign]

    result = service.issue_grant(
        preview=preview,
        tenant_id=preview.tenant_id,
        project_id=preview.project_id,
        organization_id=preview.organization_id,
        principal_id=preview.principal_id,
        expected_revision=preview.expected_revision,
        expected_patch_digest=preview.patch_digest,
        issue_idempotency_key="issue-topology-bound",
        parent_admin_grant_id="parent-admin-a",
    )

    issued = uow.topology_patch_grants.added[0]
    assert result.grant_kind == "topology_patch"
    assert result.grant_id == issued.grant_id
    assert issued.patch_digest == preview.patch_digest
    assert issued.policy_hash == preview.effective_policy_hash
    assert issued.limit_hash == preview.effective_limit_profile_hash
    assert issued.expected_revision == preview.expected_revision
    assert issued.expires_at == preview.expires_at_epoch
    assert issued.consumed_at is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("principal_id", "other-principal"),
        ("patch_digest", "a" * 64),
        ("policy_hash", "b" * 64),
        ("limit_hash", "c" * 64),
        ("expected_revision", "other-revision"),
    ],
)
def test_topology_grant_binding_fails_closed(field: str, value: str) -> None:
    preview = _preview(
        _state(),
        [{"op": "reparent", "node_id": "team-a", "parent_id": "stream-b"}],
    )
    grant = _grant(preview, **{field: value})

    with pytest.raises(OrganizationTopologyPatchError) as caught:
        OrganizationTopologyApplyService._validate_grant_binding(
            grant,
            preview=preview,
            principal_id=preview.principal_id,
        )

    assert caught.value.reason_code == "organization_patch_grant_binding_mismatch"


def test_consumed_grant_allows_only_exact_idempotent_replay() -> None:
    preview = _preview(
        _state(),
        [{"op": "reparent", "node_id": "team-a", "parent_id": "stream-b"}],
    )
    idempotency_key = "apply-topology-exact-replay"
    grant = _grant(
        preview,
        consumed_at=120.0,
        revoked_at=120.0,
        consumed_idempotency_key=idempotency_key,
    )
    request_digest = canonical_sha256(
        {
            "patch_digest": preview.patch_digest,
            "idempotency_key": idempotency_key,
            "topology_patch_grant_id": grant.grant_id,
            "principal_id": preview.principal_id,
        }
    )
    grant.consumed_request_digest = request_digest
    uow = _Uow()
    uow.topology_patch_grants.value = grant
    uow.operations.value = SimpleNamespace(
        request_digest=request_digest,
        plan_digest=preview.patch_digest,
        status="applied",
        result_json={
            "organization_id": preview.organization_id,
            "definition_revision": preview.expected_revision,
            "snapshot_hash": "snapshot-result",
            "patch_digest": preview.patch_digest,
            "applied_operations": 1,
            "replayed": False,
        },
    )

    result = _service(uow, clock=300.0).apply(
        preview=preview,
        tenant_id=preview.tenant_id,
        project_id=preview.project_id,
        organization_id=preview.organization_id,
        principal_id=preview.principal_id,
        expected_revision=preview.expected_revision,
        expected_patch_digest=preview.patch_digest,
        idempotency_key=idempotency_key,
        topology_patch_grant_id=grant.grant_id,
    )

    assert result.replayed is True


def test_fresh_apply_consumes_and_revokes_grant_in_aggregate_uow() -> None:
    state = _state()
    state.organization.lock_version = 0
    state.organization.updated_at = 0.0
    preview = _preview(
        state,
        [{"op": "reparent", "node_id": "team-a", "parent_id": "stream-b"}],
    )
    grant = _grant(preview)
    uow = _Uow()
    uow.topology_patch_grants.value = grant
    uow.memberships.value = [
        SimpleNamespace(
            principal_id=preview.principal_id,
            membership_kind="organization_admin",
            expires_at=None,
        )
    ]
    service = _service(uow)
    service._authoritative_preview = lambda *_args, **_kwargs: (preview, state)  # type: ignore[method-assign]
    service._stage_operations = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    service._stage_snapshot = lambda *_args, **_kwargs: "snapshot-result"  # type: ignore[method-assign]

    service.apply(
        preview=preview,
        tenant_id=preview.tenant_id,
        project_id=preview.project_id,
        organization_id=preview.organization_id,
        principal_id=preview.principal_id,
        expected_revision=preview.expected_revision,
        expected_patch_digest=preview.patch_digest,
        idempotency_key="apply-topology-fresh",
        topology_patch_grant_id=grant.grant_id,
    )

    assert grant.consumed_at == 100.0
    assert grant.revoked_at == 100.0
    assert grant.consumed_idempotency_key == "apply-topology-fresh"
    assert grant.consumed_request_digest
    assert grant in uow.topology_patch_grants.added
