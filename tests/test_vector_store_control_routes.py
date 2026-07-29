from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.vector_store_control import vector_store_control_bp
from agent.services.vector_index_task_attestation_service import (
    VectorIndexTaskSigningConfigurationError,
)
from agent.services.vector_index_task_service import VectorIndexTaskService
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)


def _app() -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vector_store_control_bp)
    return app


def _headers(role: str, **claims) -> dict[str, str]:
    token = generate_token(
        {"sub": "operator-a", "role": role, **claims},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def test_vector_store_control_requires_admin_and_workspace_scope(monkeypatch) -> None:
    service = SimpleNamespace(
        submit=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not submit")
        )
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )
    client = _app().test_client()
    body = {
        "operation": "delete",
        "workspace_id": "workspace-a",
        "repository_id": "repo-a",
        "idempotency_key": "request-1234",
        "payload": {"point_ids": ["1"]},
    }

    assert client.post("/api/vector-store/index-tasks", json=body).status_code == 401
    forbidden = client.post(
        "/api/vector-store/index-tasks",
        json=body,
        headers=_headers("admin", workspace_id="workspace-b"),
    )
    assert forbidden.status_code == 403
    assert forbidden.get_json()["reason_code"] == "vector_store_workspace_forbidden"


def test_global_admin_submits_typed_trusted_scope(monkeypatch) -> None:
    captured: list[dict] = []
    service = SimpleNamespace(
        submit=lambda **kwargs: captured.append(kwargs)
        or {
            "job_id": "vector-index-a",
            "status": "queued",
            "scope": kwargs["trusted_scope"].to_dict(),
        }
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )
    response = _app().test_client().post(
        "/api/vector-store/index-tasks",
        json={
            "operation": "delete",
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "idempotency_key": "request-1234",
            "payload": {"point_ids": ["1"]},
            "priority": "critical",
        },
        headers=_headers("system_admin"),
    )

    assert response.status_code == 202
    assert captured[0]["trusted_scope"].workspace_id == "workspace-a"
    assert captured[0]["operation"] == "delete"
    assert captured[0]["priority"] == "critical"


def test_missing_hub_signing_keyring_is_service_unavailable(
    monkeypatch,
) -> None:
    service = SimpleNamespace(
        submit=lambda **_kwargs: (_ for _ in ()).throw(
            VectorIndexTaskSigningConfigurationError(
                "vector_index_task_signing_keyring_required"
            )
        )
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )

    response = _app().test_client().post(
        "/api/vector-store/index-tasks",
        json={
            "operation": "delete",
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "idempotency_key": "request-1234",
            "payload": {"point_ids": ["1"]},
        },
        headers=_headers("system_admin"),
    )

    assert response.status_code == 503
    assert response.get_json()["reason_code"] == (
        "vector_index_task_signing_keyring_required"
    )


def test_route_rejects_cross_scope_ref_and_legacy_migration_path(
    monkeypatch,
) -> None:
    queue_calls: list[dict] = []

    class Queue:
        def ingest_task(self, **kwargs):
            queue_calls.append(kwargs)

    class Repository:
        def get_by_id(self, _task_id):
            return None

        def get_all(self):
            return []

    service = VectorIndexTaskService(
        task_queue=Queue(),
        task_repository=Repository(),
        audit=lambda _event, _payload: None,
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )
    client = _app().test_client()
    headers = _headers("system_admin")
    compatibility = {
        "dimensions": 2,
        "distance": "cosine",
        "provider": "test",
        "model": "v1",
        "profile": "default",
        "encoding": "float32",
        "config_hash": "config-a",
        "schema_version": "vector_store.v1",
        "manifest_hash": "manifest-a",
    }
    other_scope_ref = VectorIndexArtifactLocator.locate(
        scope={
            "workspace_id": "workspace-b",
            "repository_id": "repo-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        content_sha256="a" * 64,
    ).to_reference()

    cross_scope = client.post(
        "/api/vector-store/index-tasks",
        json={
            "operation": "index",
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "idempotency_key": "cross-scope-route-ref",
            "payload": {"input_ref": other_scope_ref},
        },
        headers=headers,
    )
    legacy = client.post(
        "/api/vector-store/index-tasks",
        json={
            "operation": "migrate",
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "idempotency_key": "legacy-route-source",
            "payload": {
                "input_ref": VectorIndexArtifactLocator.locate(
                    scope={
                        "workspace_id": "workspace-a",
                        "repository_id": "repo-a",
                        "profile_name": "default",
                        "domain": "codecompass",
                    },
                    content_sha256="a" * 64,
                ).to_reference(),
                "compatibility": compatibility,
                "migration": {
                    "dry_run": True,
                    "source_path": "legacy/index.json",
                },
            },
        },
        headers=headers,
    )

    assert cross_scope.status_code == 400
    assert cross_scope.get_json()["reason_code"] == (
        "vector_index_input_ref_scope_mismatch"
    )
    assert legacy.status_code == 400
    assert legacy.get_json()["reason_code"] == (
        "vector_index_migration_fields_forbidden"
    )
    assert queue_calls == []


def test_cancel_and_retry_enforce_task_workspace_authorization(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class Service:
        def get_task(self, job_id):
            return {
                "job_id": job_id,
                "status": "failed",
                "scope": {
                    "workspace_id": "workspace-a",
                    "repository_id": "repo-a",
                },
            }

        def cancel(self, **kwargs):
            calls.append(("cancel", kwargs))
            return self.get_task(kwargs["job_id"])

        def retry(self, **kwargs):
            calls.append(("retry", kwargs))
            return {
                **self.get_task(kwargs["job_id"]),
                "status": "queued",
            }

    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: Service(),
    )
    client = _app().test_client()
    forbidden_headers = _headers("admin", workspace_id="workspace-b")
    allowed_headers = _headers("admin", workspace_id="workspace-a")

    assert client.post(
        "/api/vector-store/index-tasks/vector-index-a/cancel",
        headers=forbidden_headers,
    ).status_code == 403
    assert client.post(
        "/api/vector-store/index-tasks/vector-index-a/retry",
        headers=forbidden_headers,
    ).status_code == 403
    assert calls == []

    cancelled = client.post(
        "/api/vector-store/index-tasks/vector-index-a/cancel",
        headers=allowed_headers,
    )
    retried = client.post(
        "/api/vector-store/index-tasks/vector-index-a/retry",
        headers=allowed_headers,
    )

    assert cancelled.status_code == 200
    assert retried.status_code == 200
    assert [name for name, _ in calls] == ["cancel", "retry"]
    assert all(
        values["actor"] == "operator-a" for _, values in calls
    )


def test_profile_override_requires_global_admin(monkeypatch) -> None:
    service = SimpleNamespace(
        set_profile_override=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not mutate profile override")
        )
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_store_rollout_service",
        lambda: service,
    )
    response = _app().test_client().put(
        "/api/vector-store/profiles/default/override",
        json={
            "override": {"provider": "qdrant"},
            "expected_revision": 0,
        },
        headers=_headers("admin", workspace_id="workspace-a"),
    )

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == (
        "vector_store_global_admin_required"
    )
