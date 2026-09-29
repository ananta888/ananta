from __future__ import annotations

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope, ModelPolicy, NetworkScope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_http_client import HermesClientError


def _env(*, capabilities: list[str], cloud_allowed: bool = False, allow_all_network: bool = False) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id="t-sec",
        actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=capabilities),
        context_envelope_ref="ctx:1",
        audit_correlation_id="audit:1",
        model_policy=ModelPolicy(cloud_allowed=cloud_allowed),
        network_scope=NetworkScope(allow_all=allow_all_network),
    )


class _Client:
    def __init__(self, payload: str = "") -> None:
        self.calls = 0
        self.payload = payload or '{"status":"success","artifact_type":"plan","summary":"ok","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'

    def health(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    def chat_completions(self, **_: object) -> dict[str, object]:
        self.calls += 1
        return {"choices": [{"message": {"content": self.payload}}]}


def test_command_execute_routed_to_hermes_denied() -> None:
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capabilities=["shell_execute"]), context_blocks=[ContextBlock("task", "a", "hub", content="x")])
    assert res.status.value == "denied"


def test_patch_apply_routed_to_hermes_denied() -> None:
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    res = adapter.patch_propose(_env(capabilities=["patch_apply"]), context_blocks=[ContextBlock("task", "a", "hub", content="x")])
    assert res.status.value == "denied"


def test_cloud_endpoint_blocked_when_cloud_not_allowed() -> None:
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="https://api.hermes.example", default_model="m"), client=_Client())  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capabilities=["planning"], cloud_allowed=False), context_blocks=[ContextBlock("task", "a", "hub", content="x")])
    assert res.status.value == "denied"


def test_sensitive_context_not_sent_to_cloud() -> None:
    client = _Client()
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, cloud_allowed=True, base_url="https://api.hermes.example", default_model="m"), client=client)  # type: ignore[arg-type]
    res = adapter.plan_only(
        _env(capabilities=["planning"], cloud_allowed=True),
        context_blocks=[ContextBlock("task", "s1", "hub", sensitivity=ContextSensitivity.secret, content="secret")],
    )
    assert res.status.value == "denied"
    assert client.calls == 0


def test_malformed_output_parse_error() -> None:
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, strict_json_required=False, base_url="http://localhost:1", default_model="m"), client=_Client(payload="not-json"))  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capabilities=["planning"]), context_blocks=[ContextBlock("task", "a", "hub", content="x")])
    assert res.status.value == "failed"
    assert "parse_error" in res.summary


def test_modified_files_claim_blocked() -> None:
    payload = '{"status":"success","artifact_type":"plan","summary":"modified files","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, strict_json_required=False, base_url="http://localhost:1", default_model="m"), client=_Client(payload=payload))  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capabilities=["planning"]), context_blocks=[ContextBlock("task", "a", "hub", content="x")])
    assert res.status.value == "failed"


def test_feature_flag_disabled_prevents_http_call() -> None:
    client = _Client()
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=False, base_url="http://localhost:1", default_model="m"), client=client)  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capabilities=["planning"]), context_blocks=[ContextBlock("task", "a", "hub", content="x")])
    assert res.status.value == "degraded"
    assert client.calls == 0
