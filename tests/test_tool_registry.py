"""Tests for worker/core/tool_registry.py (EW-T013, EW-T014, EW-T018)."""
import time

import pytest
from pydantic import ValidationError

from worker.core.tool_registry import (
    ResourceLimits,
    ToolInvocationEnvelope,
    ToolResult,
    WorkerToolEntry,
    WorkerToolRegistry,
    build_default_registry,
)


# ── WorkerToolEntry ───────────────────────────────────────────────────────────

class TestWorkerToolEntry:
    def test_catalog_entry_excludes_internal_fields(self):
        entry = WorkerToolEntry(
            id="read_file",
            kind="file_read",
            capability_classes=("code_read",),
            risk_class="low",
            input_schema={"required": ["path"]},
        )
        catalog = entry.as_catalog_entry()
        assert "id" in catalog
        assert "input_schema" not in catalog  # not in safe catalog
        assert "kind" in catalog
        assert "risk_class" in catalog

    def test_capability_classes_in_catalog(self):
        entry = WorkerToolEntry(
            id="run_shell",
            kind="shell",
            capability_classes=("shell_execute",),
            risk_class="high",
        )
        catalog = entry.as_catalog_entry()
        assert "shell_execute" in catalog["capability_classes"]


# ── WorkerToolRegistry ────────────────────────────────────────────────────────

class TestWorkerToolRegistry:
    def test_register_and_get(self):
        registry = WorkerToolRegistry()
        entry = WorkerToolEntry(
            id="my_tool", kind="test", capability_classes=("test_run",), risk_class="medium",
        )
        registry.register(entry)
        assert registry.get("my_tool") is entry

    def test_is_registered(self):
        registry = WorkerToolRegistry()
        entry = WorkerToolEntry(
            id="my_tool", kind="test", capability_classes=("test_run",), risk_class="medium",
        )
        registry.register(entry)
        assert registry.is_registered("my_tool") is True
        assert registry.is_registered("other_tool") is False

    def test_invalid_risk_class_rejected(self):
        registry = WorkerToolRegistry()
        entry = WorkerToolEntry(
            id="bad_tool", kind="test", capability_classes=("test_run",), risk_class="extreme",
        )
        with pytest.raises(ValueError, match="invalid risk_class"):
            registry.register(entry)

    def test_tools_for_capability(self):
        registry = WorkerToolRegistry()
        registry.register(WorkerToolEntry(
            id="t1", kind="file_read", capability_classes=("code_read",), risk_class="low",
        ))
        registry.register(WorkerToolEntry(
            id="t2", kind="patch", capability_classes=("code_read", "patch_propose"), risk_class="medium",
        ))
        registry.register(WorkerToolEntry(
            id="t3", kind="shell", capability_classes=("shell_execute",), risk_class="high",
        ))
        code_read_tools = registry.tools_for_capability("code_read")
        ids = {t.id for t in code_read_tools}
        assert "t1" in ids
        assert "t2" in ids
        assert "t3" not in ids

    def test_capability_catalog_is_sorted(self):
        registry = WorkerToolRegistry()
        for tool_id in ["z_tool", "a_tool", "m_tool"]:
            registry.register(WorkerToolEntry(
                id=tool_id, kind="test", capability_classes=(), risk_class="low",
            ))
        catalog = registry.capability_catalog()
        ids = [e["id"] for e in catalog]
        assert ids == sorted(ids)

    def test_unregistered_tool_fails_validation(self):
        registry = WorkerToolRegistry()
        env = ToolInvocationEnvelope(
            execution_id="exec-1", tool_id="ghost_tool", arguments={}
        )
        errors = registry.validate_invocation(env)
        assert any("not registered" in e for e in errors)

    def test_registered_tool_with_valid_args_passes(self):
        registry = WorkerToolRegistry()
        registry.register(WorkerToolEntry(
            id="read_file",
            kind="file_read",
            capability_classes=("code_read",),
            risk_class="low",
            input_schema={"required": ["path"], "properties": {"path": {"type": "string"}}},
        ))
        env = ToolInvocationEnvelope(
            execution_id="exec-1", tool_id="read_file", arguments={"path": "/tmp/test.py"}
        )
        errors = registry.validate_invocation(env)
        assert errors == []

    def test_registered_tool_missing_required_arg_fails(self):
        registry = WorkerToolRegistry()
        registry.register(WorkerToolEntry(
            id="read_file",
            kind="file_read",
            capability_classes=("code_read",),
            risk_class="low",
            input_schema={"required": ["path"]},
        ))
        env = ToolInvocationEnvelope(
            execution_id="exec-1", tool_id="read_file", arguments={}
        )
        errors = registry.validate_invocation(env)
        assert any("path" in e for e in errors)

    def test_build_default_registry_has_standard_tools(self):
        registry = build_default_registry()
        for tool_id in ["read_file", "propose_patch", "apply_patch", "run_shell", "run_tests"]:
            assert registry.is_registered(tool_id), f"{tool_id} missing from default registry"

    def test_build_default_registry_has_codecompass_context_tools(self):
        registry = build_default_registry()
        for tool_id in [
            "codecompass.resolve_context",
            "codecompass.search_symbols",
            "codecompass.expand_graph",
            "codecompass.get_file_context",
            "codecompass.get_domain_map",
        ]:
            entry = registry.get(tool_id)
            assert entry is not None, f"{tool_id} missing from default registry"
            assert entry.risk_class == "low"
            assert "filesystem_write" not in entry.side_effects
            assert "host_mutation" not in entry.side_effects

    def test_codecompass_resolve_context_requires_query(self):
        registry = build_default_registry()
        env = ToolInvocationEnvelope(
            execution_id="exec-1",
            tool_id="codecompass.resolve_context",
            arguments={},
        )
        errors = registry.validate_invocation(env)
        assert any("query" in error for error in errors)


# ── ToolInvocationEnvelope ────────────────────────────────────────────────────

class TestToolInvocationEnvelope:
    def test_valid_envelope(self):
        env = ToolInvocationEnvelope(execution_id="exec-1", tool_id="read_file")
        assert env.tool_id == "read_file"

    def test_empty_execution_id_rejected(self):
        with pytest.raises(ValidationError, match="must be non-empty"):
            ToolInvocationEnvelope(execution_id="", tool_id="read_file")

    def test_empty_tool_id_rejected(self):
        with pytest.raises(ValidationError, match="must be non-empty"):
            ToolInvocationEnvelope(execution_id="exec-1", tool_id="")

    def test_argument_type_validation(self):
        env = ToolInvocationEnvelope(
            execution_id="e1", tool_id="t1",
            arguments={"count": 5, "name": "test"},
        )
        schema = {
            "properties": {
                "count": {"type": "integer"},
                "name": {"type": "string"},
            }
        }
        errors = env.validate_arguments(schema)
        assert errors == []

    def test_argument_wrong_type_detected(self):
        env = ToolInvocationEnvelope(
            execution_id="e1", tool_id="t1",
            arguments={"path": 42},
        )
        schema = {"properties": {"path": {"type": "string"}}}
        errors = env.validate_arguments(schema)
        assert any("path" in e for e in errors)

    def test_output_limit_no_truncation(self):
        env = ToolInvocationEnvelope(
            execution_id="e1", tool_id="t1",
            resource_limits=ResourceLimits(max_output_chars=100),
        )
        output, truncated = env.apply_output_limit("hello world")
        assert output == "hello world"
        assert truncated is False

    def test_output_limit_truncation(self):
        env = ToolInvocationEnvelope(
            execution_id="e1", tool_id="t1",
            resource_limits=ResourceLimits(max_output_chars=10),
        )
        output, truncated = env.apply_output_limit("hello world, this is too long")
        assert len(output) == 10
        assert truncated is True


# ── ResourceLimits (EW-T018) ──────────────────────────────────────────────────

class TestResourceLimits:
    def test_defaults_are_sensible(self):
        limits = ResourceLimits()
        assert limits.timeout_seconds > 0
        assert limits.max_output_chars > 0
        assert limits.max_artifact_bytes > 0
        assert limits.max_files_touched > 0

    def test_zero_timeout_rejected(self):
        with pytest.raises(ValidationError, match="timeout_seconds"):
            ResourceLimits(timeout_seconds=0)

    def test_negative_max_chars_rejected(self):
        with pytest.raises(ValidationError):
            ResourceLimits(max_output_chars=-1)

    def test_custom_limits_accepted(self):
        limits = ResourceLimits(timeout_seconds=5.0, max_output_chars=500)
        assert limits.timeout_seconds == 5.0
        assert limits.max_output_chars == 500


# ── ToolResult ────────────────────────────────────────────────────────────────

class TestToolResult:
    def test_denied_factory(self):
        result = ToolResult.denied("run_shell", "exec-1", "tool_unavailable")
        assert result.success is False
        assert result.reason_code == "tool_unavailable"

    def test_timeout_factory(self):
        result = ToolResult.timeout("run_shell", "exec-1", partial_stdout="partial output")
        assert result.success is False
        assert result.reason_code == "tool_timeout"
        assert result.truncated is True
        assert result.stdout == "partial output"

    def test_timeout_factory_no_output(self):
        result = ToolResult.timeout("run_shell", "exec-1")
        assert result.truncated is False
