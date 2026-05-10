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
from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_http_client import HermesClientConfig, HermesClientError, HermesHttpClient


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
        HermesAdapterConfig(enabled=True, base_url="localhost:3000", default_model="m")
    with pytest.raises(ValueError):
        HermesAdapterConfig(enabled=True, base_url="http://localhost:3000", default_model="", timeout_seconds=10)
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
