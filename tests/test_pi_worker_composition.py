"""Deployment configuration reaches the existing Worker task adapter, opt-in only."""

from unittest.mock import Mock

import pytest

from tests.test_pi_coding_agent_provider import Runner, provider
from tests.test_pi_hub_budget_composition import composition
from tests.test_pi_native_node import task_command
from worker.runtime.native_graph import pi_composition
from worker.runtime.native_graph.authorization import HubBackedNativeAuthorizationVerifier
from worker.runtime.native_graph.composition import build_native_graph_worker_task_adapter
from worker.runtime.native_graph.handlers import NativeTaskKindHandlers, UnavailableNativeNodeHandler
from worker.runtime.native_graph.pi_node import NativePiNodeHandler
from worker.runtime.workflow_adapter_runtime_composition import build_workflow_adapter_worker_runtime
from worker.runtime.workflow_adapter_worker_profile import (
    WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA,
    WorkflowAdapterWorkerProfile,
)


def configuration(tmp_path, *, enabled):
    workspace, runtime = tmp_path / "workspace", tmp_path / "runtime"
    workspace.mkdir()
    runtime.mkdir()
    credential = tmp_path / "credential"
    credential.write_text("synthetic-private-key")
    credential.chmod(0o400)
    return {
        "worker_runtime": {
            "workspace_root": str(workspace),
            "native_graph": {
                "enabled": True,
                "allowed_task_types": ["coding", "pi_coding_agent"],
                "capabilities": ["coding.agent.pi", "text_generation"],
                "pi": {
                    "enabled": enabled,
                    "runtime_root": str(runtime),
                    "credential_files": {"pi-primary": str(credential)},
                },
            },
        }
    }


@pytest.mark.parametrize("enabled", [True, False])
def test_production_native_factory_uses_pi_only_after_explicit_configuration(tmp_path, monkeypatch, enabled):
    client, context, _ = composition()
    config = configuration(tmp_path, enabled=enabled)
    runner, shell = Runner(), Mock()
    monkeypatch.setattr(
        pi_composition,
        "NativePiNodeHandler",
        lambda **kwargs: NativePiNodeHandler(
            **kwargs,
            provider_factory=lambda **options: provider(tmp_path, runner, **options),
        ),
    )
    adapter = build_native_graph_worker_task_adapter(
        client=client,
        agent_config=config,
        executor=shell,
        authorization_verifier=HubBackedNativeAuthorizationVerifier(),
    )
    result = adapter.execute_task(
        {
            "id": "hub-task-1",
            "worker_execution_context": {
                "schema": "ananta.native_graph_worker_context.v1",
                "runtime_path": "native_graph_node",
                "native_node_command": task_command(context).to_dict(),
            },
        }
    )
    assert not shell.execute.called
    if enabled:
        assert result.status == "completed" and len(runner.calls) == 1
    else:
        assert result.status == "failed" and result.reason_code == "pi_disabled" and not runner.calls


def test_legacy_native_profile_projection_does_not_grow_a_pi_field():
    native = {"enabled": True, "allowed_task_types": ["coding"], "capabilities": ["tool_calling"]}
    profile = WorkflowAdapterWorkerProfile.model_validate(
        {
            "schema": WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA,
            "worker_runtime": {"native_graph": native},
        }
    )
    assert profile.overlay({})["worker_runtime"]["native_graph"] == native


def test_typed_worker_profile_round_trips_pi_configuration(tmp_path):
    native = configuration(tmp_path, enabled=True)["worker_runtime"]["native_graph"]
    profile = WorkflowAdapterWorkerProfile.model_validate(
        {
            "schema": WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA,
            "worker_runtime": {"native_graph": native},
        }
    )
    assert profile.overlay({})["worker_runtime"]["native_graph"] == native


@pytest.mark.parametrize("change", ["disabled-native", "missing-kind", "missing-capability"])
def test_pi_enable_requires_explicit_native_task_and_capability_scope(tmp_path, change):
    native = configuration(tmp_path, enabled=True)["worker_runtime"]["native_graph"]
    if change == "disabled-native":
        native["enabled"] = False
    elif change == "missing-kind":
        native["allowed_task_types"] = ["coding"]
    else:
        native["capabilities"] = ["text_generation"]
    with pytest.raises(ValueError, match="pi_native_deployment_scope_required"):
        WorkflowAdapterWorkerProfile.model_validate(
            {
                "schema": WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA,
                "worker_runtime": {"native_graph": native},
            }
        )


def test_task_kind_router_preserves_existing_handler_and_never_falls_back_for_pi():
    default, command = Mock(), Mock()
    handlers = {"pi_coding_agent": UnavailableNativeNodeHandler("pi_disabled")}
    router = NativeTaskKindHandlers(default=default, handlers=handlers)
    handlers.clear()
    command.node.task_kind = "coding"
    assert router.execute(command, hub_task_id="task") is default.execute.return_value
    default.execute.reset_mock()
    command.node.task_kind = "pi_coding_agent"
    with pytest.raises(ValueError, match="pi_disabled"):
        router.execute(command, hub_task_id="task")
    assert not default.execute.called


@pytest.mark.parametrize("enabled", [True, False])
def test_runtime_registration_never_advertises_disabled_pi(tmp_path, enabled):
    client, _, _ = composition()
    runtime = build_workflow_adapter_worker_runtime(
        agent_config=configuration(tmp_path, enabled=enabled), client=client,
        native_executor=Mock(), native_authorization_verifier=HubBackedNativeAuthorizationVerifier(),
    )
    assert ("coding.agent.pi" in runtime.capabilities) is enabled
    assert "workflow.adapter.native" in runtime.capabilities
