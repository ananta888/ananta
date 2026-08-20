from __future__ import annotations

import pytest

from agent.routes.config.read_models import (
    governance_policy_read_model,
    operation_policy_inventory_read_model,
)
from agent.routes.config.settings import get_config, rollback_operation_policy, set_config
from agent.services.operation_policy_observability_service import OperationPolicyObservabilityService
from agent.services.operation_policy_revision_service import (
    OperationPolicyRevisionError,
    OperationPolicyRevisionService,
)
from agent.services.operation_policy_service import (
    OperationAuthContext,
    OperationPolicyConfigError,
    OperationPolicyService,
)
from agent.services.operation_registry_service import (
    OperationDescriptor,
    OperationRegistryError,
    OperationRegistryService,
    get_operation_registry_service,
)


def _explicit_policy(**overrides):
    policy = {
        "schema_version": "1.0",
        "enabled": True,
        "revision": 0,
        "enforced_transports": ["mcp.tool"],
        "allow_operations": ["mcp.tool.health.get"],
        "deny_operations": [],
        "allow_groups": [],
        "deny_groups": [],
        "allowed_auth_sources": ["user_jwt"],
        "require_admin_for_access_classes": ["admin", "write"],
        "require_approval_for_risks": ["critical", "high"],
        "emit_audit_events": True,
    }
    policy.update(overrides)
    return policy


def test_operation_registry_is_sorted_immutable_and_rejects_ambiguity() -> None:
    registry = OperationRegistryService()
    second = OperationDescriptor(
        "api.zeta.get", "api", "/zeta", "read", "low", "enabled", "Zeta read.", "test", http_method="GET"
    )
    first = OperationDescriptor(
        "api.alpha.get", "api", "/alpha", "read", "low", "enabled", "Alpha read.", "test", http_method="GET"
    )
    registry.register_many((second, first))
    assert [item.operation_id for item in registry.list_descriptors()] == ["api.alpha.get", "api.zeta.get"]
    assert registry.get(" api.alpha.get") is None
    with pytest.raises(OperationRegistryError, match="duplicate_operation_id"):
        registry.register(first)
    with pytest.raises(OperationRegistryError, match="unsafe_default_enabled"):
        OperationDescriptor(
            "api.alpha.post", "api", "/alpha", "write", "high", "enabled", "Alpha write.", "test",
            http_method="POST", side_effecting=True, default_enabled=True,
        )


def test_legacy_mcp_migration_is_versioned_read_only_and_future_safe() -> None:
    registry = get_operation_registry_service()
    service = OperationPolicyService(registry)
    policy = service.resolve_policy({"exposure_policy": {"mcp": {"enabled": True}}})
    auth = OperationAuthContext("user_jwt", is_admin=True, approval_granted=True)
    assert service.decide(registry.get("mcp.tool.health.get"), policy, auth).allowed is True
    write_decision = service.decide(registry.get("mcp.tool.evolution.analyze"), policy, auth)
    assert write_decision.allowed is False
    assert write_decision.reason_code == "operation_not_allowlisted"
    assert policy["allow_groups"] == ["mcp.read.v1"]
    assert "mcp.tool.evolution.analyze" not in registry.group_members("mcp.read.v1")


def test_policy_default_deny_and_deny_group_override_explicit_allow() -> None:
    registry = get_operation_registry_service()
    service = OperationPolicyService(registry)
    policy = service.normalize_policy(
        _explicit_policy(
            allow_operations=["mcp.tool.health.get"],
            deny_groups=["mcp.read.v1"],
        )
    )
    decision = service.decide(
        registry.get("mcp.tool.health.get"),
        policy,
        OperationAuthContext("user_jwt", is_admin=True, approval_granted=True),
    )
    assert decision.allowed is False
    assert decision.reason_code == "operation_group_denied"
    assert decision.matched_rule_id == "deny:group:mcp.read.v1"

    policy = service.normalize_policy(_explicit_policy())
    denied = service.decide(
        registry.get("mcp.tool.tasks.get"),
        policy,
        OperationAuthContext("user_jwt", is_admin=True, approval_granted=True),
    )
    assert denied.reason_code == "operation_not_allowlisted"
    assert denied.matched_rule_id == "default:deny"


def test_policy_auth_admin_approval_and_lifecycle_edges() -> None:
    registry = get_operation_registry_service()
    service = OperationPolicyService(registry)
    policy = service.normalize_policy(_explicit_policy())
    denied_auth = service.decide(
        registry.get("mcp.tool.health.get"), policy, OperationAuthContext("agent_auth", is_admin=True)
    )
    assert denied_auth.reason_code == "operation_auth_source_denied"
    assert service.decide(None, policy, OperationAuthContext("user_jwt")).reason_code == "unknown_operation"

    write_policy = service.normalize_policy(
        _explicit_policy(
            allow_operations=["mcp.tool.evolution.analyze"],
            allowed_auth_sources=["agent_auth", "user_jwt"],
        )
    )
    write_descriptor = registry.get("mcp.tool.evolution.analyze")
    assert service.decide(write_descriptor, write_policy, OperationAuthContext("user_jwt")).reason_code == "operation_admin_required"
    assert service.decide(
        write_descriptor,
        write_policy,
        OperationAuthContext("user_jwt", is_admin=True, approval_granted=False),
    ).reason_code == "operation_approval_required"
    assert service.decide(
        write_descriptor,
        write_policy,
        OperationAuthContext("user_jwt", is_admin=True, approval_granted=True),
    ).allowed is True

    isolated_registry = OperationRegistryService()
    disabled = OperationDescriptor(
        "api.disabled.get", "api", "/disabled", "read", "low", "disabled", "Disabled read.", "test", http_method="GET"
    )
    isolated_registry.register(disabled)
    isolated_service = OperationPolicyService(isolated_registry)
    disabled_policy = isolated_service.normalize_policy(
        {
            **_explicit_policy(),
            "enforced_transports": ["api"],
            "allow_operations": ["api.disabled.get"],
        }
    )
    assert isolated_service.decide(
        disabled, disabled_policy, OperationAuthContext("user_jwt", is_admin=True, approval_granted=True)
    ).reason_code == "operation_lifecycle_disabled"


@pytest.mark.parametrize(
    "invalid_id",
    ["", "*", "mcp.tool.*", "MCP.tool.health.get", "mcp.tool.heälth.get", " mcp.tool.health.get", 7],
)
def test_policy_rejects_invalid_unknown_and_non_ascii_operation_ids(invalid_id) -> None:
    service = OperationPolicyService(get_operation_registry_service())
    with pytest.raises(OperationPolicyConfigError):
        service.normalize_policy(_explicit_policy(allow_operations=[invalid_id]))


def test_policy_rejects_empty_wildcard_and_contradictory_configuration() -> None:
    service = OperationPolicyService(get_operation_registry_service())
    with pytest.raises(OperationPolicyConfigError, match="operation_policy_allowlist_empty"):
        service.normalize_policy(_explicit_policy(allow_operations=[]))
    with pytest.raises(OperationPolicyConfigError, match="operation_policy_group_unknown"):
        service.normalize_policy(_explicit_policy(allow_groups=["mcp.all.v99"], allow_operations=[]))
    with pytest.raises(OperationPolicyConfigError, match="operation_policy_operation_conflict"):
        service.normalize_policy(
            _explicit_policy(
                allow_operations=["mcp.tool.health.get"],
                deny_operations=["mcp.tool.health.get"],
            )
        )


def test_revision_service_detects_races_and_revalidates_rollback() -> None:
    policy_service = OperationPolicyService(get_operation_registry_service())
    revisions = OperationPolicyRevisionService(policy_service)
    first = revisions.prepare_update(
        current_stored=None,
        requested={**_explicit_policy(), "expected_revision": 0},
        actor="admin-1",
    )
    assert first.changed is True
    assert first.revision == 1
    assert first.stored_policy["_history"][0]["revision"] == 0
    assert first.stored_policy["_history"][0]["actor"] == "admin-1"
    with pytest.raises(OperationPolicyRevisionError, match="operation_policy_revision_conflict"):
        revisions.prepare_update(
            current_stored=first.stored_policy,
            requested={**_explicit_policy(allow_operations=["mcp.tool.tasks.get"]), "expected_revision": 0},
            actor="admin-2",
        )
    rolled_back = revisions.prepare_rollback(
        current_stored=first.stored_policy,
        target_revision=0,
        expected_revision=1,
        actor="admin-2",
    )
    assert rolled_back.change_kind == "rollback"
    assert rolled_back.revision == 2
    assert rolled_back.stored_policy["allow_groups"] == ["mcp.read.v1"]


def test_observability_event_contains_only_safe_decision_fields(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        "agent.services.operation_policy_observability_service.log_audit",
        lambda action, details: captured.append((action, details)),
    )
    registry = get_operation_registry_service()
    service = OperationPolicyService(registry)
    decision = service.decide(
        registry.get("mcp.tool.health.get"),
        service.normalize_policy(_explicit_policy()),
        OperationAuthContext("user_jwt", is_admin=True, approval_granted=True),
    )
    observability = OperationPolicyObservabilityService()
    observability.record(decision, trace_id="trace-1", surface="test")
    details = captured[0][1]
    assert details["operation_id"] == "mcp.tool.health.get"
    assert "authorization" not in details
    assert "token" not in details
    assert "request" not in details
    assert observability.snapshot()["count"] == 1


def test_prioritized_rest_routes_declare_stable_operation_ids() -> None:
    assert get_config.operation_id == "api.config.get"
    assert set_config.operation_id == "api.config.update.post"
    assert rollback_operation_policy.operation_id == "api.config.operation_policy.rollback.post"
    assert governance_policy_read_model.operation_id == "api.governance.policy.get"
    assert operation_policy_inventory_read_model.operation_id == "api.governance.operations.get"
