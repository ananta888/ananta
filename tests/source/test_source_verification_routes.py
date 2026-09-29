from __future__ import annotations

from types import SimpleNamespace

from agent.services.source_control_access_policy import HubSourcePrincipal


class _ProjectAccess:
    def __init__(self, *, role: str = "viewer") -> None:
        self.role = role
        self.calls: list[dict] = []

    def require(self, **values):
        self.calls.append(dict(values))
        return SimpleNamespace(role=self.role)


class _OrganizationMembership:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[dict] = []

    def can_view(self, **values) -> bool:
        self.calls.append(dict(values))
        return self.allowed


def _scoped_task(*, owner: str = "owner-a") -> dict:
    return {
        "id": "T-SOURCE-SCOPED",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "history": [
            {
                "event_type": "task_ingested",
                "actor": owner,
                "details": {"source": "api"},
            }
        ],
        "verification_status": {"source_catalog": {"sources": []}},
    }


def test_source_verification_routes_success(client, app, admin_auth_header):
    tid = "T-SOURCE-VERIFY-1"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            verification_status={
                "source_catalog": {
                    "source_catalog_id": "catalog-1",
                    "source_catalog_hash": "abc123def4567890",
                    "sources": [
                        {
                            "source_id": "SRC_0001",
                            "source_type": "repo_file",
                            "path": "src/a.py",
                            "record_id": "r1",
                            "allowed_for_llm_scope": True,
                        },
                        {
                            "source_id": "SRC_0002",
                            "source_ref": {
                                "source_id": "SRC_0002",
                                "content": "NESTED_SECRET",
                            },
                            "source_type": "repo_file",
                            "path": "src/secret.py",
                            "record_id": "r2",
                            "allowed_for_llm_scope": False,
                            "content": "TOP_SECRET",
                            "text": "SECOND_SECRET",
                            "excerpt": "THIRD_SECRET",
                            "unexpected_body": "FOURTH_SECRET",
                        },
                    ],
                },
                "answer_verification": {
                    "citation_verification_status": "failed_policy_scope",
                    "answer_schema": "grounded_answer.v1",
                    "verified_claim_count": 1,
                    "unverified_claim_count": 1,
                    "failed_claims": [{"claim_id": "CLM_0002", "reason": "failed_policy_scope"}],
                },
            },
        )

    res_sources = client.get(f"/tasks/{tid}/sources", headers=admin_auth_header)
    assert res_sources.status_code == 200
    payload_sources = res_sources.json["data"]
    assert payload_sources["source_catalog_id"] == "catalog-1"
    assert payload_sources["catalog_hash"] == "abc123def4567890"
    assert payload_sources["source_count"] == 2
    blocked = [s for s in payload_sources["sources"] if s["source_id"] == "SRC_0002"][0]
    assert blocked["content_exposed"] is False
    assert blocked["redaction_reason"] == "blocked_by_policy_scope"
    assert "content" not in blocked
    assert "text" not in blocked
    assert "excerpt" not in blocked
    assert "unexpected_body" not in blocked
    assert "content" not in blocked["source_ref"]
    assert "SECRET" not in str(payload_sources)

    res_ver = client.get(f"/tasks/{tid}/answer-verification", headers=admin_auth_header)
    assert res_ver.status_code == 200
    payload_ver = res_ver.json["data"]
    assert payload_ver["status"] == "failed_policy_scope"
    assert payload_ver["answer_schema"] == "grounded_answer.v1"
    assert payload_ver["verified_claim_count"] == 1
    assert payload_ver["unverified_claim_count"] == 1


def test_source_verification_routes_404(client, admin_auth_header):
    res_sources = client.get("/tasks/does-not-exist/sources", headers=admin_auth_header)
    assert res_sources.status_code == 404

    res_ver = client.get("/tasks/does-not-exist/answer-verification", headers=admin_auth_header)
    assert res_ver.status_code == 404


def test_source_verification_route_requires_exact_scope_and_owner(
    client,
    app,
    user_auth_header,
    monkeypatch,
) -> None:
    task = _scoped_task()
    project_access = _ProjectAccess()
    monkeypatch.setattr(
        "agent.routes.tasks.task_source_access._get_task_payload",
        lambda _task_id: task,
    )
    monkeypatch.setattr(
        "agent.routes.tasks.task_source_access.get_authenticated_source_control_principal",
        lambda: HubSourcePrincipal(
            subject_id="other-user",
            tenant_id="tenant-a",
            project_id="project-a",
            roles=frozenset(),
        ),
    )
    app.extensions["project_access_authority"] = project_access

    response = client.get(
        "/tasks/T-SOURCE-SCOPED/sources",
        headers=user_auth_header,
    )

    assert response.status_code == 404
    assert len(project_access.calls) == 1


def test_source_verification_route_allows_scoped_task_owner(
    client,
    app,
    user_auth_header,
    monkeypatch,
) -> None:
    task = _scoped_task()
    project_access = _ProjectAccess()
    monkeypatch.setattr(
        "agent.routes.tasks.task_source_access._get_task_payload",
        lambda _task_id: task,
    )
    monkeypatch.setattr(
        "agent.routes.tasks.task_source_access.get_authenticated_source_control_principal",
        lambda: HubSourcePrincipal(
            subject_id="owner-a",
            tenant_id="tenant-a",
            project_id="project-a",
            roles=frozenset(),
        ),
    )
    app.extensions["project_access_authority"] = project_access

    response = client.get(
        "/tasks/T-SOURCE-SCOPED/sources",
        headers=user_auth_header,
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["source_count"] == 0


def test_source_verification_route_requires_current_organization_membership(
    client,
    app,
    user_auth_header,
    monkeypatch,
) -> None:
    task = {**_scoped_task(), "organization_id": "org-a"}
    project_access = _ProjectAccess()
    organization_membership = _OrganizationMembership(allowed=False)
    monkeypatch.setattr(
        "agent.routes.tasks.task_source_access._get_task_payload",
        lambda _task_id: task,
    )
    monkeypatch.setattr(
        "agent.routes.tasks.task_source_access.get_authenticated_source_control_principal",
        lambda: HubSourcePrincipal(
            subject_id="owner-a",
            tenant_id="tenant-a",
            project_id="project-a",
            roles=frozenset(),
        ),
    )
    app.extensions["project_access_authority"] = project_access
    app.extensions["organization_membership_service"] = (
        organization_membership
    )

    response = client.get(
        "/tasks/T-SOURCE-SCOPED/sources",
        headers=user_auth_header,
    )

    assert response.status_code == 404
    assert len(organization_membership.calls) == 1


def test_general_task_reads_redact_governed_source_metadata(
    client,
    admin_auth_header,
) -> None:
    from agent.db_models import TaskDB
    from agent.repository import task_repo

    task_id = "T-SOURCE-GENERAL-READ-REDACTION"
    task_repo.save(
        TaskDB(
            id=task_id,
            title="redact source metadata",
            verification_status={
                "status": "passed",
                "source_catalog": {
                    "source_catalog_id": "catalog-safe",
                    "source_catalog_hash": "catalog-hash-safe",
                    "sources": [
                        {
                            "path": "VERIFICATION-RAW-CONTENT-SENTINEL",
                            "content": "VERIFICATION-RAW-CONTENT-SENTINEL",
                        }
                    ],
                },
                "source_catalog_publication": {
                    "schema": "organization_source_catalog_publication.v1",
                    "binding_digest": "binding-digest-safe",
                    "record_bindings": [
                        {"path": "VERIFICATION-RAW-CONTENT-SENTINEL"}
                    ],
                },
                "answer_verification": {
                    "cited_source_ids": ["VERIFICATION-RAW-CONTENT-SENTINEL"]
                },
            },
            worker_execution_context={
                "source_catalog_binding": {"catalog_task_id": "secret-task"}
            },
        )
    )

    detail = client.get(f"/tasks/{task_id}", headers=admin_auth_header)
    assert detail.status_code == 200
    detail_data = detail.get_json()["data"]
    assert detail_data["verification_status"] == {"status": "passed"}
    assert "source_catalog_binding" not in (
        detail_data.get("worker_execution_context") or {}
    )

    listing = client.get("/tasks", headers=admin_auth_header)
    assert listing.status_code == 200
    listed_task = next(
        row
        for row in listing.get_json()["data"]
        if row.get("id") == task_id
    )
    assert listed_task["verification_status"] == {"status": "passed"}

    control_center = client.get(
        f"/api/tasks/{task_id}",
        headers=admin_auth_header,
    )
    assert control_center.status_code == 200
    control_data = control_center.get_json()["data"]
    assert control_data["task"]["verification_status"] == {
        "status": "passed"
    }
    assert control_data["verification"] == {"status": "passed"}

    governed = client.get(
        f"/tasks/{task_id}/verification",
        headers=admin_auth_header,
    )
    assert governed.status_code == 200
    governed_status = governed.get_json()["data"]["verification_status"]
    assert governed_status == {
        "status": "passed",
        "source_catalog": {
            "source_catalog_id": "catalog-safe",
            "source_catalog_hash": "catalog-hash-safe",
            "source_count": 1,
        },
        "source_catalog_publication": {
            "schema": "organization_source_catalog_publication.v1",
            "binding_digest": "binding-digest-safe",
        },
    }
    assert "VERIFICATION-RAW-CONTENT-SENTINEL" not in governed.get_data(
        as_text=True
    )
