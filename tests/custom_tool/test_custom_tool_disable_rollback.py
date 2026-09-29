"""HDE-019: disable, re-activation and rollback of custom tools."""
import pytest

from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService


def _spec(version_marker="v1"):
    return {
        "name": "custom.report",
        "description": f"Report tool {version_marker}",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {}},
        "execution_kind": "command_template",
        "command_template": ["echo", version_marker],
    }


def _store(registry, digest, *, validated=True, approval="granted", marker="v1"):
    return registry.store_promoted_tool(
        name="custom.report",
        spec=_spec(marker),
        proposal_digest=digest,
        validated_digest=digest if validated else "anders",
        validation_report_ref=None,
        approval_status=approval,
    )


def test_disable_keeps_record_and_history(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, "d1")
    registry.record_usage("custom.report", success=True)
    record = registry.set_status("custom.report", "disabled")
    assert record["status"] == "disabled"
    assert registry.get_record("custom.report") is not None
    assert registry.get_record("custom.report")["usage"]["success_count"] == 1


def test_disabled_tool_is_invisible_everywhere(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, "d1")
    registry.set_status("custom.report", "disabled")
    assert registry.get_active_tool("custom.report") is None
    assert registry.list_active_tools() == []
    assert registry.match_intent_alias("report") is None

    from agent.services.custom_tool_executor import execute_custom_tool
    import agent.services.dynamic_tool_registry_service as registry_module

    original = registry_module.dynamic_tool_registry_service
    registry_module.dynamic_tool_registry_service = registry
    try:
        result = execute_custom_tool(
            tool_name="custom.report", arguments={}, workspace_dir=str(tmp_path), tool_call_id="t-1"
        )
    finally:
        registry_module.dynamic_tool_registry_service = original
    assert result["status"] == "rejected"
    assert result["error"] == "custom_tool_not_active"


def test_reactivation_requires_grant_and_matching_digest(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, "d1")
    registry.set_status("custom.report", "disabled")
    record = registry.set_status("custom.report", "active")
    assert record["status"] == "active"

    # Break the digest linkage -> re-activation refused.
    registry.set_status("custom.report", "disabled")
    raw = registry.get_record("custom.report")
    raw["validated_digest"] = "kaputt"
    (tmp_path / "tools" / "custom.report.json").write_text(__import__("json").dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="activation_requires_matching_validated_digest"):
        registry.set_status("custom.report", "active")


def test_rollback_to_validated_approved_version(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, "d1", marker="v1")
    registry.record_usage("custom.report", success=True)
    _store(registry, "d2", marker="v2")

    record = registry.rollback("custom.report", 1)
    assert record["version"] == 3
    assert record["proposal_digest"] == "d1"
    assert record["spec"]["command_template"] == ["echo", "v1"]
    # Usage history survives the rollback.
    assert record["usage"]["success_count"] == 1


def test_rollback_to_unvalidated_version_is_refused(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, "d1", validated=False, marker="v1")
    _store(registry, "d2", marker="v2")
    with pytest.raises(ValueError, match="rollback_target_not_validated"):
        registry.rollback("custom.report", 1)


def test_rollback_to_unknown_version_is_refused(tmp_path):
    registry = DynamicToolRegistryService(tmp_path)
    _store(registry, "d1")
    with pytest.raises(ValueError, match="unknown_dynamic_tool_version"):
        registry.rollback("custom.report", 99)
