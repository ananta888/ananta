from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from agent.db_models.context_policy_lifecycle import (
    ContextPolicyLifecycleAuditDB,
    ContextPolicyMutationDB,
    ContextPolicyVersionDB,
)
from agent.repositories.context_policy_lifecycle_repository import (
    SQLContextPolicyLifecycleRepository,
)
from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyLifecycleError,
    ContextPolicyLifecycleService,
    ContextPolicyPreview,
    derive_context_policy_digest,
)
from agent.services.context_policy_lifecycle_composition import (
    ExistingContextPolicyLintAdapter,
    ExistingContextPolicyPreviewAdapter,
    RequiredIdempotencyContextPolicyLifecycleService,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)


NOW = "2026-01-01T00:00:00+00:00"


class _Lint:
    def lint(self, **_kwargs):
        return ()


class _Preview:
    def preview(self, *, policy_document, **_kwargs):
        return ContextPolicyPreview(
            decision="deny",
            reason_codes=("default_deny",),
            matched_rule_path=(),
            approval_requirement=None,
            policy_digest=derive_context_policy_digest(
                policy_document
            ),
        )


def _actor(project_id="project-alpha"):
    return ContextPolicyActor(
        subject_id="owner-alpha",
        tenant_id="tenant-alpha",
        project_id=project_id,
        roles=frozenset({"project_owner"}),
    )


def _document(default=False):
    return {
        "schema": "ananta.context-access-policy.v1",
        "policy_id": "policy-alpha",
        "scope": "project",
        "defaults": {"send_allowed": default},
        "rules": [],
        "precedence": 10,
    }


def _service(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'context-policy.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ContextPolicyVersionDB.__table__,
            ContextPolicyMutationDB.__table__,
            ContextPolicyLifecycleAuditDB.__table__,
        ],
    )
    repository = SQLContextPolicyLifecycleRepository(
        engine,
        clock=lambda: NOW,
    )
    lifecycle = ContextPolicyLifecycleService(
        repository=repository,
        lint=_Lint(),
        preview=_Preview(),
        audit=repository,
    )
    return (
        engine,
        RequiredIdempotencyContextPolicyLifecycleService(
            lifecycle,
            clock=lambda: NOW,
        ),
    )


def test_mutations_are_idempotent_and_payload_conflicts_fail(
    tmp_path,
) -> None:
    _engine, service = _service(tmp_path)
    draft = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(),
        expected_latest_version=None,
        idempotency_key="draft-alpha",
    )
    replay = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(),
        expected_latest_version=None,
        idempotency_key="draft-alpha",
    )

    assert replay == draft
    with pytest.raises(
        ContextPolicyLifecycleError,
        match="idempotency_conflict",
    ):
        service.create_draft(
            actor=_actor(),
            policy_id="policy-alpha",
            document=_document(default=True),
            expected_latest_version=None,
            idempotency_key="draft-alpha",
        )
    with pytest.raises(
        ContextPolicyLifecycleError,
        match="idempotency_key_required",
    ):
        service.create_draft(
            actor=_actor(),
            policy_id="policy-alpha",
            document=_document(),
            expected_latest_version=1,
            idempotency_key="",
        )


def test_activation_supersedes_prior_snapshot_and_replay_is_stable(
    tmp_path,
) -> None:
    engine, service = _service(tmp_path)
    first = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(),
        expected_latest_version=None,
        idempotency_key="draft-one",
    )
    first_active = service.activate(
        actor=_actor(),
        policy_id="policy-alpha",
        version=first.version,
        if_match=f'"{first.etag}"',
        idempotency_key="activate-one",
    )
    second = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(default=True),
        expected_latest_version=1,
        idempotency_key="draft-two",
    )
    second_active = service.activate(
        actor=_actor(),
        policy_id="policy-alpha",
        version=second.version,
        if_match=f'W/"{second.etag}"',
        idempotency_key="activate-two",
    )
    replay = service.activate(
        actor=_actor(),
        policy_id="policy-alpha",
        version=first.version,
        if_match=f'"{first.etag}"',
        idempotency_key="activate-one",
    )

    assert replay == first_active
    assert service.active(
        actor=_actor(),
        policy_id="policy-alpha",
    ) == second_active
    assert service.detail(
        actor=_actor(),
        policy_id="policy-alpha",
        version=1,
    ).state == "superseded"
    with Session(engine) as session:
        active_rows = session.exec(
            select(ContextPolicyVersionDB).where(
                ContextPolicyVersionDB.state == "active"
            )
        ).all()
    assert len(active_rows) == 1


def test_revoke_and_stale_versions_fail_closed_for_preview(
    tmp_path,
) -> None:
    _engine, service = _service(tmp_path)
    first = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(),
        expected_latest_version=None,
        idempotency_key="draft-one",
    )
    active = service.activate(
        actor=_actor(),
        policy_id="policy-alpha",
        version=first.version,
        if_match=first.etag,
        idempotency_key="activate-one",
    )
    revoked = service.revoke(
        actor=_actor(),
        policy_id="policy-alpha",
        version=active.version,
        if_match=active.etag,
        idempotency_key="revoke-one",
    )

    assert revoked.state == "revoked"
    with pytest.raises(
        ContextPolicyLifecycleError,
        match="active_policy_snapshot_missing",
    ):
        service.active(
            actor=_actor(),
            policy_id="policy-alpha",
        )
    with pytest.raises(
        ContextPolicyLifecycleError,
        match="policy_version_not_usable",
    ):
        service.preview(
            actor=_actor(),
            policy_id="policy-alpha",
            version=revoked.version,
            source_revision_id="srev-" + "a" * 64,
            destination_id="dst-" + "b" * 64,
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
        )


def test_rollback_creates_new_draft_without_mutating_history(
    tmp_path,
) -> None:
    _engine, service = _service(tmp_path)
    first = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(),
        expected_latest_version=None,
        idempotency_key="draft-one",
    )
    active = service.activate(
        actor=_actor(),
        policy_id="policy-alpha",
        version=first.version,
        if_match=first.etag,
        idempotency_key="activate-one",
    )
    second = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(default=True),
        expected_latest_version=1,
        idempotency_key="draft-two",
    )
    rolled_back = service.rollback(
        actor=_actor(),
        policy_id="policy-alpha",
        target_version=active.version,
        expected_latest_version=second.version,
        idempotency_key="rollback-one",
    )

    assert rolled_back.version == 3
    assert rolled_back.state == "draft"
    assert rolled_back.document == active.document
    assert service.detail(
        actor=_actor(),
        policy_id="policy-alpha",
        version=2,
    ) == second
    first_page, cursor = service.versions(
        actor=_actor(),
        policy_id="policy-alpha",
        limit=2,
    )
    second_page, final_cursor = service.versions(
        actor=_actor(),
        policy_id="policy-alpha",
        cursor=cursor,
        limit=2,
    )
    assert [item.version for item in first_page] == [3, 2]
    assert [item.version for item in second_page] == [1]
    assert final_cursor is None


def test_scope_and_cas_conflicts_do_not_leak_versions(tmp_path) -> None:
    _engine, service = _service(tmp_path)
    draft = service.create_draft(
        actor=_actor(),
        policy_id="policy-alpha",
        document=_document(),
        expected_latest_version=None,
        idempotency_key="draft-one",
    )

    with pytest.raises(
        ContextPolicyLifecycleError,
        match="version_conflict",
    ):
        service.activate(
            actor=_actor(),
            policy_id="policy-alpha",
            version=draft.version,
            if_match="stale",
            idempotency_key="activate-stale",
        )
    with pytest.raises(
        ContextPolicyLifecycleError,
        match="policy_not_found",
    ):
        service.detail(
            actor=_actor("project-other"),
            policy_id="policy-alpha",
            version=1,
        )


def test_composition_reuses_domain_lint_and_preview_with_real_facts() -> None:
    class Domain:
        def __init__(self):
            self.validated = False
            self.evaluated = False

        def validate_policy(self, _policy):
            self.validated = True
            return []

        def get_decision(self, _policy, block, destination):
            self.evaluated = True
            assert block["source_revision_id"] == "revision-known"
            assert destination.worker_id == "worker-known"
            return SimpleNamespace(
                decision="deny",
                reason_code="cloud_blocked",
                matched_rule_ids=("rule-deny-cloud",),
            )

    class Sources:
        def resolve(self, **scope):
            if scope["source_revision_id"] != "revision-known":
                return None
            return {
                "source_type": "repository",
                "sensitivity": "internal",
            }

    class Destinations:
        def resolve(self, **scope):
            if scope["destination_id"] != "destination-known":
                return None
            return {
                "worker_id": "worker-known",
                "worker_kind": "llm",
                "runtime_id": "runtime-known",
                "runtime_kind": "docker_container",
                "provider_id": "provider-known",
                "provider_location": "local",
                "model_id": "model-known",
            }

    domain = Domain()
    lint = ExistingContextPolicyLintAdapter(domain)
    preview = ExistingContextPolicyPreviewAdapter(
        service=domain,
        sources=Sources(),
        destinations=Destinations(),
    )

    assert lint.lint(document=_document()) == ()
    result = preview.preview(
        tenant_id="tenant-alpha",
        project_id="project-alpha",
        policy_document=_document(),
        source_revision_id="revision-known",
        destination_id="destination-known",
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
    )

    assert domain.validated is True
    assert domain.evaluated is True
    assert result.decision == "deny"
    assert result.matched_rule_path == ("rule-deny-cloud",)
    with pytest.raises(
        ContextPolicyLifecycleError,
        match="source_revision_not_found",
    ):
        preview.preview(
            tenant_id="tenant-alpha",
            project_id="project-alpha",
            policy_document=_document(),
            source_revision_id="revision-unknown",
            destination_id="destination-known",
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
        )
