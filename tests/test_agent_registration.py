import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask


def test_register_agent_success(client, app):
    """Testet die erfolgreiche Registrierung eines Agenten bei erreichbarer URL."""
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("agent.routes.system.agent_repo") as mock_repo:
            payload = {
                "name": "test-agent",
                "url": "http://test-agent:5000",
                "role": "worker",
                "worker_roles": ["coder"],
                "capabilities": ["coding"],
            }
            response = client.post("/register", json=payload)

            assert response.status_code == 200
            assert response.json["status"] == "success"
            assert response.json["data"]["status"] == "registered"
            assert response.json["data"]["agent"]["available_for_routing"] is True
            assert response.json["data"]["agent"]["liveness"]["available_for_routing"] is True
            mock_repo.save.assert_called_once()


def test_register_agent_unreachable(client, app):
    """Testet die Ablehnung der Registrierung bei nicht erreichbarer URL."""
    with patch("agent.routes.system.http_client.get") as mock_get:
        # Simuliere nicht erreichbare URL
        mock_get.return_value = None

        payload = {
            "name": "failing-agent",
            "url": "http://invalid-url",
            "role": "worker",
            "worker_roles": ["coder"],
            "capabilities": ["coding"],
        }
        response = client.post("/register", json=payload)

        assert response.status_code == 400
        assert "unreachable" in response.json["message"].lower()


def test_register_agent_with_capabilities_metadata(client, app):
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("agent.routes.system.agent_repo") as mock_repo:
            payload = {
                "name": "planner-agent",
                "url": "http://planner-agent:5000",
                "role": "worker",
                "worker_roles": ["planner"],
                "capabilities": ["planning", "analysis"],
                "execution_limits": {"max_parallel_tasks": 2},
            }
            response = client.post("/register", json=payload)

            assert response.status_code == 200
            saved_agent = mock_repo.save.call_args[0][0]
            assert saved_agent.worker_roles == ["planner"]
            assert saved_agent.capabilities == ["planning", "analysis"]
            assert saved_agent.execution_limits["max_parallel_tasks"] == 2
            assert saved_agent.registration_validated is True
            assert saved_agent.validated_at is not None
            assert response.json["data"]["agent"]["execution_limits"]["max_parallel_tasks"] == 2


def test_register_agent_accepts_explicit_worker_kind(client, app):
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("agent.routes.system.agent_repo") as mock_repo:
            payload = {
                "name": "worker-opencode-a",
                "url": "http://worker-opencode-a:5000",
                "role": "worker",
                "worker_roles": ["coder"],
                "capabilities": ["patch_propose", "code_read"],
                "worker_kind": "opencode",
            }
            response = client.post("/register", json=payload)
            assert response.status_code == 200
            saved_agent = mock_repo.save.call_args[0][0]
            assert saved_agent.execution_limits["worker_kind"] == "opencode"


def test_register_agent_exposes_validation_errors_field(client, app):
    """DRR-T037: Agent directory entry exposes registration_validated and validation_errors read-only."""
    from agent.db_models import AgentInfoDB
    from agent.repository import agent_repo

    agent = AgentInfoDB(
        url="http://repair-worker:5000",
        name="repair-worker",
        role="worker",
        worker_roles=["repair"],
        capabilities=["repair.diagnose", "repair.execute.low_risk"],
        execution_limits={"max_parallel_tasks": 1},
        registration_validated=True,
        validation_errors=[],
        status="online",
    )
    agent_repo.save(agent)

    client.get("/agents", headers={"Authorization": "Bearer admin-token-placeholder"})
    # Directory entry includes validation_errors read-only field when auth works
    from agent.services.agent_registry_service import AgentRegistryService

    svc = AgentRegistryService()
    entry = svc.build_directory_entry(agent=agent, timeout=60.0)
    assert "validation_errors" in entry
    assert isinstance(entry["validation_errors"], list)
    assert "registration_validated" in entry
    assert entry["registration_validated"] is True


def test_register_agent_validates_execution_limits_bounds(client, app):
    """DRR-T037: execution_limits values are clamped to sane min/max during registration."""
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("agent.routes.system.agent_repo") as mock_repo:
            payload = {
                "name": "limits-agent",
                "url": "http://limits-agent:5000",
                "role": "worker",
                "worker_roles": ["repair"],
                "capabilities": ["repair.diagnose"],
                "execution_limits": {
                    "max_parallel_tasks": 9999,  # above max 32
                    "max_runtime_seconds": 1,     # below min 30
                    "max_workspace_mb": 10,        # below min 64
                },
            }
            response = client.post("/register", json=payload)
            assert response.status_code == 200
            saved = mock_repo.save.call_args[0][0]
            # max_parallel_tasks clamped to max 32
            assert saved.execution_limits["max_parallel_tasks"] == 32
            # max_runtime_seconds clamped to minimum 30
            assert saved.execution_limits["max_runtime_seconds"] == 30
            # max_workspace_mb clamped to minimum 64
            assert saved.execution_limits["max_workspace_mb"] == 64


def test_list_agents_exposes_liveness_contract(client, admin_auth_header):
    from agent.db_models import AgentInfoDB
    from agent.repository import agent_repo

    agent_repo.save(
        AgentInfoDB(
            url="http://worker-one:5000",
            name="worker-one",
            role="worker",
            worker_roles=["coder"],
            capabilities=["coding"],
            execution_limits={"max_parallel_tasks": 2, "current_load": 1, "routing_signals": {"success_rate": 0.9}},
            status="online",
        )
    )

    response = client.get("/agents", headers=admin_auth_header)
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload[0]["liveness"]["status"] == "online"
    assert payload[0]["available_for_routing"] is True
    assert payload[0]["liveness"]["available_for_routing"] is True
    assert payload[0]["current_load"] == 1
    assert payload[0]["reported_load"] == 1
    assert payload[0]["scheduler_load"] == 0
    assert payload[0]["routing_signals"]["success_rate"] == 0.9


def test_agent_directory_uses_effective_load_max_of_reported_and_scheduler():
    from agent.db_models import AgentInfoDB
    from agent.services.agent_registry_service import AgentRegistryService

    agent = AgentInfoDB(
        url="http://worker-two:5000",
        name="worker-two",
        role="worker",
        worker_roles=["coder"],
        capabilities=["coding"],
        execution_limits={"max_parallel_tasks": 2, "current_load": 0, "scheduler_load": 2},
        status="online",
        registration_validated=True,
    )
    entry = AgentRegistryService().build_directory_entry(agent=agent, timeout=60.0)
    assert entry["reported_load"] == 0
    assert entry["scheduler_load"] == 2
    assert entry["current_load"] == 2
    assert entry["available_for_routing"] is False


def test_register_agent_rejects_invalid_role(client, app):
    payload = {"name": "bad-agent", "url": "http://bad-agent:5000", "role": "observer"}
    response = client.post("/register", json=payload)
    assert response.status_code == 400
    assert response.json["message"] == "invalid_agent_role"


def test_register_agent_requires_worker_capabilities(client, app):
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        payload = {"name": "plain-worker", "url": "http://plain-worker:5000", "role": "worker"}
        response = client.post("/register", json=payload)
        assert response.status_code == 400
        assert response.json["message"] == "worker_capabilities_required"


def test_registration_runtime_state_tracks_failed_attempts(monkeypatch):
    from agent.services.background import registration as registration_mod

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self.daemon = daemon

        def start(self):
            if self._target:
                self._target()

    registration_mod.reset_registration_state()
    monkeypatch.setattr(registration_mod.settings, "role", "worker")
    monkeypatch.setattr(registration_mod.settings, "hub_can_be_worker", False)
    monkeypatch.setattr(registration_mod.settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(registration_mod.settings, "port", 5001)

    monkeypatch.setattr("agent.common.context.shutdown_requested", False)
    monkeypatch.setattr("agent.services.background.registration.register_with_hub", lambda **kwargs: False)
    monkeypatch.setattr("agent.services.background.registration.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.services.background.registration.threading.Thread", _ImmediateThread)

    app = SimpleNamespace(
        config={
            "AGENT_NAME": "worker-alpha",
            "AGENT_TOKEN": "token-alpha",
        }
    )
    registration_mod.start_registration_thread(app)

    state = registration_mod.get_registration_state()
    assert state["enabled"] is True
    assert state["thread_started"] is True
    assert state["registered_as"] == "worker-alpha"
    assert state["running"] is False
    assert int(state["attempts"]) == 10
    assert state["last_error"] == "registration_failed"


def test_registration_runtime_state_retries_after_successful_registration(monkeypatch):
    import agent.common.context
    from agent.services.background import registration as registration_mod

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self.daemon = daemon

        def start(self):
            if self._target:
                self._target()

    registration_mod.reset_registration_state()
    monkeypatch.setattr(registration_mod.settings, "role", "worker")
    monkeypatch.setattr(registration_mod.settings, "hub_can_be_worker", False)
    monkeypatch.setattr(registration_mod.settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(registration_mod.settings, "port", 5001)
    monkeypatch.setattr(registration_mod.settings, "agent_offline_timeout", 120)
    monkeypatch.setattr("agent.common.context.shutdown_requested", False)

    results = iter([True, False, True])
    monkeypatch.setattr(
        "agent.services.background.registration.register_with_hub",
        lambda **kwargs: next(results),
    )

    sleep_calls: list[int] = []

    def _fake_sleep(seconds: int) -> bool:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            agent.common.context.shutdown_requested = True
            return False
        return True

    monkeypatch.setattr("agent.services.background.registration._sleep_with_shutdown", _fake_sleep)
    monkeypatch.setattr("agent.services.background.registration.threading.Thread", _ImmediateThread)

    app = SimpleNamespace(
        config={
            "AGENT_NAME": "worker-alpha",
            "AGENT_TOKEN": "token-alpha",
        }
    )
    registration_mod.start_registration_thread(app)

    state = registration_mod.get_registration_state()
    assert state["thread_started"] is True
    assert state["registered_as"] == "worker-alpha"
    assert state["running"] is False
    assert int(state["attempts"]) == 3
    assert state["last_success_at"] is not None
    assert state["registered_capabilities"] == []
    assert sleep_calls == [60, 2, 60]


def test_registration_uses_file_managed_service_token_and_runtime_metadata(
    monkeypatch,
    tmp_path,
):
    from agent.services.background import registration as registration_mod

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self.daemon = daemon

        def start(self):
            if self._target:
                self._target()

    token = "workflow-worker-service-token-0123456789abcdef"
    token_file = tmp_path / "workflow-worker-token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    bootstrap_token = "workflow-worker-registration-token-0123456789abcdef"
    bootstrap_file = tmp_path / "workflow-worker-registration-token"
    bootstrap_file.write_text(bootstrap_token, encoding="utf-8")
    bootstrap_file.chmod(0o600)
    captured = {}

    def register(**values):
        captured.update(values)
        return True

    registration_mod.reset_registration_state()
    monkeypatch.setattr(registration_mod.settings, "role", "worker")
    monkeypatch.setattr(registration_mod.settings, "hub_can_be_worker", False)
    monkeypatch.setattr(registration_mod.settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(registration_mod.settings, "port", 5001)
    monkeypatch.setattr("agent.common.context.shutdown_requested", False)
    monkeypatch.setattr(registration_mod, "register_with_hub", register)
    monkeypatch.setattr(registration_mod, "_sleep_with_shutdown", lambda _seconds: False)
    monkeypatch.setattr(registration_mod.threading, "Thread", _ImmediateThread)
    app = Flask(__name__)
    app.config.update(
        AGENT_NAME="worker-alpha",
        AGENT_TOKEN=None,
        AGENT_TOKEN_FILE=str(token_file),
        REGISTRATION_TOKEN_FILE=str(bootstrap_file),
    )
    app.extensions["workflow_adapter_worker_registration"] = {
        "capabilities": ["workflow.adapter.langgraph"],
        "runtime_targets": [{"runtime_id": "langgraph"}],
    }

    registration_mod.start_registration_thread(app)

    assert captured["token"] == token
    assert captured["registration_token"] == bootstrap_token
    assert captured["registration_token"] != captured["token"]
    assert captured["capabilities"] == ["workflow.adapter.langgraph"]
    assert captured["runtime_targets"] == [{"runtime_id": "langgraph"}]
    assert registration_mod.get_registration_state()[
        "registered_capabilities"
    ] == ["workflow.adapter.langgraph"]


def test_strict_registration_route_binds_keyring_identity_and_service_token(
    client,
    app,
    tmp_path,
):
    import json

    from agent.services.workflow_worker_service_auth import (
        WORKER_REGISTRATION_KEYRING_SCHEMA,
    )

    bootstrap_token = "alpha-registration-bootstrap-0123456789abcdef"
    service_token = "alpha-workflow-service-token-0123456789abcdef"
    keyring = tmp_path / "worker-registration-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    "worker-alpha": {
                        "worker_url": "http://worker-alpha:5000",
                        "registration_token": bootstrap_token,
                        "service_token_sha256": hashlib.sha256(
                            service_token.encode("utf-8")
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"alpha-session-signing-key-0123456789abcdef"
                        ).hexdigest(),
                        "allowed_capabilities": [
                            "planning",
                            "source_analysis",
                            "workflow.adapter.native",
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    keyring.chmod(0o440)
    app.config.update(
        AGENT_TOKEN="hub-administrator-service-token-0123456789abcdef",
        AGENT_TOKEN_FILE="",
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(keyring),
    )

    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        with patch("agent.routes.system.agent_repo") as mock_repo:
            mock_repo.get_all.return_value = []
            response = client.post(
                "/register",
                json={
                    "name": "worker-alpha",
                    "url": "http://worker-alpha:5000",
                    "role": "worker",
                    "token": service_token,
                    "registration_token": bootstrap_token,
                    "capabilities": [
                        "source_analysis",
                        "workflow.adapter.native",
                    ],
                    "worker_roles": ["coder"],
                },
            )

    assert response.status_code == 200
    saved = mock_repo.save.call_args.args[0]
    assert saved.name == "worker-alpha"
    assert saved.url == "http://worker-alpha:5000"
    assert saved.token == service_token
    assert saved.registration_provenance == "strict_registration_keyring_v1"
    assert saved.authorized_capabilities == [
        "planning",
        "source_analysis",
        "workflow.adapter.native",
    ]
    assert saved.capabilities == [
        "source_analysis",
        "workflow.adapter.native",
    ]


def test_strict_registration_route_rejects_foreign_bootstrap_identity(
    client,
    app,
    tmp_path,
):
    import json

    from agent.services.workflow_worker_service_auth import (
        WORKER_REGISTRATION_KEYRING_SCHEMA,
    )

    alpha_bootstrap = "alpha-registration-bootstrap-0123456789abcdef"
    beta_bootstrap = "beta-registration-bootstrap-0123456789abcdefg"
    keyring = tmp_path / "worker-registration-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    "worker-alpha": {
                        "worker_url": "http://worker-alpha:5000",
                        "registration_token": alpha_bootstrap,
                        "service_token_sha256": hashlib.sha256(
                            b"alpha-workflow-service-token-0123456789abcdef"
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"alpha-session-signing-key-0123456789abcdef"
                        ).hexdigest(),
                        "allowed_capabilities": ["workflow.adapter.native"],
                    },
                    "worker-beta": {
                        "worker_url": "http://worker-beta:5000",
                        "registration_token": beta_bootstrap,
                        "service_token_sha256": hashlib.sha256(
                            b"beta-workflow-service-token-0123456789abcdefg"
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"beta-session-signing-key-0123456789abcdefg"
                        ).hexdigest(),
                        "allowed_capabilities": ["workflow.adapter.langgraph"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    keyring.chmod(0o440)
    app.config.update(
        AGENT_TOKEN="hub-administrator-service-token-0123456789abcdef",
        AGENT_TOKEN_FILE="",
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(keyring),
    )

    with patch("agent.routes.system.agent_repo") as mock_repo:
        mock_repo.get_all.return_value = []
        response = client.post(
            "/register",
            json={
                "name": "worker-alpha",
                "url": "http://worker-alpha:5000",
                "role": "worker",
                "token": "alpha-workflow-service-token-0123456789abcdef",
                "registration_token": beta_bootstrap,
                "capabilities": ["workflow.adapter.native"],
                "worker_roles": ["coder"],
            },
        )

    assert response.status_code == 401
    assert response.get_json()["data"]["reason_code"] == (
        "workflow_worker_registration_identity_denied"
    )
    mock_repo.save.assert_not_called()
