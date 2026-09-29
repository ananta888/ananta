from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyDiagnostic,
    ContextPolicyLifecycleError,
    ContextPolicyLifecycleService,
    ContextPolicyPreview,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation


class _Repository:
    def __init__(self) -> None:
        self.values = {}

    def latest(self, **scope):
        matches = [
            value
            for key, value in self.values.items()
            if key[0] == scope["policy_id"]
        ]
        return max(matches, key=lambda item: item.version) if matches else None

    def get_version(self, **scope):
        return self.values.get((scope["policy_id"], scope["version"]))

    def list_versions(self, **scope):
        return (
            sorted(
                [
                    value
                    for key, value in self.values.items()
                    if key[0] == scope["policy_id"]
                ],
                key=lambda item: item.version,
            ),
            None,
        )

    def append_draft(self, *, version, expected_latest_version):
        self.values[(version.policy_id, version.version)] = version
        return version

    def transition(self, *, policy_id, version, target_state, **kwargs):
        current = self.values[(policy_id, version)]
        transitioned = replace(
            current,
            state=target_state,
            etag="f" * 64,
        )
        self.values[(policy_id, version)] = transitioned
        return transitioned


class _Lint:
    def __init__(self, diagnostics=()) -> None:
        self.diagnostics = diagnostics

    def lint(self, **kwargs):
        return self.diagnostics


class _Preview:
    def preview(self, *, policy_document, **kwargs):
        import hashlib
        import json

        digest = hashlib.sha256(
            json.dumps(
                policy_document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return ContextPolicyPreview(
            decision="deny",
            reason_codes=("cloud_blocked",),
            matched_rule_path=("rule-example",),
            approval_requirement=None,
            policy_digest=digest,
        )


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, **event):
        self.events.append(event)


def _actor(*roles: str) -> ContextPolicyActor:
    return ContextPolicyActor(
        subject_id="actor-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset(roles or ("project_owner",)),
    )


def _document():
    return {
        "schema": "ananta.context-access-policy.v1",
        "policy_id": "policy-example",
        "scope": "project",
        "defaults": {"send_allowed": False},
        "rules": [],
        "precedence": 10,
    }


def _service(*, diagnostics=()):
    repository = _Repository()
    audit = _Audit()
    return (
        ContextPolicyLifecycleService(
            repository=repository,
            lint=_Lint(diagnostics),
            preview=_Preview(),
            audit=audit,
        ),
        repository,
        audit,
    )


def test_draft_version_is_server_derived_and_immutable() -> None:
    service, repository, audit = _service()

    first = service.create_draft(
        actor=_actor(),
        policy_id="policy-example",
        document=_document(),
        expected_latest_version=None,
        now="2026-01-01T00:00:00Z",
    )
    second = service.create_draft(
        actor=_actor(),
        policy_id="policy-example",
        document=_document(),
        expected_latest_version=1,
        now="2026-01-02T00:00:00Z",
    )

    assert (first.version, second.version) == (1, 2)
    assert repository.values[("policy-example", 1)] == first
    assert audit.events[0]["policy_digest"] == first.policy_digest


def test_activate_requires_clean_lint_and_if_match() -> None:
    service, _, _ = _service(
        diagnostics=(
            ContextPolicyDiagnostic(
                severity="error",
                reason_code="secret_cloud_allow",
            ),
        )
    )
    draft = service.create_draft(
        actor=_actor(),
        policy_id="policy-example",
        document=_document(),
        expected_latest_version=None,
        now="2026-01-01T00:00:00Z",
    )

    with pytest.raises(ContextPolicyLifecycleError, match="version_conflict"):
        service.activate(
            actor=_actor(),
            policy_id=draft.policy_id,
            version=draft.version,
            if_match="wrong",
        )
    with pytest.raises(ContextPolicyLifecycleError, match="lint_failed"):
        service.activate(
            actor=_actor(),
            policy_id=draft.policy_id,
            version=draft.version,
            if_match=draft.etag,
        )


def test_preview_uses_server_ids_and_candidate_digest() -> None:
    service, _, _ = _service()
    draft = service.create_draft(
        actor=_actor(),
        policy_id="policy-example",
        document=_document(),
        expected_latest_version=None,
        now="2026-01-01T00:00:00Z",
    )

    preview = service.preview(
        actor=_actor(),
        policy_id=draft.policy_id,
        version=draft.version,
        source_revision_id="revision-example",
        destination_id="destination-example",
        operation=GrantOperation.CHAT_CONTEXT,
        transformation=GrantTransformation.REDACTED,
    )

    assert preview.decision == "deny"
    assert preview.policy_digest == draft.policy_digest


def test_rollback_creates_new_draft_instead_of_mutating_history() -> None:
    service, repository, _ = _service()
    draft = service.create_draft(
        actor=_actor(),
        policy_id="policy-example",
        document=_document(),
        expected_latest_version=None,
        now="2026-01-01T00:00:00Z",
    )
    repository.values[(draft.policy_id, draft.version)] = replace(
        draft,
        state="superseded",
    )

    rolled_back = service.rollback(
        actor=_actor(),
        policy_id=draft.policy_id,
        target_version=1,
        expected_latest_version=1,
        now="2026-01-02T00:00:00Z",
    )

    assert rolled_back.version == 2
    assert rolled_back.state == "draft"
    assert repository.values[("policy-example", 1)].state == "superseded"


def test_regular_member_cannot_mutate_policy() -> None:
    service, _, _ = _service()

    with pytest.raises(ContextPolicyLifecycleError, match="forbidden"):
        service.create_draft(
            actor=_actor("member"),
            policy_id="policy-example",
            document=_document(),
            expected_latest_version=None,
            now="2026-01-01T00:00:00Z",
        )
