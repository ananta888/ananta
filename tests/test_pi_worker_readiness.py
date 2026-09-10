"""Readiness is an existing registered-Worker observation, never inference authority."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.cli_backends.pi_readiness import pi_readiness_projection
from tests.test_pi_coding_agent_provider import runtime as installed_runtime


def registered_runtime():
    return {
        "capabilities": ["workflow.adapter.native", "coding.agent.pi"],
        "runtime_targets": [{
            "runtime_id": "ananta-native", "runtime_kind": "docker_container", "adapter_id": "native",
            "allowed_capabilities": ["coding.agent.pi"],
        }],
        "private": "must never be projected",
    }


def test_registered_pi_is_ready_for_assignment_not_a_verified_inference_or_auth_grant():
    installation, runtime = installed_runtime(), registered_runtime()
    before = copy.deepcopy((installation, runtime))
    value = pi_readiness_projection(installation=installation, runtime=runtime)
    assert value["state"] == "ready_for_assignment" and value["verified_version"] == "0.85.1"
    assert value["native_registered"] is True and value["auth_status"] == "profile_configured_unverified"
    assert value["inference_verified"] is False and value["requires_hub_assignment"] is True
    assert value["free_class"] == "open_source_byok" and value["inference_cost"] == "provider_dependent"
    assert value["global_auto_routing"] is False
    assert value["capabilities"] == {
        "headless": True, "structured_output": True, "tools": False, "mcp": False,
        "workspace_write": False, "session_resume": False,
    }
    assert "private" not in str(value) and "pinned" not in str(value)
    assert (installation, runtime) == before


@pytest.mark.parametrize("mutation,expected", [
    ("missing", "not_installed"), ("truthy_installed", "not_installed"),
    ("status", "version_unverified"), ("version", "version_unverified"),
    ("probe", "version_unverified"), ("boolean_rc", "version_unverified"),
    ("native", "native_not_registered"), ("target", "native_not_registered"),
    ("string_capabilities", "native_not_registered"), ("missing_target", "native_not_registered"),
])
def test_missing_or_unverified_worker_observation_does_not_become_ready(mutation, expected):
    installation, runtime = installed_runtime(), registered_runtime()
    if mutation == "missing":
        installation = {}
    elif mutation == "truthy_installed":
        installation["installed"] = "true"
    elif mutation == "status":
        installation["status"] = "error"
    elif mutation == "version":
        installation["version"] = "0.85.2"
    elif mutation == "probe":
        installation["version_probe"] = {"rc": 0, "stdout": "private", "stderr": "private"}
    elif mutation == "boolean_rc":
        installation["version_probe"]["rc"] = False
    elif mutation == "native":
        runtime["capabilities"].remove("coding.agent.pi")
    elif mutation == "target":
        runtime["runtime_targets"][0]["runtime_kind"] = "foreign"
    elif mutation == "string_capabilities":
        runtime["capabilities"] = "coding.agent.pi"
    else:
        runtime["runtime_targets"] = []
    value = pi_readiness_projection(installation=installation, runtime=runtime)
    assert value["state"] == expected and value["inference_verified"] is False
    assert "private" not in str(value)


def test_worker_status_reuses_installed_runtime_metadata_without_new_execution(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "worker")
    provisioner = Mock()
    provisioner.status.return_value = installed_runtime()
    monkeypatch.setattr("agent.cli_backends.provisioning.get_cli_backend_provisioner", lambda: provisioner)
    client.application.extensions["workflow_adapter_worker_registration"] = registered_runtime()
    response = client.post("/api/sgpt/backends/pi/provision", json={"action": "status"}, headers=admin_auth_header)
    assert response.status_code == 200
    value = response.json["data"]["native_execution"]
    assert value["state"] == "ready_for_assignment" and value["inference_verified"] is False
    provisioner.status.assert_called_once_with("pi")
    provisioner.install.assert_not_called()


@pytest.mark.parametrize("enabled", [True, False])
def test_actual_worker_composition_projects_only_explicitly_enabled_pi(tmp_path, enabled):
    from flask import Flask

    from tests.test_pi_hub_budget_composition import composition
    from tests.test_pi_worker_composition import configuration
    from worker.runtime.native_graph.authorization import HubBackedNativeAuthorizationVerifier
    from worker.runtime.workflow_adapter_runtime_composition import (
        initialize_workflow_adapter_worker_runtime,
        workflow_adapter_registration_metadata,
    )

    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = configuration(tmp_path, enabled=enabled)
    client, _, _ = composition()
    initialize_workflow_adapter_worker_runtime(
        app, client=client, native_executor=Mock(),
        native_authorization_verifier=HubBackedNativeAuthorizationVerifier(),
    )
    value = pi_readiness_projection(
        installation=installed_runtime(), runtime=workflow_adapter_registration_metadata(app),
    )
    assert value["native_registered"] is enabled
    assert value["state"] == ("ready_for_assignment" if enabled else "native_not_registered")
    assert value["inference_verified"] is False and "synthetic-private-key" not in str(value)


def test_hub_forwards_worker_readiness_without_probing_its_local_installation(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "hub")
    worker = SimpleNamespace(name="worker-1", url="http://worker-1:5000", token="synthetic-private-service-token")
    monkeypatch.setattr("agent.routes.sgpt._registered_worker", lambda url: worker if url == worker.url else None)
    local = Mock(side_effect=AssertionError("must never probe local Hub installation"))
    monkeypatch.setattr("agent.cli_backends.provisioning.get_cli_backend_provisioner", local)
    value = pi_readiness_projection(installation=installed_runtime(), runtime=registered_runtime())
    gateway = Mock()
    gateway.forward_task.return_value = {"status": "success", "data": {"native_execution": value}}
    monkeypatch.setattr("agent.routes.sgpt.get_worker_gateway", lambda: gateway)
    response = client.post(
        "/api/sgpt/backends/pi/provision",
        json={"action": "status", "worker_url": worker.url}, headers=admin_auth_header,
    )
    assert response.status_code == 200 and response.json["data"]["native_execution"] == value
    assert response.json["data"]["worker"] == {"name": "worker-1", "url": worker.url}
    assert "synthetic-private" not in str(response.json)
    assert gateway.forward_task.call_args.args[:3] == (
        worker.url, "/api/sgpt/backends/pi/provision", {"action": "status"},
    )
    local.assert_not_called()
