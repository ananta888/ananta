from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask
from sqlalchemy.exc import IntegrityError

from agent.db_models import ApprovalRequestDB
from agent.routes.approvals import _decode_approval_cursor, _encode_approval_cursor
from agent.services.approval_request_service import (
    ApprovalDecisionError,
    ApprovalRequestService,
    canonical_approval_intent_key,
    canonicalize_tool_call,
    compute_arguments_digest,
)
from agent.services.planning_principal_identity_service import (
    canonical_planning_actor_id,
    canonical_planning_worker_id,
    planning_separation_of_duties_reason,
)


def _passive_request_inputs() -> dict[str, Any]:
    artifact_digest = "a" * 64
    policy_hash = "b" * 64
    arguments = {
        "operation": "track_adopt",
        "artifact_revision_id": "revision-1",
        "artifact_digest": artifact_digest,
        "policy_hash": policy_hash,
        "goal_id": "goal-1",
        "organization_id": "organization-1",
    }
    return {
        "tool_name": "planning.track.adopt",
        "approval_intent_key": canonical_approval_intent_key(
            tenant_id="tenant-1",
            project_id="project-1",
            organization_id="organization-1",
            goal_id="goal-1",
            operation="track_adopt",
            artifact_revision_id="revision-1",
            artifact_digest=artifact_digest,
            policy_hash=policy_hash,
        ),
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "organization_id": "organization-1",
        "goal_id": "goal-1",
        "arguments": arguments,
        "target_fingerprint": artifact_digest,
        "scope": {"operation": "track_adopt"},
    }


def _bound_request(**overrides: Any) -> ApprovalRequestDB:
    inputs = _passive_request_inputs()
    canonical, _, _ = canonicalize_tool_call(
        inputs["tool_name"],
        inputs["arguments"],
    )
    values = {
        "id": "approval-1",
        "tool_name": inputs["tool_name"],
        "approval_intent_key": inputs["approval_intent_key"],
        "tenant_id": inputs["tenant_id"],
        "project_id": inputs["project_id"],
        "organization_id": inputs["organization_id"],
        "goal_id": inputs["goal_id"],
        "canonical_arguments": canonical,
        "arguments_digest": compute_arguments_digest(
            inputs["tool_name"],
            canonical,
            inputs["target_fingerprint"],
        ),
        "target_fingerprint": inputs["target_fingerprint"],
    }
    values.update(overrides)
    return ApprovalRequestDB(**values)


class _OneResult:
    def __init__(self, value: ApprovalRequestDB | None) -> None:
        self._value = value

    def one_or_none(self) -> ApprovalRequestDB | None:
        return self._value


class _RacingSession:
    """Minimal session double for a writer that loses the unique-key race."""

    def __init__(self, authoritative: ApprovalRequestDB) -> None:
        self.authoritative = authoritative
        self.select_count = 0
        self.savepoint_rolled_back = False
        self.outer_rollback_called = False

    def exec(self, _statement: Any) -> _OneResult:
        self.select_count += 1
        return _OneResult(None if self.select_count == 1 else self.authoritative)

    @contextmanager
    def begin_nested(self):
        try:
            yield
        except IntegrityError:
            self.savepoint_rolled_back = True
            raise

    def add(self, _request: ApprovalRequestDB) -> None:
        return None

    def flush(self, _objects: Any = None) -> None:
        raise IntegrityError("INSERT", {}, RuntimeError("unique intent"))

    def rollback(self) -> None:
        self.outer_rollback_called = True


def test_passive_approval_race_rereads_authoritative_row_without_outer_rollback() -> None:
    authoritative = _bound_request()
    session = _RacingSession(authoritative)

    result = ApprovalRequestService().ensure_passive_request_in_session(
        session,  # type: ignore[arg-type]
        **_passive_request_inputs(),
    )

    assert result is authoritative
    assert session.savepoint_rolled_back is True
    assert session.outer_rollback_called is False


def test_passive_approval_race_rejects_conflicting_authoritative_row() -> None:
    session = _RacingSession(_bound_request(project_id="project-other"))

    with pytest.raises(ApprovalDecisionError) as caught:
        ApprovalRequestService().ensure_passive_request_in_session(
            session,  # type: ignore[arg-type]
            **_passive_request_inputs(),
        )

    assert caught.value.code == "approval_intent_conflict"
    assert session.savepoint_rolled_back is True
    assert session.outer_rollback_called is False


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("tool_name", "planning.category.promote"),
        ("tenant_id", "tenant-other"),
        ("project_id", "project-other"),
        ("organization_id", "organization-other"),
        ("goal_id", "goal-other"),
        ("canonical_arguments", {"operation": "different"}),
        ("arguments_digest", "c" * 64),
        ("target_fingerprint", "d" * 64),
    ],
)
def test_passive_approval_intent_replay_rejects_every_binding_mismatch(
    field: str,
    conflicting_value: Any,
) -> None:
    inputs = _passive_request_inputs()
    canonical, _, _ = canonicalize_tool_call(
        inputs["tool_name"],
        inputs["arguments"],
    )
    request = _bound_request(**{field: conflicting_value})

    with pytest.raises(ApprovalDecisionError) as caught:
        ApprovalRequestService._validate_passive_request_binding(
            request,
            tool_name=inputs["tool_name"],
            tenant_id=inputs["tenant_id"],
            project_id=inputs["project_id"],
            organization_id=inputs["organization_id"],
            goal_id=inputs["goal_id"],
            canonical_arguments=canonical,
            arguments_digest=compute_arguments_digest(
                inputs["tool_name"],
                canonical,
                inputs["target_fingerprint"],
            ),
            target_fingerprint=inputs["target_fingerprint"],
        )

    assert caught.value.code == "approval_intent_conflict"


def test_planning_sod_uses_canonical_actor_not_display_prefix() -> None:
    legacy_revision = SimpleNamespace(
        created_by_principal_id=None,
        created_by="hub:replan:alice",
        execution_provenance={},
    )

    assert (
        planning_separation_of_duties_reason(
            revision=legacy_revision,
            decided_by="alice",
        )
        == "planning_separation_of_duties_required"
    )


def test_planning_sod_prefers_persisted_creator_principal() -> None:
    revision = SimpleNamespace(
        created_by_principal_id=canonical_planning_actor_id("alice"),
        created_by="hub:replan:display-only",
        execution_provenance={"created_by_hub_actor": "display-only"},
    )

    assert (
        planning_separation_of_duties_reason(
            revision=revision,
            decided_by="alice",
        )
        == "planning_separation_of_duties_required"
    )


def test_planning_sod_recovers_hub_actor_from_legacy_provenance() -> None:
    legacy_revision = SimpleNamespace(
        created_by_principal_id=None,
        created_by="hub:proposal:proposal-1",
        execution_provenance={"created_by_hub_actor": "alice"},
    )

    assert (
        planning_separation_of_duties_reason(
            revision=legacy_revision,
            decided_by="alice",
        )
        == "planning_separation_of_duties_required"
    )


def test_planning_sod_namespaces_workers_separately_from_human_principals() -> None:
    revision = SimpleNamespace(
        created_by_principal_id=canonical_planning_worker_id("alice"),
        created_by="worker:alice",
        execution_provenance={},
    )

    assert canonical_planning_actor_id("alice") == "principal:alice"
    assert (
        planning_separation_of_duties_reason(
            revision=revision,
            decided_by="alice",
        )
        is None
    )


def test_planning_sod_fails_closed_for_ambiguous_legacy_creator() -> None:
    ambiguous_revision = SimpleNamespace(
        created_by_principal_id=None,
        created_by="hub",
        execution_provenance={},
    )

    assert (
        planning_separation_of_duties_reason(
            revision=ambiguous_revision,
            decided_by="operator-1",
        )
        == "planning_creator_principal_unverified"
    )


def test_approval_cursor_is_signed_and_bound_to_principal_and_scope() -> None:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "approval-cursor-test-secret"
    row = SimpleNamespace(id="approval-cursor-one", created_at=1234.5)
    scope = {
        "principal_id": "operator-one",
        "tenant_id": "tenant-one",
        "project_id": "project-one",
        "organization_id": "organization-one",
        "goal_id": None,
        "task_id": None,
        "status": "pending",
    }

    with app.app_context():
        cursor = _encode_approval_cursor(row, scope)
        assert _decode_approval_cursor(cursor, scope) == (1234.5, "approval-cursor-one")
        with pytest.raises(ValueError, match="approval_cursor_invalid"):
            _decode_approval_cursor(
                cursor,
                {**scope, "organization_id": "organization-two"},
            )
        with pytest.raises(ValueError, match="approval_cursor_invalid"):
            _decode_approval_cursor(
                ("A" if cursor[0] != "A" else "B") + cursor[1:],
                scope,
            )
