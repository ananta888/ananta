from __future__ import annotations

import builtins
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from agent import utils
from worker.runtime import workflow_adapter_runtime_composition as composition
from worker.runtime.workflow_adapter_worker_profile import (
    WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA,
    WorkflowAdapterWorkerProfileError,
    load_workflow_adapter_worker_profile,
)
from worker.runtime.workspace_resolver import (
    ConfiguredWorkerWorkspaceResolver,
    WorkerWorkspaceResolutionError,
)


class _FakeHubClient:
    pass


class _FakeLangGraphAdapter:
    def __init__(self, config, **values):
        self.config = config
        self.values = values

    def descriptor(self):
        return SimpleNamespace(
            enabled=True,
            status="ready",
            reason="ready",
            version="1.0.0",
        )


def _clear_hub_environment(monkeypatch) -> None:
    for name in (
        "ANANTA_WORKFLOW_HUB_URL",
        "ANANTA_WORKFLOW_HUB_TOKEN_FILE",
        "ANANTA_LANGGRAPH_HUB_URL",
        "ANANTA_LANGGRAPH_HUB_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_worker_composition_fails_closed_without_hub_authority(monkeypatch) -> None:
    _clear_hub_environment(monkeypatch)

    runtime = composition.build_workflow_adapter_worker_runtime(agent_config={})

    assert runtime.capabilities == ()
    assert runtime.runtime_targets == ()
    assert runtime.reason_codes == ("workflow_hub_gateway_not_configured",)


def test_worker_adapters_compose_without_hub_database_or_service_imports(
    monkeypatch,
) -> None:
    from worker.runtime.native_graph.authorization import (
        HubBackedNativeAuthorizationVerifier,
    )
    from worker.runtime.native_graph.composition import (
        build_native_graph_worker_task_adapter,
    )
    from worker.runtime.workflow_tool_pipeline_composition import (
        build_workflow_tool_pipeline,
    )

    forbidden = (
        "agent.database",
        "agent.db_models",
        "agent.repository",
        "agent.services.ananta_tool_registry_service",
        "agent.services.approval_request_service",
        "agent.services.native_worker_runtime_service",
        "agent.services.provider_invocation_middleware",
        "agent.services.worker_runtime_execution_adapter",
        "agent.services.worker_workspace_service",
    )
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden):
            raise AssertionError(f"Worker composition imported Hub service: {name}")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "__import__", guarded_import)
        client = _FakeHubClient()
        pipeline = build_workflow_tool_pipeline(client)
        native = build_native_graph_worker_task_adapter(
            client=client,
            agent_config={
                "worker_runtime": {
                    "native_graph": {
                        "enabled": True,
                        "allowed_task_types": ["coding"],
                        "capabilities": ["tool_calling"],
                    }
                }
            },
            executor=object(),
            authorization_verifier=HubBackedNativeAuthorizationVerifier(),
        )

    assert pipeline is not None
    assert native is not None


def test_worker_workspace_resolver_is_mount_bound_and_rejects_escape(
    tmp_path,
) -> None:
    root = tmp_path / "worker-root"
    root.mkdir()
    resolver = ConfiguredWorkerWorkspaceResolver(
        {"worker_runtime": {"workspace_root": str(root)}}
    )

    resolved = resolver.resolve_workspace_context(
        task={
            "id": "task-a",
            "worker_execution_context": {
                "workspace": {"scope_key": "goal-a"}
            },
        }
    )
    assert resolved.workspace_dir == (root / "goal-a").resolve()

    with pytest.raises(
        WorkerWorkspaceResolutionError,
        match="worker_workspace_outside_configured_root",
    ):
        resolver.resolve_workspace_context(
            task={
                "id": "task-b",
                "worker_execution_context": {
                    "workspace": {"output_dir": "../outside"}
                },
            }
        )


def test_worker_composition_advertises_only_successfully_built_adapters(
    monkeypatch,
) -> None:
    native_adapter = object()
    monkeypatch.setattr(
        composition,
        "build_native_graph_worker_task_adapter",
        lambda **_values: native_adapter,
    )
    monkeypatch.setattr(
        composition,
        "build_workflow_tool_pipeline",
        lambda _client: object(),
    )
    monkeypatch.setattr(composition, "LangGraphAdapter", _FakeLangGraphAdapter)
    monkeypatch.setattr(
        composition.HttpLangGraphCheckpointGateway,
        "from_environment",
        classmethod(lambda _cls: object()),
    )

    runtime = composition.build_workflow_adapter_worker_runtime(
        agent_config={
            "providers": {
                "langgraph": {
                    "enabled": True,
                    "mode": "local_live",
                    "checkpoint_policy": "hub_owned",
                    "allowed_tools": [],
                }
            }
        },
        client=_FakeHubClient(),
    )

    assert runtime.capabilities == (
        "workflow.adapter.langgraph",
        "workflow.adapter.native",
    )
    assert {
        (value["runtime_id"], value["adapter_id"])
        for value in runtime.runtime_targets
    } == {("ananta-native", "native"), ("langgraph", "langgraph")}
    assert runtime.reason_codes == ()


def test_worker_runtime_initializer_exposes_registration_metadata(
    monkeypatch,
) -> None:
    _clear_hub_environment(monkeypatch)
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {}

    runtime = composition.initialize_workflow_adapter_worker_runtime(app)

    assert app.extensions["workflow_adapter_task_consumer"] is runtime.consumer
    assert app.extensions["workflow_adapter_worker_registration"] == {
        "capabilities": [],
        "runtime_targets": [],
        "reason_codes": ["workflow_hub_gateway_not_configured"],
    }


def test_worker_registration_adds_workflow_runtime_health_metadata(monkeypatch) -> None:
    captured = {}

    def post(_url, data, **_values):
        captured.update(data)
        return {"status": "ok"}

    monkeypatch.setattr(utils, "_http_post", post)
    monkeypatch.setattr(utils.settings, "agent_url", "http://worker-a:5001")
    monkeypatch.setattr(utils.settings, "registration_token", None)

    assert utils.register_with_hub(
        "http://hub:5000",
        "worker-a",
        5001,
        "worker-token",
        capabilities=["workflow.adapter.langgraph"],
        runtime_targets=[
            {
                "runtime_target_id": "workflow-adapter-langgraph",
                "runtime_id": "langgraph",
                "adapter_id": "langgraph",
                "runtime_version": "1.0.0",
            }
        ],
    )
    assert "source_analysis" in captured["capabilities"]
    assert "workflow.adapter.langgraph" in captured["capabilities"]
    assert captured["runtime_targets"][0]["runtime_id"] == "langgraph"


def test_production_profile_is_typed_and_unknown_fields_fail_closed(
    tmp_path,
) -> None:
    profile = load_workflow_adapter_worker_profile(
        str(
            Path(__file__).parents[1]
            / "config/workflow_runtime/langgraph_worker_profile.v1.json"
        )
    )
    assert profile.schema_version == WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA
    assert profile.providers.langgraph is not None
    assert profile.providers.langgraph.checkpoint_policy == "hub_owned"
    assert profile.providers.langgraph.allowed_tools == []

    invalid = tmp_path / "invalid-profile.json"
    invalid.write_text(
        json.dumps(
            {
                "schema": WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA,
                "providers": {
                    "langgraph": {"enabled": True, "unknown": True}
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        WorkflowAdapterWorkerProfileError,
        match="workflow_adapter_worker_profile_invalid",
    ):
        load_workflow_adapter_worker_profile(str(invalid))
