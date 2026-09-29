from __future__ import annotations

from unittest.mock import patch

import pytest
from urllib import error

from agent.services.bridge_adapter_registry import BridgeAdapterRegistry
from agent.services.capability_registry import CapabilityRegistry
from agent.services.hermes_worker_profile import (
    HERMES_ALLOWED_CAPABILITIES,
    HERMES_DENIED_CAPABILITIES,
    get_default_hermes_profile,
)
from agent.services.tool_routing_service import ToolRoutingService
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_context_converter import convert_context_blocks_to_prompt
from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_http_client import HermesClientConfig, HermesClientError, HermesHttpClient
from worker.core.hermes_output_parser import parse_hermes_json_output
from worker.core.hermes_prompting import build_governed_system_prompt
from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.diagnostics import AuditEmitter


def _env(**overrides: object) -> ExecutionEnvelope:
    payload = {
        "task_id": "t-hermes",
        "actor_ref": "hub:test",
        "capability_grant": CapabilityGrant(capabilities=["planning"]),
        "context_envelope_ref": "ctx:1",
        "audit_correlation_id": "audit:1",
    }
    payload.update(overrides)
    return ExecutionEnvelope(**payload)


def test_hermes_profile_allowed_and_denied_sets_are_explicit() -> None:
    profile = get_default_hermes_profile()
    assert set(profile.allowed_capabilities) == set(HERMES_ALLOWED_CAPABILITIES)
    assert set(profile.denied_capabilities) == set(HERMES_DENIED_CAPABILITIES)
    assert profile.risk_class in {"medium", "high", "critical"}
    assert profile.requires_structured_output is True
    assert profile.max_context_policy == "bounded"
    assert profile.default_cloud_allowed is False


def test_hermes_profile_unknown_capability_denied() -> None:
    profile = get_default_hermes_profile()
    assert profile.supports_capability("unknown_hermes_cap") is False


def test_hermes_adapter_config_validation_and_redaction() -> None:
    cfg = HermesAdapterConfig(
        enabled=True,
        base_url="http://localhost:3000",
        default_model="hermes-model",
    )
    diag = cfg.diagnostics_view()
    assert diag["api_key_value"] == "[REDACTED]"
    assert "patch_apply" in cfg.blocked_task_kinds
    assert "command_execute" in cfg.blocked_task_kinds

    with pytest.raises(ValueError):
        HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="localhost:3000", default_model="m")
    with pytest.raises(ValueError):
        HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:3000", default_model="", timeout_seconds=10)
    with pytest.raises(ValueError):
        HermesAdapterConfig(
            enabled=False,
            base_url="",
            default_model="",
            allowed_task_kinds=["plan_only"],
            blocked_task_kinds=["plan_only"],
        )


def test_hermes_adapter_skeleton_health_and_no_mutation_methods() -> None:
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=False))
    health = adapter.health()
    assert health["status"] == "disabled"
    result = adapter.propose(_env())
    assert result.status.value == "degraded"
    assert not hasattr(adapter, "apply_patch")
    assert not hasattr(adapter, "write_file")


def test_bridge_registry_hermes_entry_feature_flag_behavior() -> None:
    registry_disabled = BridgeAdapterRegistry(feature_flags={"enable_hermes_worker_adapter": False})
    disabled = registry_disabled.hermes_entry(enabled=True, health_status="ready")
    assert disabled["status"] == "degraded"
    assert disabled["reason"] == "disabled_by_feature_flag"
    assert disabled["kind"] == "external_agent_worker"

    registry_enabled = BridgeAdapterRegistry(feature_flags={"enable_hermes_worker_adapter": True})
    ready = registry_enabled.hermes_entry(enabled=True, health_status="ready")
    assert ready["status"] == "ready"
    assert ready["id"] == "hermes"
    assert "shell_execute" in ready["denied_capability_classes"]


def test_capability_registry_hermes_decisions() -> None:
    registry = CapabilityRegistry()
    allow = registry.hermes_capability_decision("planning")
    deny = registry.hermes_capability_decision("shell_execute")
    unknown = registry.hermes_capability_decision("something_new")
    assert allow["allowed"] is True
    assert deny["allowed"] is False
    assert unknown["allowed"] is False
    assert allow["requires_structured_output"] is True


def test_tool_router_can_select_hermes_for_allowed_capabilities() -> None:
    service = ToolRoutingService()
    cfg = {
        "feature_flags": {"enable_hermes_worker_adapter": True},
        "hermes_worker_adapter": {"enabled": True},
    }
    fake_backends = {"capabilities": {"hermes": {"available": True}, "sgpt": {"available": True}}}
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        routed = service.route_execution_backend(
            task_kind="analysis",
            requested_backend="hermes",
            required_capabilities=["review"],
            governance_mode="balanced",
            agent_cfg=cfg,
        )
    assert routed["decision"]["selected_target"] == "hermes"
    assert routed["decision"]["selected_reason"] == "requested_backend_selected"


def test_tool_router_rejects_hermes_for_denied_capabilities_and_falls_back() -> None:
    service = ToolRoutingService()
    cfg = {
        "feature_flags": {"enable_hermes_worker_adapter": True},
        "hermes_worker_adapter": {"enabled": True},
    }
    fake_backends = {"capabilities": {"hermes": {"available": True}, "opencode": {"available": True}}}
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        routed = service.route_execution_backend(
            task_kind="ops",
            requested_backend="hermes",
            required_capabilities=["shell_execution"],
            governance_mode="balanced",
            agent_cfg=cfg,
        )
    assert routed["decision"]["selected_target"] == "opencode"
    hermes_alt = next(item for item in routed["decision"]["alternatives"] if item["target"] == "hermes")
    assert "missing_capabilities" in hermes_alt["reason"]


def test_tool_router_marks_hermes_disabled_by_feature_flag() -> None:
    service = ToolRoutingService()
    cfg = {"feature_flags": {"enable_hermes_worker_adapter": False}, "hermes_worker_adapter": {"enabled": True}}
    fake_backends = {"capabilities": {"hermes": {"available": True}}}
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        routed = service.route_execution_backend(
            task_kind="analysis",
            requested_backend="hermes",
            required_capabilities=["review"],
            governance_mode="balanced",
            agent_cfg=cfg,
        )
    assert routed["decision"]["selected_target"] is None
    assert routed["decision"]["selected_reason"] == "no_backend_available"
    hermes_alt = routed["decision"]["alternatives"][0]
    assert hermes_alt["reason"] == "disabled_by_feature_flag"


def test_hermes_http_client_maps_transport_errors_and_redacts() -> None:
    client = HermesHttpClient(config=HermesClientConfig(base_url="http://localhost:9000", timeout_seconds=1, default_model="m"))
    assert client._map_http_status(401) == "hermes_unauthorized"
    assert client._map_http_status(429) == "hermes_rate_limited"
    assert client._map_http_status(503) == "hermes_server_error"
    err = HermesClientError(code="hermes_unauthorized", detail="x", status_code=401)
    assert "Bearer" not in str(err)


def test_hermes_http_client_chat_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HermesHttpClient(config=HermesClientConfig(base_url="http://localhost:9000", timeout_seconds=1, default_model="m"))

    def _ok(url: str, *, headers: dict[str, str], payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        assert url.endswith("/v1/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        assert payload["model"] == "m"
        return {"id": "ok"}

    monkeypatch.setattr(client, "_post_json", _ok)
    resp = client.chat_completions(api_key="secret", system_message="sys", user_message="usr")
    assert resp["id"] == "ok"


def test_hermes_http_client_timeout_mapping() -> None:
    client = HermesHttpClient(config=HermesClientConfig(base_url="http://localhost:9000", timeout_seconds=1, default_model="m"))
    with patch("worker.core.hermes_http_client.request.urlopen", side_effect=TimeoutError("timeout")):
        with pytest.raises(HermesClientError) as exc:
            client._post_json(
                "http://localhost:9000/v1/chat/completions",
                headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
                payload={"model": "m", "messages": []},
                timeout_seconds=1,
            )
        assert exc.value.code == "hermes_timeout"


def test_hermes_http_client_http_error_mappings() -> None:
    client = HermesHttpClient(config=HermesClientConfig(base_url="http://localhost:9000", timeout_seconds=1, default_model="m"))
    for status, expected in ((401, "hermes_unauthorized"), (429, "hermes_rate_limited"), (503, "hermes_server_error")):
        with patch("worker.core.hermes_http_client.request.urlopen", side_effect=error.HTTPError("u", status, "x", hdrs=None, fp=None)):
            with pytest.raises(HermesClientError) as exc:
                client._post_json(
                    "http://localhost:9000/v1/chat/completions",
                    headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
                    payload={"model": "m", "messages": []},
                    timeout_seconds=1,
                )
            assert exc.value.code == expected


def test_hermes_http_client_connection_error_mapping() -> None:
    client = HermesHttpClient(config=HermesClientConfig(base_url="http://localhost:9000", timeout_seconds=1, default_model="m"))
    with patch("worker.core.hermes_http_client.request.urlopen", side_effect=error.URLError("down")):
        with pytest.raises(HermesClientError) as exc:
            client._post_json(
                "http://localhost:9000/v1/chat/completions",
                headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
                payload={"model": "m", "messages": []},
                timeout_seconds=1,
            )
        assert exc.value.code == "hermes_connection_error"


def test_hermes_health_states_and_redaction() -> None:
    class _Client:
        def __init__(self, code: str | None = None) -> None:
            self.code = code

        def health(self, *, api_key: str = "") -> dict[str, object]:
            if self.code:
                raise HermesClientError(code=self.code, detail="x")
            return {"ok": True}

    disabled = HermesAdapter(config=HermesAdapterConfig(enabled=False), client=_Client())  # type: ignore[arg-type]
    assert disabled.health()["status"] == "disabled"

    misconfigured = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="", default_model="m"), client=_Client())  # type: ignore[arg-type]
    assert misconfigured.health()["status"] == "misconfigured"

    ready = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    payload = ready.health()
    assert payload["status"] == "ready"
    assert payload["api_key_value"] == "[REDACTED]"

    unauthorized = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client("hermes_unauthorized"))  # type: ignore[arg-type]
    assert unauthorized.health()["status"] == "unauthorized"

    unavailable = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client("hermes_connection_error"))  # type: ignore[arg-type]
    assert unavailable.health()["status"] == "unavailable"


def test_model_selection_rules_and_blocking() -> None:
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="default", blocked_models=["blocked"]))
    env_override = _env(model_policy={"preferred_model": "override"})
    sel = adapter._select_model(envelope=env_override, task_kind="plan_only", mode="plan_only")
    assert sel["requested_model"] == "override"
    assert sel["effective_model"] == "override"

    env_fallback = _env()
    sel2 = adapter._select_model(envelope=env_fallback, task_kind="plan_only", mode="plan_only")
    assert sel2["effective_model"] == "default"

    env_blocked = _env(model_policy={"preferred_model": "blocked"})
    sel3 = adapter._select_model(envelope=env_blocked, task_kind="plan_only", mode="plan_only")
    assert sel3["blocked"] is True


def test_system_prompt_contains_mode_and_denied_operations() -> None:
    env = _env(denied_operations=["patch_apply", "shell_execute"])
    prompt = build_governed_system_prompt(
        envelope=env,
        allowed_mode="plan_only",
        denied_operations=["patch_apply", "shell_execute"],
        output_schema={"type": "object"},
    )
    assert "external_ananta_worker" in prompt
    assert "plan_only" in prompt
    assert "patch_apply" in prompt
    assert "shell_execute" in prompt


def test_context_converter_budget_sensitive_and_missing() -> None:
    blocks = [
        ContextBlock("task", "a", "hub", sensitivity=ContextSensitivity.public, content="A" * 50),
        ContextBlock("task", "b", "hub", sensitivity=ContextSensitivity.secret, content="SECRET"),
    ]
    result = convert_context_blocks_to_prompt(blocks, max_context_chars=30, allow_sensitive=False)
    assert result.total_chars <= 30
    assert any(item["reason_code"] in {"sensitive_block_excluded", "truncated_for_budget"} for item in result.skipped + result.truncated)
    empty = convert_context_blocks_to_prompt([], max_context_chars=10, allow_sensitive=False)
    assert empty.has_required_context is False


def test_output_parser_strict_and_unsafe_claims() -> None:
    ok = parse_hermes_json_output('{"status":"ok","artifact_type":"plan","summary":"s","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}')
    assert ok.ok is True
    fenced = parse_hermes_json_output('```json {"status":"ok","artifact_type":"plan","summary":"s","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true} ```')
    assert fenced.ok is True
    bad = parse_hermes_json_output("done")
    assert bad.ok is False
    unsafe = parse_hermes_json_output('{"status":"ok","artifact_type":"plan","summary":"modified files","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}')
    assert unsafe.ok is False


def test_plan_only_success_and_no_mutation_required() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"success","artifact_type":"plan","summary":"Plan","findings":[{"step":"a"}],"risks":["r"],"suggested_tests":["t"],"confidence":0.7,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                        }
                    }
                ]
            }

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
    blocks = [ContextBlock("task", "x", "hub", sensitivity=ContextSensitivity.public, content="Need a plan")]
    result = adapter.plan_only(env, context_blocks=blocks)
    assert result.status.value == "success"
    assert result.no_side_effects_confirmed is True


def test_review_mode_requires_review_capability_and_preserves_incomplete_warning() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"success","artifact_type":"review","summary":"Review","findings":[],"risks":["r"],"suggested_tests":["t"],"confidence":0.4,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                        }
                    }
                ]
            }

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    denied = adapter.review(_env(capability_grant=CapabilityGrant(capabilities=["planning"])))
    assert denied.status.value == "denied"
    env_ok = _env(capability_grant=CapabilityGrant(capabilities=["verify"]))
    blocks = [ContextBlock("task", "x", "hub", sensitivity=ContextSensitivity.public, content="Review this")]
    ok = adapter.review(env_ok, context_blocks=blocks)
    assert ok.status.value == "success"
    assert "incomplete_context_warning" in ok.warnings


def test_retry_policy_transient_and_parse_retry_only_once() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls = 0

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

        def chat_completions(self, **_: object) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                raise HermesClientError(code="hermes_rate_limited", detail="rl")
            if self.calls == 2:
                return {"choices": [{"message": {"content": "not json"}}]}
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"success","artifact_type":"plan","summary":"ok","findings":[1],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                        }
                    }
                ]
            }

    client = _Client()
    adapter = HermesAdapter(
        config=HermesAdapterConfig(
            enabled=True,
            feature_flag_enabled=True,
            base_url="http://localhost:1",
            default_model="m",
            max_retries=1,
            strict_json_required=True,
            parse_retry_enabled=True,
        ),
        client=client,  # type: ignore[arg-type]
    )
    env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
    blocks = [ContextBlock("task", "x", "hub", sensitivity=ContextSensitivity.public, content="Plan it")]
    result = adapter.plan_only(env, context_blocks=blocks)
    assert result.status.value == "success"
    assert client.calls == 3


def test_summarize_mode_requires_capability_and_redacts_sensitive() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"success","artifact_type":"summary","summary":"S","findings":[],"risks":[],"suggested_tests":[],"confidence":0.6,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                        }
                    }
                ]
            }

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    denied = adapter.summarize(_env(capability_grant=CapabilityGrant(capabilities=["planning"])))
    assert denied.status.value == "denied"
    env_ok = _env(capability_grant=CapabilityGrant(capabilities=["planning", "summarize"]))
    blocks = [ContextBlock("task", "s1", "hub", sensitivity=ContextSensitivity.secret, content="OPENAI_API_KEY=sk-proj-abcdef012345678901234567")]
    ok = adapter.summarize(env_ok, context_blocks=blocks)
    assert ok.status.value in {"degraded", "success"}
    if ok.status.value == "success":
        assert ok.artifacts[0].kind == "summary_artifact"


def test_patch_propose_mode_and_no_workspace_mutation_claim() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"success","artifact_type":"patch","summary":"Patch","findings":[],"risks":["r"],"suggested_tests":["t"],"confidence":0.7,"requires_approval_for_apply":true,"no_side_effects_claimed":true,"patch_unified_diff":"--- a/x.py\\n+++ b/x.py\\n@@ -1 +1 @@\\n-a\\n+b","touched_files":["x.py"]}'
                        }
                    }
                ]
            }

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    env = _env(capability_grant=CapabilityGrant(capabilities=["patch_propose"]))
    result = adapter.patch_propose(env, context_blocks=[ContextBlock("task", "p", "hub", content="Propose patch")])
    assert result.status.value == "success"
    assert result.artifacts[0].kind == "patch_artifact"
    assert result.artifacts[0].metadata["requires_approval_for_apply"] is True


def test_patch_propose_rejects_applied_claim() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"status":"success","artifact_type":"patch","summary":"applied patch","findings":[],"risks":[],"suggested_tests":[],"confidence":0.6,"requires_approval_for_apply":true,"no_side_effects_claimed":true,"touched_files":["x.py"],"patch_description":"modified files already"}'}}]}

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    env = _env(capability_grant=CapabilityGrant(capabilities=["patch_propose"]))
    result = adapter.patch_propose(env, context_blocks=[ContextBlock("task", "p", "hub", content="patch")])
    assert result.status.value == "failed"


def test_research_limited_capability_and_network_policy() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"status":"success","artifact_type":"research","summary":"R","findings":[],"risks":[],"suggested_tests":[],"confidence":0.6,"requires_approval_for_apply":false,"no_side_effects_claimed":true,"claims":[{"c":"x"}]}'}}]}

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    denied_cap = adapter.research_limited(_env(capability_grant=CapabilityGrant(capabilities=["planning"])), context_blocks=[ContextBlock("task", "r", "hub", content="ctx")])
    assert denied_cap.status.value == "denied"
    denied_net = adapter.research_limited(
        _env(capability_grant=CapabilityGrant(capabilities=["research_limited"]), network_scope={"allow_all": True}),
        context_blocks=[ContextBlock("task", "r", "hub", content="ctx")],
    )
    assert denied_net.status.value == "denied"
    ok = adapter.research_limited(
        _env(capability_grant=CapabilityGrant(capabilities=["research_limited"]), network_scope={"allow_all": False}),
        context_blocks=[ContextBlock("task", "r", "hub", content="provided research context")],
    )
    assert ok.status.value == "success"
    assert ok.artifacts[0].kind == "research_artifact"


def test_artifact_mapping_contains_hash_metadata() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"status":"success","artifact_type":"plan","summary":"Plan","findings":[],"risks":[],"suggested_tests":[],"confidence":0.8,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'}}]}

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capability_grant=CapabilityGrant(capabilities=["planning"])), context_blocks=[ContextBlock("task", "h", "hub", content="plan")])
    art = res.artifacts[0]
    assert art.metadata["source"] == "hermes"
    assert art.metadata["content_hash"]
    assert art.metadata["context_hash"]


def test_trace_contains_hashes_and_retry_fields() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"status":"success","artifact_type":"plan","summary":"Plan","findings":[],"risks":[],"suggested_tests":[],"confidence":0.8,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'}}]}

        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}

    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capability_grant=CapabilityGrant(capabilities=["planning"])), context_blocks=[ContextBlock("task", "h", "hub", content="plan")])
    events = res.trace_bundle.events
    merged = " ".join(str(e.payload) for e in events)
    assert "prompt_hash" in merged
    assert "context_hash" in merged


def test_worker_result_error_shapes_for_parse_timeout_remote_degraded() -> None:
    class _Timeout:
        def chat_completions(self, **_: object) -> dict[str, object]:
            raise HermesClientError(code="hermes_timeout", detail="t")
        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}
    class _Remote:
        def chat_completions(self, **_: object) -> dict[str, object]:
            raise HermesClientError(code="hermes_server_error", detail="x")
        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}
    class _Parse:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices":[{"message":{"content":"bad"}}]}
        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}
    env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
    blocks=[ContextBlock("task","e","hub",content="x")]
    t = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m", max_retries=0), client=_Timeout())  # type: ignore[arg-type]
    assert t.plan_only(env, context_blocks=blocks).status.value == "failed"
    r = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m", max_retries=0), client=_Remote())  # type: ignore[arg-type]
    assert r.plan_only(env, context_blocks=blocks).status.value == "failed"
    p = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m", strict_json_required=False), client=_Parse())  # type: ignore[arg-type]
    assert p.plan_only(env, context_blocks=blocks).status.value == "failed"
    d = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Parse())  # type: ignore[arg-type]
    assert d.plan_only(env, context_blocks=[]).status.value == "degraded"


def test_audit_events_emitted_for_success_and_denial() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices":[{"message":{"content":'{"status":"success","artifact_type":"plan","summary":"ok","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'}}]}
        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}
    emitter = AuditEmitter()
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client(), audit_emitter=emitter)  # type: ignore[arg-type]
    adapter.plan_only(_env(capability_grant=CapabilityGrant(capabilities=["planning"])), context_blocks=[ContextBlock("task","a","hub",content="x")])
    events = emitter.flush()
    assert any(e["event_type"] == "routing_selected" for e in events)
    assert any(e["event_type"] == "provider_call" for e in events)
    assert all("api_key" not in str(e).lower() for e in events)


def test_cloud_allowed_enforcement_and_classification() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices":[{"message":{"content":'{"status":"success","artifact_type":"plan","summary":"ok","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'}}]}
        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}
    cloud_adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="https://api.hermes.example", default_model="m"), client=_Client())  # type: ignore[arg-type]
    denied = cloud_adapter.plan_only(
        _env(capability_grant=CapabilityGrant(capabilities=["planning"]), model_policy={"cloud_allowed": False}),
        context_blocks=[ContextBlock("task","c","hub",content="x")],
    )
    assert denied.status.value == "denied"


def test_phase1_tool_autonomy_claim_blocked() -> None:
    class _Client:
        def chat_completions(self, **_: object) -> dict[str, object]:
            return {"choices":[{"message":{"content":'{"status":"success","artifact_type":"plan","summary":"executed commands and modified files","findings":[],"risks":[],"suggested_tests":[],"confidence":0.5,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'}}]}
        def health(self, **_: object) -> dict[str, object]:
            return {"ok": True}
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"), client=_Client())  # type: ignore[arg-type]
    res = adapter.plan_only(_env(capability_grant=CapabilityGrant(capabilities=["planning"])), context_blocks=[ContextBlock("task","u","hub",content="x")])
    assert res.status.value == "failed"
