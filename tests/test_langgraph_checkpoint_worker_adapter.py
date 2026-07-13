from __future__ import annotations

import json
import urllib.request

import pytest

from agent.providers.lc_lg import LangGraphProviderConfig
from ananta_contracts.langgraph_checkpoint import (
    LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA,
    LangGraphCheckpointBinding,
    LangGraphCheckpointSnapshot,
)
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.langgraph_checkpoint_adapter import (
    HttpLangGraphCheckpointGateway,
    LangGraphCheckpointGatewayError,
    LangGraphHubOwnedCheckpointer,
)
from worker.adapters.workflow_adapter_base import WorkerError

_SERVICE_TOKEN = "langgraph-test-service-token-00000001"


def _binding() -> LangGraphCheckpointBinding:
    return LangGraphCheckpointBinding.from_mapping(
        {
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "step_id": "task-a",
            "task_id": "task-a",
            "plan_hash": "plan-a",
            "policy_version": "policy-a",
            "fencing_token": 7,
            "authorization_envelope": {"schema": "signed-envelope"},
        }
    )


def _snapshot(revision: int = 1) -> LangGraphCheckpointSnapshot:
    return LangGraphCheckpointSnapshot.from_mapping(
        {
            "schema": "ananta.langgraph_checkpoint_snapshot.v1",
            "checkpoint": {"id": "checkpoint-a", "channel_values": {}},
            "metadata": {"source": "loop"},
            "pending_writes": [["node-task", "messages", {"value": "ok"}]],
            "config": {
                "configurable": {
                    "thread_id": "task-a",
                    "checkpoint_ns": "",
                    "checkpoint_id": "checkpoint-a",
                    "ananta_checkpoint_revision": revision,
                }
            },
            "parent_config": None,
            "revision": revision,
            "signed_checkpoint_ref": f"wfc-{revision}",
        }
    )


class _Gateway:
    def __init__(self) -> None:
        self.latest: LangGraphCheckpointSnapshot | None = None
        self.calls: list[str] = []

    def get(self, **_values):
        self.calls.append("get")
        return self.latest

    def list(self, **_values):
        self.calls.append("list")
        return (self.latest,) if self.latest is not None else ()

    def put(self, **values):
        self.calls.append(f"put:{values['expected_revision']}")
        self.latest = _snapshot(values["expected_revision"] + 1)
        return self.latest

    def put_writes(self, **values):
        self.calls.append(f"put_writes:{values['expected_revision']}")
        self.latest = _snapshot(values["expected_revision"] + 1)
        return self.latest


def _config() -> dict:
    return {"configurable": {"thread_id": "task-a", "checkpoint_ns": ""}}


def test_base_checkpointer_maps_get_put_list_and_writes() -> None:
    gateway = _Gateway()
    saver = LangGraphHubOwnedCheckpointer(gateway=gateway, binding=_binding())

    stored_config = saver.put(
        _config(),
        {"id": "checkpoint-a", "channel_values": {}},
        {"source": "loop"},
        {},
    )
    found = saver.get_tuple(stored_config)
    listed = list(saver.list(_config(), filter={"source": "loop"}, limit=10))
    saver.put_writes(stored_config, [("messages", {"value": "ok"})], "node-task")

    assert found is not None
    assert found.checkpoint["id"] == "checkpoint-a"
    assert found.pending_writes == [("node-task", "messages", {"value": "ok"})]
    assert len(listed) == 1
    assert gateway.calls == ["get", "put:0", "get", "list", "get", "put_writes:1"]
    with pytest.raises(LangGraphCheckpointGatewayError, match="langgraph_checkpoint_delete_forbidden"):
        saver.delete_thread("task-a")


def test_http_gateway_sends_bearer_in_header_and_never_uses_query(monkeypatch) -> None:
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read(_limit: int) -> bytes:
            return json.dumps(
                {
                    "data": {
                        "schema": LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA,
                        "snapshot": None,
                    }
                }
            ).encode("utf-8")

    def fake_urlopen(request: urllib.request.Request, **_kwargs):
        captured.update(
            {
                "method": request.method,
                "url": request.full_url,
                "authorization": request.headers.get("Authorization"),
                "body": json.loads(bytes(request.data or b"{}").decode("utf-8")),
            }
        )
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    gateway = HttpLangGraphCheckpointGateway(
        hub_url="http://hub:8000",
        bearer_token=_SERVICE_TOKEN,
    )

    assert gateway.get(binding=_binding(), config=_config()) is None
    assert captured["method"] == "POST"
    assert "?" not in captured["url"]
    assert captured["authorization"] == f"Bearer {_SERVICE_TOKEN}"
    assert captured["body"]["binding"]["authorization_envelope"] == {"schema": "signed-envelope"}


def test_http_gateway_environment_requires_safe_absolute_token_file(
    tmp_path,
) -> None:
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(_SERVICE_TOKEN + "\n", encoding="utf-8")
    token_file.chmod(0o440)

    gateway = HttpLangGraphCheckpointGateway.from_environment(
        {
            "ANANTA_LANGGRAPH_HUB_URL": "http://ai-agent-hub:5000",
            "ANANTA_LANGGRAPH_HUB_TOKEN_FILE": str(token_file),
        }
    )

    assert isinstance(gateway, HttpLangGraphCheckpointGateway)
    token_file.chmod(0o660)
    with pytest.raises(ValueError, match="unsafe"):
        HttpLangGraphCheckpointGateway.from_environment(
            {
                "ANANTA_LANGGRAPH_HUB_URL": "http://ai-agent-hub:5000",
                "ANANTA_LANGGRAPH_HUB_TOKEN_FILE": str(token_file),
            }
        )


def test_http_gateway_environment_stays_disabled_without_both_settings() -> None:
    assert HttpLangGraphCheckpointGateway.from_environment({}) is None
    with pytest.raises(ValueError, match="absolute token file"):
        HttpLangGraphCheckpointGateway.from_environment({"ANANTA_LANGGRAPH_HUB_URL": "http://ai-agent-hub:5000"})


def test_hub_owned_adapter_requires_gateway_and_complete_delegation_binding() -> None:
    config = LangGraphProviderConfig(
        enabled=True,
        mode="local_live",
        checkpoint_policy="hub_owned",
        state_policy="hub_owned",
    )
    adapter = LangGraphAdapter(config)
    with pytest.raises(WorkerError, match="Hub-owned checkpoint policy"):
        adapter._get_checkpointer(task_id="task-a", payload={})  # noqa: SLF001

    gateway = _Gateway()
    configured = LangGraphAdapter(config, checkpoint_gateway=gateway)
    payload = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "task-a",
        "plan_hash": "plan-a",
        "policy_version": "policy-a",
        "fencing_token": 7,
        "authorization_envelope": {"schema": "signed-envelope"},
    }
    assert isinstance(
        configured._get_checkpointer(task_id="task-a", payload=payload),  # noqa: SLF001
        LangGraphHubOwnedCheckpointer,
    )


def test_production_checkpoint_policy_rejects_unsigned_json_resume_tokens() -> None:
    adapter = LangGraphAdapter(
        LangGraphProviderConfig(
            enabled=True,
            mode="local_live",
            checkpoint_policy="hub_owned",
            state_policy="hub_owned",
        ),
        checkpoint_gateway=_Gateway(),
    )

    result = adapter.execute(
        task_id="task-a",
        task_type="agent_workflow",
        payload={},
        resume_token=json.dumps(
            {
                "schema": "ananta.langgraph_local_resume.v1",
                "task_id": "task-a",
                "graph_id": "tampered",
            }
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "unsigned_resume_token_forbidden"


def test_delegated_provider_context_is_forwarded_and_binding_checked(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(self, *, prompt, payload, budget, model_provider_ref):
        del self, prompt, budget, model_provider_ref
        captured.update(payload)
        return "ok"

    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.SimplexRunner.run",
        fake_run,
    )
    monkeypatch.setattr(
        "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
        staticmethod(lambda: False),
    )
    adapter = LangGraphAdapter(
        LangGraphProviderConfig(
            enabled=True,
            mode="local_live",
            checkpoint_policy="none",
            metadata={
                "allow_manual_walker_fallback": True,
                "manual_walker_fallback_mode": "development",
            },
        )
    )
    provider_context = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "policy_version": "policy-a",
        "plan_hash": "plan-a",
        "prompt_version": "prompt-a",
        "require_hub_provider_budget": True,
        "provider_transport_mode": "hub_bound",
        "provider_decision_reason": "hub_provider_policy_selected",
        "provider_binding_id": "provider-binding:test",
        "selected_provider_id": "lmstudio",
        "selected_model_id": "model-a",
    }
    payload = {
        **{
            key: provider_context[key]
            for key in (
                "tenant_id",
                "workflow_id",
                "run_id",
                "policy_version",
                "plan_hash",
            )
        },
        "provider_context": provider_context,
        "graph_descriptor": {
            "nodes": [{"id": "llm", "kind": "llm"}, {"id": "end", "kind": "end"}],
            "edges": [{"from": "llm", "to": "end"}],
        },
    }

    result = adapter.execute(
        task_id="task-a",
        task_type="agent_workflow",
        payload=payload,
    )

    assert result.status == "success", result
    assert captured["provider_context"] == provider_context
    with pytest.raises(WorkerError) as exc_info:
        adapter._bound_provider_context(  # noqa: SLF001
            {**payload, "provider_context": {**provider_context, "plan_hash": "tampered"}}
        )
    assert exc_info.value.reason_code == "provider_context_plan_hash_mismatch"
