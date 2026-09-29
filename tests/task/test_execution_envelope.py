"""Tests for worker/core/execution_envelope.py (EW-T007)."""
import hashlib
import json
import time

import pytest
from pydantic import ValidationError

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    FilesystemScope,
    LegacyEnvelopeAdapter,
    ModelPolicy,
    NetworkScope,
    ToolPolicy,
    TraceBundle,
    TraceEvent,
    WorkerResult,
    WorkerResultStatus,
    _capability_hash,
    make_trace,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _minimal_envelope(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id="task-001",
        actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=["planning"]),
        context_envelope_ref="ctx:001",
        audit_correlation_id="audit:001",
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


def _minimal_trace() -> TraceBundle:
    return TraceBundle(
        correlation_id="audit:001",
        capability_snapshot_hash="abc123",
    )


# ── CapabilityGrant ────────────────────────────────────────────────────────────

class TestCapabilityGrant:
    def test_known_capabilities_accepted_with_order_independent_snapshot_hash(self):
        g = CapabilityGrant(capabilities=["planning", "code_read"])
        assert "planning" in g.capabilities
        # snapshot hash is auto-computed from the capabilities
        expected = _capability_hash(["planning", "code_read"])
        assert g.snapshot_hash == expected
        # snapshot hash does not depend on capability order
        g2 = CapabilityGrant(capabilities=["code_read", "planning"])
        assert g.snapshot_hash == g2.snapshot_hash

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValidationError, match="unknown capability classes"):
            CapabilityGrant(capabilities=["planning", "hack_the_planet"])

    def test_empty_capabilities_accepted(self):
        g = CapabilityGrant(capabilities=[])
        assert g.capabilities == []

    def test_has_reports_granted_and_missing_capabilities(self):
        g = CapabilityGrant(capabilities=["planning"])
        assert g.has("planning") is True
        assert g.has("shell_execute") is False


# ── ModelPolicy ───────────────────────────────────────────────────────────────

class TestModelPolicy:
    def test_cloud_blocked_when_cloud_allowed_false(self):
        p = ModelPolicy(cloud_allowed=False)
        for provider in ["openai", "anthropic", "gemini", "groq", "openrouter", "bedrock", "azure"]:
            assert p.is_provider_allowed(provider) is False, f"{provider} should be blocked"

    def test_cloud_allowed_when_flag_true(self):
        p = ModelPolicy(cloud_allowed=True)
        assert p.is_provider_allowed("openai") is False

    def test_cloud_allowed_with_legacy_default_allow(self):
        p = ModelPolicy(cloud_allowed=True, legacy_default_allow=True)
        assert p.is_provider_allowed("openai") is True

    def test_local_provider_allowed_by_default(self):
        p = ModelPolicy(cloud_allowed=False)
        assert p.is_provider_allowed("ollama") is True

    def test_allowlist_filters_local_providers(self):
        p = ModelPolicy(allowed_providers=["ollama"], cloud_allowed=False)
        assert p.is_provider_allowed("ollama") is True
        assert p.is_provider_allowed("lmstudio") is False

    def test_empty_allowlist_allows_local_providers_by_default(self):
        p = ModelPolicy(allowed_providers=[], cloud_allowed=False)
        assert p.is_provider_allowed("lmstudio") is True

    def test_legacy_default_allow_allows_non_cloud(self):
        p = ModelPolicy(allowed_providers=[], cloud_allowed=False, legacy_default_allow=True)
        assert p.is_provider_allowed("lmstudio") is True

    def test_provider_check_case_insensitive(self):
        p = ModelPolicy(allowed_providers=["Ollama"], cloud_allowed=False)
        assert p.is_provider_allowed("ollama") is True


# ── ToolPolicy ────────────────────────────────────────────────────────────────

class TestToolPolicy:
    def test_empty_allowlist_denies_by_default(self):
        p = ToolPolicy()
        assert p.is_tool_allowed("any_tool") is False

    def test_legacy_default_allow_enables_empty_tool_allowlist(self):
        p = ToolPolicy(legacy_default_allow=True)
        assert p.is_tool_allowed("any_tool") is True

    def test_allowlist_filters_unknown_tool(self):
        p = ToolPolicy(allowed_tool_ids=["read_file"])
        assert p.is_tool_allowed("read_file") is True
        assert p.is_tool_allowed("shell_exec") is False

    def test_deny_override_blocks_listed_tool(self):
        p = ToolPolicy(
            allowed_tool_ids=["read_file", "shell_exec"],
            approval_overrides={"shell_exec": "deny"},
        )
        assert p.is_tool_allowed("shell_exec") is False

    def test_confirm_required_override(self):
        p = ToolPolicy(approval_overrides={"patch_apply": "confirm_required"})
        assert p.requires_approval("patch_apply") is True
        assert p.requires_approval("read_file") is False


# ── ApprovalRef ───────────────────────────────────────────────────────────────

class TestApprovalRef:
    def test_valid_approval_ref(self):
        ref = ApprovalRef(
            ref_id="ref-001",
            operation="patch_apply",
            granted_at=time.time(),
            granted_by="user:admin",
        )
        assert ref.ref_id == "ref-001"

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param(dict(ref_id="", operation="patch_apply", granted_by="admin"), id="empty_ref_id"),
            pytest.param(dict(ref_id="ref-1", operation="", granted_by="admin"), id="empty_operation"),
            pytest.param(dict(ref_id="ref-1", operation="op", granted_by=""), id="empty_granted_by"),
            pytest.param(dict(ref_id="   ", operation="op", granted_by="admin"), id="whitespace_only_stripped"),
        ],
    )
    def test_blank_field_rejected(self, kwargs):
        with pytest.raises(ValidationError, match="must be non-empty"):
            ApprovalRef(granted_at=0.0, **kwargs)


# ── ExecutionEnvelope ─────────────────────────────────────────────────────────

class TestExecutionEnvelope:
    def test_minimal_valid_envelope(self):
        env = _minimal_envelope()
        assert env.task_id == "task-001"

    @pytest.mark.parametrize(
        "override",
        [
            pytest.param({"task_id": ""}, id="empty_task_id"),
            pytest.param({"task_id": "   "}, id="whitespace_task_id"),
            pytest.param({"actor_ref": ""}, id="empty_actor_ref"),
            pytest.param({"context_envelope_ref": ""}, id="empty_context_envelope_ref"),
            pytest.param({"audit_correlation_id": ""}, id="empty_audit_correlation_id"),
        ],
    )
    def test_blank_required_field_rejected(self, override):
        with pytest.raises(ValidationError, match="must be non-empty"):
            _minimal_envelope(**override)

    def test_has_capability_true(self):
        env = _minimal_envelope(capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]))
        assert env.has_capability("planning") is True
        assert env.has_capability("code_read") is True

    def test_has_capability_false(self):
        env = _minimal_envelope()
        assert env.has_capability("shell_execute") is False

    def test_approval_for_found(self):
        ref = ApprovalRef(ref_id="r1", operation="patch_apply", granted_at=1.0, granted_by="user")
        env = _minimal_envelope(approval_refs=[ref])
        found = env.approval_for("patch_apply")
        assert found is not None
        assert found.ref_id == "r1"

    def test_approval_for_not_found(self):
        env = _minimal_envelope()
        assert env.approval_for("patch_apply") is None

    def test_denied_operation_blocks(self):
        env = _minimal_envelope(denied_operations=["shell_execute"])
        assert env.is_operation_denied("shell_execute") is True
        assert env.is_operation_allowed("shell_execute") is False

    def test_allowed_operations_whitelist(self):
        env = _minimal_envelope(allowed_operations=["read_file"])
        assert env.is_operation_allowed("read_file") is True
        assert env.is_operation_allowed("write_file") is False

    def test_empty_allowed_operations_allows_all(self):
        env = _minimal_envelope(allowed_operations=[])
        assert env.is_operation_allowed("anything") is True

    def test_denied_overrides_allowed(self):
        env = _minimal_envelope(
            allowed_operations=["shell_execute"],
            denied_operations=["shell_execute"],
        )
        assert env.is_operation_allowed("shell_execute") is False

    def test_goal_id_optional(self):
        env = _minimal_envelope(goal_id=None)
        assert env.goal_id is None
        env2 = _minimal_envelope(goal_id="goal-xyz")
        assert env2.goal_id == "goal-xyz"

    def test_serialization_is_stable(self):
        env = _minimal_envelope()
        d = env.model_dump()
        assert d["task_id"] == "task-001"
        assert "capability_grant" in d
        assert "snapshot_hash" in d["capability_grant"]


# ── TraceBundle ───────────────────────────────────────────────────────────────

class TestTraceBundle:
    def test_append_records_typed_timestamped_events_with_payload(self):
        before = time.time()
        trace = _minimal_trace()
        trace.append("preflight_allow", reason_code=None, capability="planning")
        assert len(trace.events) == 1
        assert trace.events[0].event_type == "preflight_allow"
        trace.append("tool_call", tool_id="read_file", path="/tmp/x")
        after = time.time()
        assert trace.events[1].payload["tool_id"] == "read_file"
        # event timestamp is set automatically
        assert before <= trace.events[0].ts <= after


# ── WorkerResult ──────────────────────────────────────────────────────────────

class TestWorkerResult:
    def test_denied_factory(self):
        trace = _minimal_trace()
        result = WorkerResult.denied("task-1", "missing_capability", trace)
        assert result.status == WorkerResultStatus.denied
        assert result.no_side_effects_confirmed is True
        assert "missing_capability" in result.policy_observations
        assert len(trace.events) == 1
        assert trace.events[0].event_type == "preflight_denied"
        assert result.trace_bundle is not None

    def test_needs_approval_factory(self):
        trace = _minimal_trace()
        result = WorkerResult.needs_approval("task-2", "patch_apply", trace)
        assert result.status == WorkerResultStatus.needs_approval
        assert result.no_side_effects_confirmed is True
        assert "approval_missing" in result.policy_observations

    def test_invalid_factory(self):
        result = WorkerResult.invalid("task-3", "empty task_id")
        assert result.status == WorkerResultStatus.invalid_request
        assert result.no_side_effects_confirmed is True
        assert "invalid_request" in result.policy_observations
        assert result.trace_bundle is not None

    def test_invalid_factory_with_empty_task_id(self):
        result = WorkerResult.invalid("", "bad envelope")
        assert result.task_id == "unknown"


# ── LegacyEnvelopeAdapter ─────────────────────────────────────────────────────

class TestLegacyEnvelopeAdapter:
    @pytest.mark.parametrize(
        "mode,granted,not_granted",
        [
            pytest.param("plan_only", ["planning"], ["code_read"], id="plan_only_maps_to_planning"),
            pytest.param("patch_propose", ["code_read", "patch_propose"], ["patch_apply"], id="patch_propose"),
            pytest.param("patch_apply", ["patch_apply"], [], id="patch_apply"),
            pytest.param("command_execute", ["shell_plan", "shell_execute"], [], id="command_execute"),
        ],
    )
    def test_mode_maps_to_capabilities(self, mode, granted, not_granted):
        adapter = LegacyEnvelopeAdapter()
        env = adapter.wrap(task_id="t1", mode=mode)
        for capability in granted:
            assert env.has_capability(capability)
        for capability in not_granted:
            assert not env.has_capability(capability)

    def test_unknown_mode_falls_back_to_planning(self):
        adapter = LegacyEnvelopeAdapter()
        env = adapter.wrap(task_id="t1", mode="nonexistent_mode")
        assert env.has_capability("planning")

    def test_wrap_denies_cloud_and_generates_audit_correlation_id(self):
        adapter = LegacyEnvelopeAdapter()
        env = adapter.wrap(task_id="t1", mode="plan_only")
        assert env.model_policy.cloud_allowed is False
        assert "t1" in env.audit_correlation_id

    def test_all_known_modes_produce_valid_envelope(self):
        adapter = LegacyEnvelopeAdapter()
        for mode in ["plan_only", "patch_propose", "patch_apply", "command_plan",
                     "command_execute", "test_run", "verify"]:
            env = adapter.wrap(task_id=f"t-{mode}", mode=mode)
            assert isinstance(env, ExecutionEnvelope)


# ── make_trace helper ─────────────────────────────────────────────────────────

class TestMakeTrace:
    def test_trace_inherits_correlation_id_and_snapshot_hash_from_envelope(self):
        env = _minimal_envelope()
        trace = make_trace(env)
        assert trace.correlation_id == env.audit_correlation_id
        assert trace.capability_snapshot_hash == env.capability_grant.snapshot_hash


# ── _capability_hash helper ───────────────────────────────────────────────────

class TestCapabilityHash:
    def test_deterministic(self):
        assert _capability_hash(["a", "b"]) == _capability_hash(["a", "b"])

    def test_order_independent(self):
        assert _capability_hash(["b", "a"]) == _capability_hash(["a", "b"])

    def test_deduplicates(self):
        assert _capability_hash(["a", "a"]) == _capability_hash(["a"])

    def test_returns_hex_string(self):
        h = _capability_hash(["planning"])
        assert len(h) == 64
        int(h, 16)  # must be valid hex
