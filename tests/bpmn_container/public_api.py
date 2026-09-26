"""Isolated composition of production login and workflow HTTP boundaries."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from unittest.mock import patch

from bpmn_container.transport import HUB_URL, request_json


class SyntheticAdmission:
    """Explicit test-only port; never reads or writes production release evidence."""

    allowed = True

    def evaluate(self, *, runtime_id, **_values):
        allowed = self.allowed and runtime_id == "ananta-native"
        return allowed, "runtime_release_synthetic_admitted" if allowed else "runtime_release_synthetic_denied"


class PublicApi:
    def __init__(self, hub):
        from werkzeug.security import generate_password_hash

        from agent.config import settings
        from agent.db_models import UserDB
        from agent.routes import workflow_control_security
        from agent.routes.auth import auth_bp
        from agent.routes.visual_process import vp_bp
        from agent.services.repository_registry import get_repository_registry

        self.hub = hub
        self.admission = SyntheticAdmission()
        self.failures = []
        self.active_workflow_id = None
        self.password = secrets.token_urlsafe(32)
        settings.secret_key = secrets.token_urlsafe(48)
        hub.app.secret_key = settings.secret_key
        for username in ("bpmn-owner", "bpmn-foreign"):
            get_repository_registry().user_repo.save(
                UserDB(username=username, password_hash=generate_password_hash(self.password), role="user")
            )
        self.rebuild()
        # Replace only the process composition lookup, never auth/selection/
        # queue/result services. This keeps production configuration out of tests.
        self.lookup = patch.object(workflow_control_security, "get_workflow_backend_control_facade", lambda: self)
        self.lookup.start()
        hub.app.register_blueprint(auth_bp)
        hub.app.register_blueprint(vp_bp)

    def rebuild(self):
        from agent.services.local_workflow_backend import LocalWorkflowBackend
        from agent.services.workflow_control_composition import build_workflow_backend_control_facade
        from agent.services.workflow_control_persistence import (
            SQLAlchemyWorkflowCommandReplayNonceStore,
            SQLAlchemyWorkflowControlBindingStore,
        )

        self.facade = build_workflow_backend_control_facade(
            LocalWorkflowBackend(),
            bindings=SQLAlchemyWorkflowControlBindingStore(self.hub.engine),
            command_key_ring=self.hub.keys,
            command_replay_store=SQLAlchemyWorkflowCommandReplayNonceStore(self.hub.engine),
            authorization_grants=self.hub.grants,
            release_admission=self.admission,
        )

    def bind(self, principal):
        return ObservedBackend(self.facade.bind(principal), self.failures)

    def login(self, username="bpmn-owner"):
        result = request_json(HUB_URL + "/login", token="", payload={"username": username, "password": self.password})
        return result["data"]["access_token"]

    def call(self, path, *, token="", payload=None):
        return request_json(HUB_URL + "/api/visual-process/" + path, token=token, payload=payload)

    def drive(self, workflow_id, token):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            binding = self.facade.bindings.get(workflow_id)
            assert binding is not None
            self.hub.dispatch_ready(binding.run_id)
            self.hub.collect()
            reconciliation = self.facade.reconcile_active()
            assert not reconciliation.get("failed"), reconciliation
            status = self.call(f"workflow/{workflow_id}/status", token=token)
            if status["status"] in {"completed", "failed", "cancelled", "waiting_for_approval"}:
                return status
            time.sleep(0.025)
        raise AssertionError("public_workflow_terminal_timeout")

    def close(self):
        self.lookup.stop()

    def observation(self):
        binding = self.facade.bindings.get(self.active_workflow_id)
        if binding is None:
            return {"workflow_id": self.active_workflow_id, "binding_persisted": False}
        return {
            "workflow_id": binding.workflow_id,
            "binding_correlation_id": binding.request.correlation_id,
            "persisted_status": self.facade.bindings.last_status(binding.workflow_id),
            "tasks": [
                {
                    "task_id": task.id,
                    "status": task.status,
                    "result": (task.verification_status or {}).get("native_node_result"),
                }
                for task in self.hub.tasks_for(binding.run_id)
            ],
            "events": [
                {"event_type": event.event_type, "step_id": event.step_id, "correlation_id": event.correlation_id}
                for event in self.hub.events.list_events(tenant_id=binding.tenant_id, run_id=binding.run_id)
            ],
        }


class ObservedBackend:
    """Record bounded failure reasons hidden by production's public 503 response."""

    def __init__(self, backend, failures):
        self.backend = backend
        self.failures = failures

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def _call(self, operation, *args, **kwargs):
        try:
            return getattr(self.backend, operation)(*args, **kwargs)
        except Exception as exc:
            failure = {"operation": operation, "error": str(exc)[:500], "type": type(exc).__name__}
            if failure not in self.failures:
                self.failures.append(failure)
                print("PUBLIC_API_DIAGNOSTIC=" + str(failure), flush=True)
            raise

    def start_workflow(self, *args, **kwargs):
        return self._call("start_workflow", *args, **kwargs)

    def get_workflow_status(self, *args, **kwargs):
        return self._call("get_workflow_status", *args, **kwargs)


@dataclass(frozen=True)
class PublicScenario:
    """Inject XML and expected delegation without changing transport/composition."""

    name: str
    xml: str
    expected_nodes: tuple[str, ...]


def public_scenario(hub, scenario, *, rebuild=False, replay_start=True, graph_start=False):
    api = hub.public
    token = api.login()
    imported = api.call("bpmn/import", token=token, payload={"bpmn_xml": scenario.xml})
    assert imported["execution_support"]["supported"], imported
    graph = imported["graph"]
    graph["id"] = "public-" + scenario.name
    api.active_workflow_id = graph["id"]
    compiled = api.call("workflow-request", token=token, payload={"graph": graph})
    assert not compiled["errors"], compiled
    body = {
        **({"graph": graph} if graph_start else {"workflow_request": compiled["workflow_request"]}),
        "command_id": "public-start-" + scenario.name,
    }
    started = api.call("workflow/start", token=token, payload=body)
    assert started["status"] not in {"failed", "degraded", "unavailable"}, started
    workflow_id = started["workflow_id"]
    binding = api.facade.bindings.get(workflow_id)
    run_id = binding.run_id
    if rebuild:
        # Simulate loss of control-service objects; SQL bindings/checkpoints,
        # command receipts and ownership remain authoritative.
        api.rebuild()
    if replay_start:
        replay = api.call("workflow/start", token=token, payload=body)
        assert replay["workflow_id"] == workflow_id
        assert api.facade.bindings.get(workflow_id).run_id == run_id
    result = api.drive(workflow_id, token)
    assert result["status"] == "completed", result
    observations = hub.observations(run_id)
    assert sorted(row["node"] for row in observations) == sorted(scenario.expected_nodes), observations
    task_ids = [row["task_id"] for row in observations]
    if replay_start:
        api.call("workflow/start", token=token, payload=body)
        api.facade.reconcile_active()
        assert [task.id for task in hub.tasks_for(run_id)] == task_ids
    foreign = api.login("bpmn-foreign")
    denied(api, f"workflow/{workflow_id}/status", token=foreign, expected=404)
    denied(api, "workflow/start", token=foreign, payload=body, expected=409)
    return {
        "status": result["status"],
        "workflow_id": workflow_id,
        "tasks": observations,
        "login": "production_password_and_session_jwt",
        "start_replay_same_run": replay_start,
        "foreign_owner_denied": True,
        "start_input": "graph" if graph_start else "canonical_workflow_request",
        "sql_composition_recovery": rebuild,
        "release_admission": "explicit_synthetic_port",
    }


def denied(api, path, *, expected, token="", payload=None):
    import json
    import urllib.error

    try:
        api.call(path, token=token, payload=payload)
    except urllib.error.HTTPError as exc:
        with exc:
            body = json.loads(exc.read(65536))
        assert exc.code == expected, (exc.code, body)
        return body
    raise AssertionError("public_request_unexpectedly_admitted")


def public_auth_case(hub):
    import os

    from bpmn_container.fixtures import service

    api = hub.public
    before = len(hub.tasks.get_all())
    denied(api, "workflow/start", payload={}, expected=401)
    denied(api, "workflow-request", payload={}, expected=401)
    denied(api, "workflow/start", token="invalid", payload={}, expected=401)
    denied(api, "workflow/start", token=os.environ["BPMN_WORKER_TOKEN"], payload={}, expected=401)
    token = api.login()
    imported = api.call("bpmn/import", payload={"bpmn_xml": service()})
    graph = imported["graph"]
    graph["id"] = "public-release-denied"
    api.admission.allowed = False
    try:
        denied(api, "workflow/start", token=token, payload={"graph": graph}, expected=503)
    finally:
        api.admission.allowed = True
    assert len(hub.tasks.get_all()) == before
    return {"status": "denied_before_delegation", "new_tasks": 0, "release_denial_enforced": True}
