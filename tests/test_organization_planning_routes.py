from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from agent.routes import organization_planning as routes
from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.organization_planning_composition import (
    OrganizationPlanningCompositionError,
)
from agent.services.organization_track_planning_contract_service import (
    track_planning_result_digest,
)


@pytest.fixture()
def app() -> Flask:
    application = Flask(__name__)
    application.register_blueprint(routes.organization_planning_bp)
    return application


def _principal() -> OrganizationAccessPrincipal:
    return OrganizationAccessPrincipal(principal_id="operator-1", tenant_id="tenant-1")


def test_blueprint_exposes_only_scoped_planning_and_capability_ingress(app: Flask) -> None:
    rules = {(rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))) for rule in app.url_map.iter_rules()}

    assert ("/api/organizations/<organization_id>/planning", ("GET",)) in rules
    assert (
        "/api/organizations/<organization_id>/goals/<goal_id>/planning/category-research",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<category_revision_id>/derive-tracks",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<category_revision_id>/track-planning",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<category_revision_id>/reference-workflows/<workflow_key>/preview",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<category_revision_id>/reference-workflows/<workflow_key>/derive",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<track_revision_id>/materialize",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<track_revision_id>/tasks/<plan_task_id>/dispatch-next",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/dispatches/<dispatch_intent_id>/retry",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/dispatches/pump",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<artifact_revision_id>/promote",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/planning/<artifact_revision_id>/adopt",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/proposals/<proposal_id>/approve",
        ("POST",),
    ) in rules
    assert (
        "/api/organizations/<organization_id>/proposals/<proposal_id>/reject",
        ("POST",),
    ) in rules
    assert (
        "/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/proposals",
        ("POST",),
    ) in rules
    assert (
        "/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/planning/category",
        ("POST",),
    ) in rules
    assert (
        "/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/planning/tracks",
        ("POST",),
    ) in rules
    assert not any("followup" in path or path.endswith("/tasks") for path, _methods in rules)


def test_scoped_read_forwards_cursor_and_hides_foreign_ids(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeComposition:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def get_planning(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if kwargs["organization_id"] == "foreign-org":
                raise OrganizationPlanningCompositionError(
                    "organization_planning_not_found",
                    status_code=404,
                )
            return {
                "organization_id": kwargs["organization_id"],
                "definition_revision": "rev-1",
                "nodes": [],
                "proposals": [],
                "next_cursor": "next-opaque",
            }

    composition = FakeComposition()
    monkeypatch.setattr(routes, "get_organization_planning_composition", lambda: composition)
    monkeypatch.setattr(
        routes,
        "_operator_principal",
        lambda *_args, **_kwargs: _principal(),
    )

    with app.test_request_context("/api/organizations/org-1/planning?cursor=opaque&page_size=7"):
        response = routes.get_organization_planning.__wrapped__("org-1")
        assert response.get_json()["next_cursor"] == "next-opaque"
    assert composition.calls == [
        {
            "principal": _principal(),
            "organization_id": "org-1",
            "cursor": "opaque",
            "page_size": 7,
        }
    ]

    with app.test_request_context("/api/organizations/foreign-org/planning"):
        response, status_code = routes.get_organization_planning.__wrapped__("foreign-org")
        assert status_code == 404
        assert response.get_json() == {
            "error": "organization_planning_not_found",
            "reason_code": "organization_planning_not_found",
        }


def test_mutations_require_revision_header_and_exact_digest(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeComposition:
        def transition_artifact(self, **_kwargs: Any) -> tuple[dict[str, Any], int]:
            raise AssertionError("composition must not run without the precondition")

    monkeypatch.setattr(routes, "get_organization_planning_composition", lambda: FakeComposition())
    monkeypatch.setattr(
        routes,
        "_operator_principal",
        lambda *_args, **_kwargs: _principal(),
    )

    with app.test_request_context(
        "/api/organizations/org-1/planning/category-r1/promote",
        method="POST",
        json={"expected_revision": "1", "expected_digest": "a" * 64},
    ):
        response, status_code = routes.promote_organization_planning_artifact.__wrapped__("org-1", "category-r1")
    assert status_code == 428
    assert response.get_json()["reason_code"] == "organization_planning_precondition_required"


def test_worker_ingress_rejects_normal_bearers_and_accepts_only_closed_carrier(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = app.test_client()
    endpoint = "/api/worker-results/tasks/task-1/assignments/assignment-1/proposals"

    denied = client.post(
        endpoint,
        headers={"Authorization": "Bearer ordinary-user-or-admin-token"},
        json={"schema": "worker_task_proposals.v1", "payload_digest": "sha256:" + "0" * 64, "proposals": []},
    )
    assert denied.status_code == 401
    assert denied.get_json()["reason_code"] == "worker_result_capability_required"

    class FakeCapabilityService:
        def verify(self, token: str, *, source_task_id: str, assignment_id: str) -> dict[str, Any]:
            assert token == "wrc1.bound.signature"
            assert source_task_id == "task-1"
            assert assignment_id == "assignment-1"
            return {
                "worker_id": "worker-1",
                "source_task_id": source_task_id,
                "assignment_id": assignment_id,
                "dispatch_lease_id": "lease-1",
                "scopes": ["worker.result.submit", "worker.task_proposal.submit"],
            }

    carrier = {
        "schema": "worker_task_proposals.v1",
        "payload_digest": "sha256:" + "1" * 64,
        "proposals": [{"proposal_id": "proposal-1"}],
    }
    monkeypatch.setattr(routes, "WorkerResultCapabilityService", FakeCapabilityService)
    monkeypatch.setattr(
        routes,
        "ingest_callback_task_proposals",
        lambda **kwargs: [
            {
                "proposal_id": kwargs["callback_payload"]["task_proposals"]["proposals"][0]["proposal_id"],
                "proposal_revision": 1,
                "proposal_digest": "sha256:" + "2" * 64,
                "payload_digest": "sha256:" + "3" * 64,
                "state": "submitted",
                "replayed": False,
                "task_created": False,
                "queue_write": False,
            }
        ],
    )

    accepted = client.post(
        endpoint,
        headers={"Authorization": "Bearer wrc1.bound.signature"},
        json=carrier,
    )
    assert accepted.status_code == 202
    payload = accepted.get_json()
    assert payload["proposals"][0]["revision"] == "1"
    assert payload["proposals"][0]["digest"] == "sha256:" + "2" * 64
    assert payload["proposals"][0]["status"] == "pending"
    assert payload["task_created"] is False
    assert payload["queue_write"] is False

    open_carrier = client.post(
        endpoint,
        headers={"Authorization": "Bearer wrc1.bound.signature"},
        json={**carrier, "followups": []},
    )
    assert open_carrier.status_code == 422
    assert open_carrier.get_json()["reason_code"] == "worker_task_proposals_carrier_invalid"


def test_category_research_catalog_selector_is_closed() -> None:
    binding = {
        "catalog_task_id": "catalog-task-1",
        "catalog_id": "catalog-1",
        "catalog_hash": "a" * 64,
        "repository_revision": "revision-1",
        "manifest_hash": "b" * 64,
        "source_allowlist_version": "a" * 64,
        "source_scope": "organization:org-1",
    }

    assert routes._source_catalog_binding(binding) == binding
    with pytest.raises(OrganizationPlanningCompositionError):
        routes._source_catalog_binding({**binding, "source_ids": ["SRC_0001"]})


def test_dispatch_pump_forwards_only_scoped_limit(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeComposition:
        def pump_dispatches(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs == {
                "principal": _principal(),
                "organization_id": "org-1",
                "limit": 7,
            }
            return {
                "organization_id": "org-1",
                "processed_count": 0,
                "dispatches": [],
            }

    monkeypatch.setattr(
        routes,
        "get_organization_planning_composition",
        lambda: FakeComposition(),
    )
    monkeypatch.setattr(
        routes,
        "_operator_principal",
        lambda *_args, **_kwargs: _principal(),
    )

    with app.test_request_context(
        "/api/organizations/org-1/planning/dispatches/pump",
        method="POST",
        json={"limit": 7},
    ):
        response = routes.pump_organization_planning_dispatches.__wrapped__("org-1")
    assert response.get_json()["processed_count"] == 0


def test_track_planning_task_creation_forwards_exact_promoted_revision_scope(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeComposition:
        def create_track_planning_task(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs == {
                "principal": _principal(),
                "organization_id": "org-1",
                "category_revision_id": "category-r1",
                "expected_revision": 2,
                "expected_digest": "a" * 64,
                "expected_policy_hash": "b" * 64,
                "unit_id": "unit-1",
                "team_id": "team-1",
                "role_slot_id": "slot-1",
                "source_category_item_ids": ["ITEM-A", "ITEM-B"],
                "idempotency_key": "track-task-key-1",
            }
            return {
                "task_id": "ptracktask-1",
                "task_kind": "planning_track_task",
                "category_revision_id": "category-r1",
                "source_category_item_ids": ["ITEM-A", "ITEM-B"],
                "replayed": False,
                "materialized_task_ids": [],
            }

    monkeypatch.setattr(
        routes,
        "get_organization_planning_composition",
        lambda: FakeComposition(),
    )
    monkeypatch.setattr(
        routes,
        "_operator_principal",
        lambda *_args, **_kwargs: _principal(),
    )
    with app.test_request_context(
        "/api/organizations/org-1/planning/category-r1/track-planning",
        method="POST",
        headers={
            "If-Match": f'"2:{"a" * 64}"',
            "Idempotency-Key": "track-task-key-1",
        },
        json={
            "expected_revision": 2,
            "expected_digest": "a" * 64,
            "expected_policy_hash": "b" * 64,
            "unit_id": "unit-1",
            "team_id": "team-1",
            "role_slot_id": "slot-1",
            "source_category_item_ids": ["ITEM-A", "ITEM-B"],
        },
    ):
        response, status_code = routes.create_organization_track_planning_task.__wrapped__(
            "org-1",
            "category-r1",
        )
    assert status_code == 201
    assert response.get_json()["materialized_task_ids"] == []


def test_track_planning_result_requires_closed_digest_bound_carrier(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCapabilityService:
        def verify(
            self,
            token: str,
            *,
            source_task_id: str,
            assignment_id: str,
        ) -> dict[str, Any]:
            assert token == "wrc1.bound.signature"
            return {
                "worker_id": "worker-1",
                "source_task_id": source_task_id,
                "assignment_id": assignment_id,
                "dispatch_lease_id": "lease-1",
                "scopes": ["worker.result.submit", "worker.task_proposal.submit"],
            }

    class FakeComposition:
        def accept_track_planning_result(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["source_task_id"] == "task-1"
            assert kwargs["assignment_id"] == "assignment-1"
            assert kwargs["idempotency_key"] == "track-result-key-1"
            assert kwargs["carrier"]["category_revision_id"] == "category-r1"
            return {
                "category_revision_id": "category-r1",
                "track_revisions": [{"artifact_revision_id": "track-r1"}],
                "replayed": False,
                "materialized_task_ids": [],
                "task_created": False,
                "queue_write": False,
            }

    carrier = {
        "schema": "organization_track_planning_result.v1",
        "payload_digest": "",
        "category_revision_id": "category-r1",
        "source_category_item_ids": ["ITEM-A"],
        "track_candidates": [
            {
                "artifact_id": "track-a",
                "payload": {"source_category_item_ids": ["ITEM-A"]},
            }
        ],
        "exclusions": {},
    }
    carrier["payload_digest"] = track_planning_result_digest(carrier)
    monkeypatch.setattr(routes, "WorkerResultCapabilityService", FakeCapabilityService)
    monkeypatch.setattr(
        routes,
        "get_organization_planning_composition",
        lambda: FakeComposition(),
    )
    client = app.test_client()
    endpoint = "/api/worker-results/tasks/task-1/assignments/assignment-1/planning/tracks"
    response = client.post(
        endpoint,
        headers={
            "Authorization": "Bearer wrc1.bound.signature",
            "Idempotency-Key": "track-result-key-1",
        },
        json=carrier,
    )
    assert response.status_code == 201
    assert response.get_json()["materialized_task_ids"] == []
    assert response.get_json()["task_created"] is False

    rejected = client.post(
        endpoint,
        headers={
            "Authorization": "Bearer wrc1.bound.signature",
            "Idempotency-Key": "track-result-key-1",
        },
        json={**carrier, "routing": {"worker_id": "attacker"}},
    )
    assert rejected.status_code == 422
    assert rejected.get_json()["reason_code"] == ("track_planning_result_carrier_invalid")
