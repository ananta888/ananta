from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.routes.tasks.recovery_manifest import recovery_manifest_bp
from agent.services.recovery_plan_contract import (
    calculate_recovery_task_payload_digest,
)
from agent.services.recovery_task_manifest_service import (
    RECOVERY_TASK_MANIFEST_SCHEMA,
    RecoveryTaskManifestService,
)
from agent.services.workflow_worker_service_auth import (
    STRICT_WORKER_REGISTRATION_PROVENANCE,
    WORKER_REGISTRATION_KEYRING_SCHEMA,
)

HUB_TOKEN = "hub-recovery-manifest-token-0123456789abcdef"
ALPHA_TOKEN = "alpha-recovery-manifest-token-0123456789abcdef"
BETA_TOKEN = "beta-recovery-manifest-token-0123456789abcdefg"
ALPHA_BOOTSTRAP = (
    "alpha-recovery-registration-token-0123456789abcdef"
)
BETA_BOOTSTRAP = (
    "beta-recovery-registration-token-0123456789abcdefg"
)
ALPHA_SESSION = "alpha-recovery-session-key-0123456789abcdef"
BETA_SESSION = "beta-recovery-session-key-0123456789abcdefg"
ALPHA_URL = "http://worker-alpha:5000"
BETA_URL = "http://worker-beta:5000"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _LockPort:
    @contextmanager
    def mutation_lock(self, _task_id: str):
        yield True


class _TaskRepo:
    def __init__(self, task) -> None:
        self.task = task

    def get_by_id(self, task_id: str):
        if str(getattr(self.task, "id", "")) == task_id:
            return self.task
        return None


class _AgentRepo:
    def __init__(self, agents) -> None:
        self.agents = list(agents)

    def get_all(self):
        return list(self.agents)


def _worker(*, name: str, url: str, token: str):
    return SimpleNamespace(
        name=name,
        url=url,
        token=token,
        role="worker",
        capabilities=["coding"],
        authorized_capabilities=["coding"],
        registration_provenance=(
            STRICT_WORKER_REGISTRATION_PROVENANCE
        ),
        registration_validated=True,
        status="online",
    )


def _recovery_child(*, assigned_agent_url: str = ALPHA_URL):
    task = SimpleNamespace(
        id="recovery-child-1",
        title="Implement approved segment",
        description="Execute only the approved recovery segment.",
        priority="High",
        status="assigned",
        created_at=1.0,
        updated_at=2.0,
        goal_id="goal-1",
        goal_trace_id="trace-1",
        plan_id="plan-1",
        plan_node_id="node-1",
        parent_task_id=None,
        source_task_id="source-1",
        team_id="team-1",
        derivation_reason="goal_task_recovery",
        derivation_depth=1,
        task_kind="coding",
        retrieval_intent="implementation",
        required_context_scope="task",
        preferred_bundle_mode="focused",
        required_capabilities=["coding"],
        context_bundle_id="bundle-1",
        worker_execution_context={
            "schema": "ananta.goal_execution_context.v1",
            "worker_execution_contract": {
                "allowed_tools": ["read", "edit"],
            },
            "expected_artifacts": [
                {"path": "src/result.py"},
            ],
        },
        worker_execution_contract=None,
        expected_artifacts=[],
        verification_spec={
            "expected_artifacts": [
                {"path": "src/result.py"},
            ],
        },
        depends_on=[],
        assigned_agent_url=assigned_agent_url,
        callback_url="https://hub.invalid/private-callback",
        callback_token="must-not-cross-manifest-boundary",
        history=[{"event": "hub-private"}],
        last_output="hub-private-output",
        verification_status={},
        status_reason_details={},
    )
    task.status_reason_details = {
        "materialized_from_plan": True,
        "recovery_dispatch_lease": {
            "token_digest": "must-not-cross-manifest-boundary",
        },
        "model_recovery_release": {
            "schema": "ananta.recovery_release_gate.v1",
            "release_epoch": "release-1",
            "plan_id": task.plan_id,
            "source_task_id": task.source_task_id,
            "goal_id": task.goal_id,
            "team_id": task.team_id,
            "approval_request_id": "approval-1",
            "recovery_key": "recovery-key-1",
            "task_payload_digest": (
                calculate_recovery_task_payload_digest(task)
            ),
        },
    }
    return task


def _manifest_service(task) -> RecoveryTaskManifestService:
    repos = SimpleNamespace(task_repo=_TaskRepo(task))
    return RecoveryTaskManifestService(
        repository_provider=lambda: repos,
        lock_provider=lambda: _LockPort(),
    )


def _write_keyring(path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    "worker-alpha": {
                        "worker_url": ALPHA_URL,
                        "registration_token": ALPHA_BOOTSTRAP,
                        "service_token_sha256": _sha256(
                            ALPHA_TOKEN
                        ),
                        "session_signing_key_sha256": _sha256(
                            ALPHA_SESSION
                        ),
                        "allowed_capabilities": ["coding"],
                    },
                    "worker-beta": {
                        "worker_url": BETA_URL,
                        "registration_token": BETA_BOOTSTRAP,
                        "service_token_sha256": _sha256(
                            BETA_TOKEN
                        ),
                        "session_signing_key_sha256": _sha256(
                            BETA_SESSION
                        ),
                        "allowed_capabilities": ["coding"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o440)


def _headers(token: str, *, worker_id: str, worker_url: str):
    return {
        "Authorization": f"Bearer {token}",
        "X-Ananta-Worker-ID": worker_id,
        "X-Ananta-Worker-URL": worker_url,
    }


def _app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    task,
) -> Flask:
    keyring = tmp_path / "recovery-manifest-keyring.json"
    _write_keyring(keyring)
    app = Flask(__name__)
    app.secret_key = (
        "recovery-manifest-user-session-secret-0123456789abcdef"
    )
    app.config.update(
        TESTING=True,
        AGENT_TOKEN=HUB_TOKEN,
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(
            keyring
        ),
    )
    app.extensions["repository_registry"] = SimpleNamespace(
        agent_repo=_AgentRepo(
            [
                _worker(
                    name="worker-alpha",
                    url=ALPHA_URL,
                    token=ALPHA_TOKEN,
                ),
                _worker(
                    name="worker-beta",
                    url=BETA_URL,
                    token=BETA_TOKEN,
                ),
            ]
        )
    )
    app.register_blueprint(recovery_manifest_bp)
    monkeypatch.setattr(
        "agent.routes.tasks.recovery_manifest."
        "get_recovery_task_manifest_service",
        lambda: _manifest_service(task),
    )
    monkeypatch.setattr(
        "agent.routes.tasks.recovery_manifest.log_audit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agent.auth.log_audit",
        lambda *_args, **_kwargs: None,
    )
    return app


def test_manifest_endpoint_requires_assigned_registered_worker_and_projects_only_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    task = _recovery_child()
    client = _app(monkeypatch, tmp_path, task).test_client()
    path = (
        f"/internal/tasks/{task.id}/recovery-child-manifest"
    )

    accepted = client.get(
        path,
        headers=_headers(
            ALPHA_TOKEN,
            worker_id="worker-alpha",
            worker_url=ALPHA_URL,
        ),
    )
    hub_credential = client.get(
        path,
        headers=_headers(
            HUB_TOKEN,
            worker_id="worker-alpha",
            worker_url=ALPHA_URL,
        ),
    )
    wrong_assignment = client.get(
        path,
        headers=_headers(
            BETA_TOKEN,
            worker_id="worker-beta",
            worker_url=BETA_URL,
        ),
    )

    assert accepted.status_code == 200
    assert accepted.headers["Cache-Control"] == "no-store"
    manifest = accepted.get_json()["data"]
    assert manifest["schema"] == RECOVERY_TASK_MANIFEST_SCHEMA
    assert manifest["task"]["id"] == task.id
    assert manifest["task"]["status_reason_details"] == {
        "model_recovery_release": (
            task.status_reason_details["model_recovery_release"]
        )
    }
    for forbidden in (
        "assigned_agent_url",
        "callback_url",
        "callback_token",
        "history",
        "last_output",
        "status",
        "verification_status",
    ):
        assert forbidden not in manifest["task"]
    assert hub_credential.status_code == 401
    assert wrong_assignment.status_code == 403
    assert wrong_assignment.get_json()["data"]["reason_code"] == (
        "recovery_task_manifest_assignment_denied"
    )


def test_manifest_endpoint_rejects_approval_payload_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    task = _recovery_child()
    task.description = "payload changed after approval"
    client = _app(monkeypatch, tmp_path, task).test_client()

    response = client.get(
        f"/internal/tasks/{task.id}/recovery-child-manifest",
        headers=_headers(
            ALPHA_TOKEN,
            worker_id="worker-alpha",
            worker_url=ALPHA_URL,
        ),
    )

    assert response.status_code == 409
    assert response.get_json()["data"]["reason_code"] == (
        "recovery_task_manifest_payload_digest_mismatch"
    )


def test_manifest_endpoint_never_projects_recovery_source_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    source = SimpleNamespace(
        id="recovery-source-1",
        assigned_agent_url=ALPHA_URL,
        derivation_reason=None,
        verification_status={},
        status_reason_details={
            "model_recovery": {
                "status": "materialized",
                "plan_id": "plan-1",
            }
        },
    )
    client = _app(monkeypatch, tmp_path, source).test_client()

    response = client.get(
        f"/internal/tasks/{source.id}/recovery-child-manifest",
        headers=_headers(
            ALPHA_TOKEN,
            worker_id="worker-alpha",
            worker_url=ALPHA_URL,
        ),
    )

    assert response.status_code == 404
    assert response.get_json()["data"]["reason_code"] == (
        "recovery_task_manifest_not_found"
    )


class _Response:
    status_code = 200

    def __init__(self, manifest: dict) -> None:
        self._manifest = manifest

    def json(self):
        return {
            "status": "success",
            "data": copy.deepcopy(self._manifest),
        }


class _SyncClient:
    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _Response(self.manifest)


def _configure_worker_sync(
    monkeypatch: pytest.MonkeyPatch,
    client: _SyncClient,
    captured: dict,
) -> None:
    _configure_worker_identity(monkeypatch, client)

    def update(task_id, status, **values):
        captured.update(
            {
                "id": task_id,
                "status": status,
                **values,
            }
        )

    monkeypatch.setattr(
        "agent.services.task_runtime_service."
        "update_local_task_status",
        update,
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.get_local_task_status",
        lambda task_id: (
            copy.deepcopy(captured)
            if captured.get("id") == task_id
            else None
        ),
    )


def _configure_worker_identity(
    monkeypatch: pytest.MonkeyPatch,
    client: _SyncClient,
) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(settings, "agent_name", "worker-alpha")
    monkeypatch.setattr(settings, "agent_url", ALPHA_URL)
    monkeypatch.setattr(
        "agent.auth.resolve_configured_agent_token",
        lambda *_args, **_kwargs: ALPHA_TOKEN,
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service._get_hub_sync_client",
        lambda: client,
    )


def test_worker_sync_uses_scoped_manifest_endpoint_identity_headers_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services.task_runtime_service import sync_task_from_hub

    task = _recovery_child()
    manifest = _manifest_service(task).manifest_for_worker(
        task_id=task.id,
        worker_url=ALPHA_URL,
    )
    client = _SyncClient(manifest)
    captured: dict = {}
    _configure_worker_sync(monkeypatch, client, captured)

    synced = sync_task_from_hub(task.id)

    assert synced is not None
    assert captured["id"] == task.id
    assert captured["status"] == "todo"
    assert captured["source_task_id"] == task.source_task_id
    assert "team_id" not in captured
    assert "assigned_agent_url" not in captured
    assert client.calls == [
        {
            "url": (
                "http://hub:5000/internal/tasks/recovery-child-1/"
                "recovery-child-manifest"
            ),
            "timeout": 10,
            "return_response": True,
            "silent": True,
            "headers": {
                "Authorization": f"Bearer {ALPHA_TOKEN}",
                "X-Ananta-Worker-ID": "worker-alpha",
                "X-Ananta-Worker-URL": ALPHA_URL,
            },
        }
    ]
    assert ALPHA_TOKEN != HUB_TOKEN


def test_worker_sync_rejects_manifest_payload_digest_drift_before_local_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services.task_runtime_service import sync_task_from_hub

    task = _recovery_child()
    manifest = _manifest_service(task).manifest_for_worker(
        task_id=task.id,
        worker_url=ALPHA_URL,
    )
    manifest["task"]["title"] = "tampered in transit"
    client = _SyncClient(manifest)
    captured: dict = {}
    _configure_worker_sync(monkeypatch, client, captured)

    assert sync_task_from_hub(task.id) is None
    assert captured == {}


def test_worker_sync_creates_missing_local_recovery_child_from_manifest(
    monkeypatch: pytest.MonkeyPatch,
    app,
) -> None:
    from agent.repository import task_repo
    from agent.services.task_runtime_service import sync_task_from_hub

    task = _recovery_child()
    task.id = "recovery-child-real-sync"
    task.status_reason_details[
        "model_recovery_release"
    ]["task_payload_digest"] = (
        calculate_recovery_task_payload_digest(task)
    )
    manifest = _manifest_service(task).manifest_for_worker(
        task_id=task.id,
        worker_url=ALPHA_URL,
    )
    client = _SyncClient(manifest)
    _configure_worker_identity(monkeypatch, client)
    task_repo.delete(task.id)

    try:
        with app.app_context():
            synced = sync_task_from_hub(task.id)
            persisted = task_repo.get_by_id(task.id)

        assert synced is not None
        assert persisted is not None
        assert persisted.id == task.id
        assert persisted.status == "todo"
        assert persisted.context_bundle_id == task.context_bundle_id
        assert persisted.assigned_agent_url is None
        assert persisted.team_id is None
    finally:
        task_repo.delete(task.id)
