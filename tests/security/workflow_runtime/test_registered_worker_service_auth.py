from __future__ import annotations

import hashlib
import json
import urllib.request
from types import SimpleNamespace

import pytest
from flask import Flask, g

from agent.auth import admin_required, check_auth, check_service_auth
from agent.services.workflow_worker_service_auth import (
    KNOWLEDGE_INDEX_PAYLOAD_SCOPE,
    RUNTIME_SERVICE_KEYRING_SCHEMA,
    STRICT_WORKER_REGISTRATION_PROVENANCE,
    WORKER_REGISTRATION_KEYRING_SCHEMA,
    WORKFLOW_LANGGRAPH_CHECKPOINT_SCOPE,
    WORKFLOW_TEMPORAL_TASK_SCOPE,
    WORKFLOW_WORKER_COMMAND_SCOPE,
    WorkflowWorkerAuthConfigurationError,
    WorkflowWorkerAuthDenied,
    authenticate_preconfigured_runtime_service,
    authenticate_registered_workflow_worker,
    validate_strict_worker_registration,
    validate_workflow_credential_disjointness,
)
from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient

HUB_TOKEN = "hub-administrator-service-token-0123456789abcdef"
ALPHA_TOKEN = "alpha-workflow-service-token-0123456789abcdef"
BETA_TOKEN = "beta-workflow-service-token-0123456789abcdefg"
ALPHA_BOOTSTRAP = "alpha-registration-bootstrap-0123456789abcdef"
BETA_BOOTSTRAP = "beta-registration-bootstrap-0123456789abcdefg"
TEMPORAL_TOKEN = "temporal-runtime-service-token-0123456789abcdef"
ALPHA_SESSION_KEY = "alpha-session-signing-key-0123456789abcdef"
BETA_SESSION_KEY = "beta-session-signing-key-0123456789abcdefg"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _AgentRepo:
    def __init__(self, agents):
        self._agents = list(agents)

    def get_all(self):
        return list(self._agents)


def _agent(
    *,
    name: str,
    url: str,
    token: str,
    capabilities: list[str],
    registration_provenance: str = STRICT_WORKER_REGISTRATION_PROVENANCE,
    authorized_capabilities: list[str] | None = None,
):
    return SimpleNamespace(
        name=name,
        url=url,
        token=token,
        role="worker",
        capabilities=capabilities,
        authorized_capabilities=list(authorized_capabilities or capabilities),
        registration_provenance=registration_provenance,
        registration_validated=True,
        status="online",
    )


def _strict_app(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Flask:
    alpha = _agent(
        name="worker-alpha",
        url="http://worker-alpha:5000",
        token=ALPHA_TOKEN,
        capabilities=["workflow.adapter.native"],
    )
    beta = _agent(
        name="worker-beta",
        url="http://worker-beta:5000",
        token=BETA_TOKEN,
        capabilities=["workflow.adapter.langgraph"],
    )
    app = Flask(__name__)
    registration_keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(registration_keyring)
    runtime_keyring = tmp_path / "runtime-service-keyring.json"
    runtime_keyring.write_text(
        json.dumps(
            {
                "schema": RUNTIME_SERVICE_KEYRING_SCHEMA,
                "services": {
                    "ananta-temporal-worker": {
                        "token": TEMPORAL_TOKEN,
                        "scopes": [WORKFLOW_TEMPORAL_TASK_SCOPE],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    runtime_keyring.chmod(0o440)
    app.config.update(
        TESTING=True,
        AGENT_TOKEN=HUB_TOKEN,
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(
            registration_keyring
        ),
        ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE=str(runtime_keyring),
    )
    app.extensions["repository_registry"] = SimpleNamespace(
        agent_repo=_AgentRepo([alpha, beta])
    )
    monkeypatch.setattr("agent.auth.log_audit", lambda *_args, **_kwargs: None)

    @app.post("/workflow-command")
    @check_service_auth(scope=WORKFLOW_WORKER_COMMAND_SCOPE)
    def workflow_command():
        return {
            "admin": g.is_admin,
            "auth": dict(g.auth_payload),
            "identity": dict(g.service_identity),
        }

    @app.post("/checkpoint")
    @check_service_auth(scope=WORKFLOW_LANGGRAPH_CHECKPOINT_SCOPE)
    def checkpoint():
        return {"worker_id": g.service_identity["worker_id"]}

    @app.post("/temporal")
    @check_service_auth(scope=WORKFLOW_TEMPORAL_TASK_SCOPE)
    def temporal():
        return {
            "admin": g.is_admin,
            "service_id": g.service_identity["service_id"],
        }

    @app.get("/generic")
    @check_auth
    def generic():
        return {"ok": True}

    @app.get("/admin")
    @admin_required
    def admin():
        return {"ok": True}

    return app


def _headers(token: str, *, worker_id: str, worker_url: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Ananta-Worker-ID": worker_id,
        "X-Ananta-Worker-URL": worker_url,
    }


def test_registered_worker_token_is_identity_bound_scoped_and_never_admin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _strict_app(monkeypatch, tmp_path).test_client()
    headers = _headers(
        ALPHA_TOKEN,
        worker_id="worker-alpha",
        worker_url="http://worker-alpha:5000",
    )

    accepted = client.post("/workflow-command", headers=headers)
    generic = client.get("/generic", headers=headers)
    admin = client.get("/admin", headers=headers)
    checkpoint = client.post("/checkpoint", headers=headers)
    temporal = client.post("/temporal", headers=headers)

    assert accepted.status_code == 200
    assert accepted.get_json()["admin"] is False
    assert accepted.get_json()["auth"] == {
        "auth_mode": "registered_worker_service_token",
        "token_use": "workflow_worker_service",
        "worker_id": "worker-alpha",
        "worker_url": "http://worker-alpha:5000",
        "service_scope": WORKFLOW_WORKER_COMMAND_SCOPE,
    }
    assert generic.status_code == 401
    assert admin.status_code == 403
    assert checkpoint.status_code == 403
    assert checkpoint.get_json()["data"]["reason_code"] == (
        "workflow_worker_service_scope_forbidden"
    )
    # Temporal retains its separate legacy runtime-service boundary. A Native
    # Worker credential still cannot use that endpoint.
    assert temporal.status_code == 401


def test_knowledge_index_payload_scope_requires_index_worker_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    worker = _agent(
        name="worker-alpha",
        url="http://worker-alpha:5000",
        token=ALPHA_TOKEN,
        capabilities=["retrieval", "index_write"],
    )
    keyring = tmp_path / "index-worker-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    "worker-alpha": {
                        "worker_url": "http://worker-alpha:5000",
                        "registration_token": ALPHA_BOOTSTRAP,
                        "service_token_sha256": _sha256(ALPHA_TOKEN),
                        "session_signing_key_sha256": _sha256(ALPHA_SESSION_KEY),
                        "allowed_capabilities": ["retrieval", "index_write"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    keyring.chmod(0o440)
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        AGENT_TOKEN=HUB_TOKEN,
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(keyring),
    )
    app.extensions["repository_registry"] = SimpleNamespace(
        agent_repo=_AgentRepo([worker])
    )
    monkeypatch.setattr("agent.auth.log_audit", lambda *_args, **_kwargs: None)

    @app.get("/knowledge-index-payload")
    @check_service_auth(scope=KNOWLEDGE_INDEX_PAYLOAD_SCOPE)
    def payload():
        return {"scope": g.auth_payload["service_scope"]}

    response = app.test_client().get(
        "/knowledge-index-payload",
        headers=_headers(
            ALPHA_TOKEN,
            worker_id="worker-alpha",
            worker_url="http://worker-alpha:5000",
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["scope"] == KNOWLEDGE_INDEX_PAYLOAD_SCOPE


def test_worker_cannot_claim_another_registered_identity_or_use_hub_admin_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _strict_app(monkeypatch, tmp_path).test_client()

    foreign_claim = client.post(
        "/workflow-command",
        headers=_headers(
            ALPHA_TOKEN,
            worker_id="worker-beta",
            worker_url="http://worker-beta:5000",
        ),
    )
    hub_token = client.post(
        "/workflow-command",
        headers=_headers(
            HUB_TOKEN,
            worker_id="worker-alpha",
            worker_url="http://worker-alpha:5000",
        ),
    )
    missing_claim = client.post(
        "/workflow-command",
        headers={"Authorization": f"Bearer {ALPHA_TOKEN}"},
    )

    assert foreign_claim.status_code == 403
    assert foreign_claim.get_json()["data"]["reason_code"] == (
        "workflow_worker_service_identity_mismatch"
    )
    assert hub_token.status_code == 401
    assert hub_token.get_json()["data"]["reason_code"] == (
        "workflow_worker_service_token_invalid"
    )
    assert missing_claim.status_code == 401
    assert missing_claim.get_json()["data"]["reason_code"] == (
        "workflow_worker_service_identity_required"
    )


def test_registered_langgraph_worker_receives_only_its_declared_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _strict_app(monkeypatch, tmp_path).test_client()
    headers = _headers(
        BETA_TOKEN,
        worker_id="worker-beta",
        worker_url="http://worker-beta:5000/",
    )

    assert client.post("/checkpoint", headers=headers).status_code == 200
    assert client.post("/workflow-command", headers=headers).status_code == 200


def test_legacy_or_unproven_agent_rows_are_never_strict_service_identities(
    tmp_path,
) -> None:
    keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(keyring)
    legacy = _agent(
        name="worker-alpha",
        url="http://worker-alpha:5000",
        token=ALPHA_TOKEN,
        capabilities=["workflow.adapter.native"],
        registration_provenance="legacy",
    )

    with pytest.raises(WorkflowWorkerAuthDenied) as raised:
        authenticate_registered_workflow_worker(
            ALPHA_TOKEN,
            required_scope=WORKFLOW_WORKER_COMMAND_SCOPE,
            claimed_worker_id="worker-alpha",
            claimed_worker_url="http://worker-alpha:5000",
            registered_agents=[legacy],
            hub_service_token=HUB_TOKEN,
            config={
                "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(
                    keyring
                )
            },
        )
    assert raised.value.status_code == 403
    assert raised.value.reason_code == (
        "workflow_worker_registration_not_validated"
    )


def test_temporal_runtime_uses_its_own_scope_and_never_hub_admin_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _strict_app(monkeypatch, tmp_path).test_client()
    headers = {
        "Authorization": f"Bearer {TEMPORAL_TOKEN}",
        "X-Ananta-Service-ID": "ananta-temporal-worker",
    }

    accepted = client.post("/temporal", headers=headers)
    worker_command = client.post("/workflow-command", headers=headers)
    generic = client.get("/generic", headers=headers)
    admin = client.get("/admin", headers=headers)
    wrong_identity = client.post(
        "/temporal",
        headers={**headers, "X-Ananta-Service-ID": "another-runtime"},
    )

    assert accepted.status_code == 200
    assert accepted.get_json() == {
        "admin": False,
        "service_id": "ananta-temporal-worker",
    }
    assert worker_command.status_code == 401
    assert generic.status_code == 401
    assert admin.status_code == 403
    assert wrong_identity.status_code == 401
    assert wrong_identity.get_json()["data"]["reason_code"] == (
        "workflow_runtime_service_identity_mismatch"
    )


def _write_keyring(
    path,
    *,
    alpha_bootstrap: str = ALPHA_BOOTSTRAP,
    beta_bootstrap: str = BETA_BOOTSTRAP,
    alpha_service_token: str = ALPHA_TOKEN,
    beta_service_token: str = BETA_TOKEN,
    alpha_session_key: str = ALPHA_SESSION_KEY,
    beta_session_key: str = BETA_SESSION_KEY,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    "worker-alpha": {
                        "worker_url": "http://worker-alpha:5000",
                        "registration_token": alpha_bootstrap,
                        "service_token_sha256": _sha256(alpha_service_token),
                        "session_signing_key_sha256": _sha256(alpha_session_key),
                        "allowed_capabilities": ["workflow.adapter.native"],
                    },
                    "worker-beta": {
                        "worker_url": "http://worker-beta:5000",
                        "registration_token": beta_bootstrap,
                        "service_token_sha256": _sha256(beta_service_token),
                        "session_signing_key_sha256": _sha256(beta_session_key),
                        "allowed_capabilities": ["workflow.adapter.langgraph"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o440)


def _write_runtime_keyring(path, *, token: str = TEMPORAL_TOKEN) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": RUNTIME_SERVICE_KEYRING_SCHEMA,
                "services": {
                    "ananta-temporal-worker": {
                        "token": token,
                        "scopes": [WORKFLOW_TEMPORAL_TASK_SCOPE],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o440)


def _registration(
    *,
    name: str = "worker-alpha",
    url: str = "http://worker-alpha:5000",
    service_token: str = ALPHA_TOKEN,
    bootstrap_token: str = ALPHA_BOOTSTRAP,
    capabilities: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "url": url,
        "role": "worker",
        "token": service_token,
        "registration_token": bootstrap_token,
        "capabilities": list(capabilities or ["workflow.adapter.native"]),
    }


def test_registration_keyring_binds_bootstrap_to_name_url_and_distinct_service_token(
    tmp_path,
) -> None:
    keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(keyring)
    config = {
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(keyring)
    }

    accepted = validate_strict_worker_registration(
        _registration(),
        registered_agents=[],
        hub_service_token=HUB_TOKEN,
        config=config,
    )
    assert accepted.worker_id == "worker-alpha"
    assert accepted.allowed_capabilities == ("workflow.adapter.native",)
    assert accepted.service_token_sha256 == _sha256(ALPHA_TOKEN)
    assert accepted.session_signing_key_sha256 == _sha256(ALPHA_SESSION_KEY)

    for payload, reason in (
        (
            _registration(bootstrap_token=BETA_BOOTSTRAP),
            "workflow_worker_registration_identity_denied",
        ),
        (
            _registration(url="http://worker-beta:5000"),
            "workflow_worker_registration_identity_denied",
        ),
        (
            _registration(service_token=ALPHA_BOOTSTRAP),
            "workflow_worker_registration_secret_reuse_denied",
        ),
        (
            _registration(service_token=HUB_TOKEN),
            "workflow_worker_hub_admin_credential_reuse_denied",
        ),
    ):
        with pytest.raises(WorkflowWorkerAuthDenied) as raised:
            validate_strict_worker_registration(
                payload,
                registered_agents=[],
                hub_service_token=HUB_TOKEN,
                config=config,
            )
        assert raised.value.reason_code == reason


def test_registration_rejects_capability_escalation_and_invalid_allowlist(
    tmp_path,
) -> None:
    keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(keyring)
    config = {
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(keyring)
    }

    with pytest.raises(WorkflowWorkerAuthDenied) as raised:
        validate_strict_worker_registration(
            _registration(
                capabilities=[
                    "workflow.adapter.native",
                    "workflow.adapter.langgraph",
                ]
            ),
            registered_agents=[],
            hub_service_token=HUB_TOKEN,
            config=config,
        )
    assert raised.value.reason_code == (
        "workflow_worker_registration_capability_escalation_denied"
    )

    invalid = tmp_path / "invalid-worker-registration-keyring.json"
    invalid.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    "worker-alpha": {
                        "worker_url": "http://worker-alpha:5000",
                        "registration_token": ALPHA_BOOTSTRAP,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    invalid.chmod(0o440)
    with pytest.raises(WorkflowWorkerAuthConfigurationError):
        validate_strict_worker_registration(
            _registration(),
            registered_agents=[],
            hub_service_token=HUB_TOKEN,
            config={
                "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(
                    invalid
                )
            },
        )


def test_registration_keyring_prevents_cross_worker_overwrite_and_token_reuse(
    tmp_path,
) -> None:
    keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(keyring)
    config = {
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(keyring)
    }
    existing = _agent(
        name="worker-beta",
        url="http://worker-beta:5000",
        token=BETA_TOKEN,
        capabilities=["workflow.adapter.langgraph"],
    )

    with pytest.raises(WorkflowWorkerAuthDenied) as token_conflict:
        validate_strict_worker_registration(
            _registration(service_token=BETA_TOKEN),
            registered_agents=[existing],
            hub_service_token=HUB_TOKEN,
            config=config,
        )
    assert token_conflict.value.reason_code == "workflow_worker_service_token_conflict"


def test_registration_allows_service_token_rotation_for_same_bound_identity(
    tmp_path,
) -> None:
    keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(keyring)
    old_token = "alpha-old-workflow-service-token-0123456789abcdef"
    existing = _agent(
        name="worker-alpha",
        url="http://worker-alpha:5000",
        token=old_token,
        capabilities=["workflow.adapter.native"],
    )

    credential = validate_strict_worker_registration(
        _registration(service_token=ALPHA_TOKEN),
        registered_agents=[existing],
        hub_service_token=HUB_TOKEN,
        config={
            "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(keyring)
        },
    )

    assert credential.worker_id == "worker-alpha"


def test_registration_rejects_cross_keyring_secret_reuse_matrix(tmp_path) -> None:
    runtime_keyring = tmp_path / "runtime-keyring.json"
    _write_runtime_keyring(runtime_keyring)

    cases = (
        (
            HUB_TOKEN,
            ALPHA_TOKEN,
            HUB_TOKEN,
            [],
            "workflow_worker_registration_hub_admin_credential_reuse_denied",
        ),
        (
            TEMPORAL_TOKEN,
            ALPHA_TOKEN,
            TEMPORAL_TOKEN,
            [],
            "workflow_worker_registration_runtime_credential_reuse_denied",
        ),
        (
            ALPHA_BOOTSTRAP,
            BETA_BOOTSTRAP,
            ALPHA_BOOTSTRAP,
            [],
            "workflow_worker_service_registration_credential_reuse_denied",
        ),
        (
            BETA_TOKEN,
            ALPHA_TOKEN,
            BETA_TOKEN,
            [
                _agent(
                    name="worker-beta",
                    url="http://worker-beta:5000",
                    token=BETA_TOKEN,
                    capabilities=["workflow.adapter.langgraph"],
                )
            ],
            "workflow_worker_registration_service_credential_reuse_denied",
        ),
        (
            ALPHA_BOOTSTRAP,
            TEMPORAL_TOKEN,
            ALPHA_BOOTSTRAP,
            [],
            "workflow_worker_runtime_credential_reuse_denied",
        ),
    )
    for index, (
        alpha_bootstrap,
        service_token,
        submitted_bootstrap,
        registered_agents,
        reason_code,
    ) in enumerate(cases):
        registration_keyring = tmp_path / f"registration-keyring-{index}.json"
        _write_keyring(registration_keyring, alpha_bootstrap=alpha_bootstrap)
        with pytest.raises(WorkflowWorkerAuthDenied) as raised:
            validate_strict_worker_registration(
                _registration(
                    service_token=service_token,
                    bootstrap_token=submitted_bootstrap,
                ),
                registered_agents=registered_agents,
                hub_service_token=HUB_TOKEN,
                config={
                    "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(
                        registration_keyring
                    ),
                    "ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE": str(
                        runtime_keyring
                    ),
                },
            )
        assert raised.value.reason_code == reason_code


def test_runtime_keyring_rejects_hub_or_registered_worker_token_reuse(
    tmp_path,
) -> None:
    keyring = tmp_path / "runtime-keyring.json"
    _write_runtime_keyring(keyring, token=ALPHA_TOKEN)
    config = {"ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE": str(keyring)}

    with pytest.raises(WorkflowWorkerAuthConfigurationError, match="worker_credential_reuse"):
        authenticate_preconfigured_runtime_service(
            ALPHA_TOKEN,
            required_scope=WORKFLOW_TEMPORAL_TASK_SCOPE,
            claimed_service_id="ananta-temporal-worker",
            forbidden_tokens=[ALPHA_TOKEN],
            config=config,
        )
    with pytest.raises(WorkflowWorkerAuthConfigurationError, match="admin_credential_reuse"):
        authenticate_preconfigured_runtime_service(
            ALPHA_TOKEN,
            required_scope=WORKFLOW_TEMPORAL_TASK_SCOPE,
            claimed_service_id="ananta-temporal-worker",
            forbidden_token=ALPHA_TOKEN,
            config=config,
        )


def test_runtime_keyring_rejects_any_bootstrap_token_reuse(tmp_path) -> None:
    registration_keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(registration_keyring)
    runtime_keyring = tmp_path / "runtime-keyring.json"
    _write_runtime_keyring(runtime_keyring, token=BETA_BOOTSTRAP)

    with pytest.raises(
        WorkflowWorkerAuthConfigurationError,
        match="registration_credential_reuse",
    ):
        authenticate_preconfigured_runtime_service(
            BETA_BOOTSTRAP,
            required_scope=WORKFLOW_TEMPORAL_TASK_SCOPE,
            claimed_service_id="ananta-temporal-worker",
            forbidden_token=HUB_TOKEN,
            forbidden_tokens=[ALPHA_TOKEN, BETA_TOKEN],
            config={
                "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(
                    registration_keyring
                ),
                "ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE": str(
                    runtime_keyring
                ),
            },
        )


@pytest.mark.parametrize(
    ("session_secret", "reason_code"),
    [
        (
            HUB_TOKEN,
            "workflow_hub_session_hub_service_credential_reuse_denied",
        ),
        (
            ALPHA_TOKEN,
            "workflow_hub_session_worker_service_credential_reuse_denied",
        ),
        (
            ALPHA_BOOTSTRAP,
            "workflow_hub_session_worker_registration_credential_reuse_denied",
        ),
        (
            ALPHA_SESSION_KEY,
            "workflow_hub_session_worker_session_credential_reuse_denied",
        ),
        (
            TEMPORAL_TOKEN,
            "workflow_hub_session_runtime_service_credential_reuse_denied",
        ),
    ],
)
def test_strict_boundary_rejects_user_session_secret_reuse_in_every_domain(
    tmp_path,
    session_secret: str,
    reason_code: str,
) -> None:
    registration_keyring = tmp_path / "worker-registration-keyring.json"
    _write_keyring(registration_keyring)
    runtime_keyring = tmp_path / "runtime-keyring.json"
    _write_runtime_keyring(runtime_keyring)

    with pytest.raises(WorkflowWorkerAuthConfigurationError, match=reason_code):
        validate_workflow_credential_disjointness(
            user_session_secret=session_secret,
            hub_service_token=HUB_TOKEN,
            worker_service_tokens=[ALPHA_TOKEN, BETA_TOKEN],
            config={
                "ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH": True,
                "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(
                    registration_keyring
                ),
                "ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE": str(
                    runtime_keyring
                ),
            },
        )


def test_keyring_rejects_duplicate_pre_registration_worker_secret_fingerprints(
    tmp_path,
) -> None:
    keyring = tmp_path / "duplicate-worker-secret-keyring.json"
    _write_keyring(keyring, beta_session_key=ALPHA_TOKEN)

    with pytest.raises(WorkflowWorkerAuthConfigurationError):
        validate_workflow_credential_disjointness(
            user_session_secret="independent-hub-session-key-0123456789abcdef",
            hub_service_token=HUB_TOKEN,
            worker_service_tokens=[],
            config={
                "ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH": True,
                "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(keyring),
            },
        )


def test_application_startup_runs_complete_credential_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from agent.ai_agent import _validate_workflow_credential_boundary

    app = _strict_app(monkeypatch, tmp_path)
    app.secret_key = ALPHA_TOKEN

    with pytest.raises(
        WorkflowWorkerAuthConfigurationError,
        match="workflow_hub_session_worker_service_credential_reuse_denied",
    ):
        _validate_workflow_credential_boundary(app)


def test_native_workflow_client_reads_own_token_and_sends_bound_identity_headers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    token_file = tmp_path / "alpha-service-token"
    token_file.write_text(ALPHA_TOKEN, encoding="utf-8")
    token_file.chmod(0o440)
    captured: dict[str, str | None] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read(_limit: int) -> bytes:
            return b'{"data":{"allowed":true}}'

    def fake_urlopen(request: urllib.request.Request, **_kwargs):
        captured["worker_id"] = request.headers.get("X-ananta-worker-id")
        captured["worker_url"] = request.headers.get("X-ananta-worker-url")
        captured["authorization"] = request.headers.get("Authorization")
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = HttpWorkflowHubDecisionClient.from_environment(
        {
            "ANANTA_WORKFLOW_HUB_URL": "http://hub:5000",
            "ANANTA_WORKFLOW_HUB_TOKEN_FILE": str(token_file),
            "AGENT_NAME": "worker-alpha",
            "AGENT_URL": "http://worker-alpha:5000",
        }
    )
    assert client is not None

    assert client.command("authorize_execution", binding={})["allowed"] is True
    assert captured == {
        "authorization": f"Bearer {ALPHA_TOKEN}",
        "worker_id": "worker-alpha",
        "worker_url": "http://worker-alpha:5000",
    }
