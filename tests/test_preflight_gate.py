"""Tests for worker/core/preflight.py (EW-T008, EW-T009, EW-T010).

Covers:
  EW-T008 — PreflightGate.check() with positive + negative paths
  EW-T009 — Fail-closed default-deny: unknown task kinds, tools, providers
  EW-T010 — Capability snapshot integrity via verify_snapshot_integrity()
"""
import time

import pytest

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    ModelPolicy,
    ToolPolicy,
    TraceBundle,
    WorkerResult,
    WorkerResultStatus,
    make_trace,
)
from worker.core.runtime_target import SelectedWorkerRuntimeRef, WorkerKind, WorkerRuntimeKind, WorkerSelectionMode
from worker.core.preflight import (
    REASON_APPROVAL_MISSING,
    REASON_CONTEXT_MISSING,
    REASON_DENIED_OPERATION,
    REASON_INVALID_REQUEST,
    REASON_MISSING_CAPABILITY,
    REASON_PROVIDER_BLOCKED,
    REASON_SNAPSHOT_MISMATCH,
    REASON_TASK_KIND_UNKNOWN,
    REASON_TOOL_UNAVAILABLE,
    PreflightDecision,
    PreflightGate,
    verify_snapshot_integrity,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _env(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id="task-001",
        actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=["planning"]),
        context_envelope_ref="ctx:001",
        audit_correlation_id="audit:001",
        selected_worker_runtime=SelectedWorkerRuntimeRef(
            selected_worker_id="w-local-1",
            selected_worker_kind=WorkerKind.native_ananta_worker,
            selected_runtime_target_id="rt-local-1",
            selected_runtime_kind=WorkerRuntimeKind.local_process,
            selection_mode=WorkerSelectionMode.automatic,
        ),
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


def _approval(operation: str) -> ApprovalRef:
    return ApprovalRef(
        ref_id=f"ref-{operation}",
        operation=operation,
        granted_at=time.time(),
        granted_by="user:admin",
    )


GATE = PreflightGate()
KNOWN_TASK_KINDS = frozenset({"plan", "patch", "shell", "test"})


# ── EW-T008: Basic preflight check ───────────────────────────────────────────

class TestPreflightCheck:
    def test_valid_low_risk_envelope_allowed(self):
        env = _env()
        result = GATE.check(env)
        assert result.allowed
        assert result.decision == PreflightDecision.allow

    def test_empty_task_id_invalid_request(self):
        # Pydantic catches empty task_id at construction; test via field override
        # We test what preflight does when somehow given a bad envelope
        env = _env()
        object.__setattr__(env, "task_id", "")
        result = GATE.check(env)
        assert result.decision == PreflightDecision.invalid_request
        assert result.reason_code == REASON_INVALID_REQUEST

    def test_empty_context_envelope_ref_blocked(self):
        env = _env()
        object.__setattr__(env, "context_envelope_ref", "")
        result = GATE.check(env)
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_CONTEXT_MISSING

    def test_empty_capabilities_blocked(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=[]))
        result = GATE.check(env)
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_MISSING_CAPABILITY

    def test_confirm_required_cap_without_approval_needs_approval(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["patch_apply"]))
        result = GATE.check(env)
        assert result.decision == PreflightDecision.confirm_required
        assert result.reason_code == REASON_APPROVAL_MISSING
        assert result.detail == "patch_apply"

    def test_confirm_required_cap_with_approval_allowed(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["patch_apply"]),
            approval_refs=[_approval("patch_apply")],
        )
        result = GATE.check(env)
        assert result.allowed

    def test_shell_execute_without_approval_needs_approval(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["shell_execute"]))
        result = GATE.check(env)
        assert result.decision == PreflightDecision.confirm_required

    def test_shell_execute_with_approval_allowed(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["shell_execute"]),
            approval_refs=[_approval("shell_execute")],
        )
        result = GATE.check(env)
        assert result.allowed

    def test_multiple_confirm_required_caps_first_missing_blocks(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["patch_apply", "memory_write"]),
            approval_refs=[_approval("memory_write")],  # only one approval
        )
        result = GATE.check(env)
        assert result.decision == PreflightDecision.confirm_required
        assert result.detail == "patch_apply"

    def test_multiple_confirm_required_caps_all_approved(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["patch_apply", "memory_write"]),
            approval_refs=[_approval("patch_apply"), _approval("memory_write")],
        )
        result = GATE.check(env)
        assert result.allowed


# ── EW-T009: Fail-closed default-deny ────────────────────────────────────────

class TestFailClosed:
    def test_unknown_provider_blocked_when_allowlist_set(self):
        # With an explicit allowlist, providers not in it are blocked (fail-closed)
        env = _env(model_policy=ModelPolicy(allowed_providers=["ollama"], cloud_allowed=False))
        result = GATE.check_provider(env, "some_unknown_provider")
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_PROVIDER_BLOCKED

    def test_empty_allowlist_allows_local_provider_by_default(self):
        env = _env(model_policy=ModelPolicy(cloud_allowed=False))
        result = GATE.check_provider(env, "local_ollama_fork")
        assert result.allowed

    def test_cloud_provider_blocked_when_cloud_not_allowed(self):
        env = _env(model_policy=ModelPolicy(cloud_allowed=False))
        for provider in ["openai", "anthropic", "gemini", "groq"]:
            result = GATE.check_provider(env, provider)
            assert result.decision == PreflightDecision.blocked, f"{provider} should be blocked"

    def test_allowed_provider_passes(self):
        env = _env(model_policy=ModelPolicy(allowed_providers=["ollama"], cloud_allowed=False))
        result = GATE.check_provider(env, "ollama")
        assert result.allowed

    def test_unknown_tool_blocked(self):
        env = _env(tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]))
        result = GATE.check_tool(env, "shell_exec")
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_TOOL_UNAVAILABLE

    def test_allowed_tool_passes(self):
        env = _env(tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]))
        result = GATE.check_tool(env, "read_file")
        assert result.allowed

    def test_empty_tool_allowlist_denies_by_default(self):
        env = _env(tool_policy=ToolPolicy())
        result = GATE.check_tool(env, "any_tool")
        assert result.decision == PreflightDecision.blocked

    def test_legacy_tool_allowlist_override_allows(self):
        env = _env(tool_policy=ToolPolicy(legacy_default_allow=True))
        result = GATE.check_tool(env, "any_tool")
        assert result.allowed

    def test_denied_tool_via_override_blocked(self):
        env = _env(tool_policy=ToolPolicy(
            allowed_tool_ids=["read_file", "shell_exec"],
            approval_overrides={"shell_exec": "deny"},
        ))
        result = GATE.check_tool(env, "shell_exec")
        assert result.decision == PreflightDecision.blocked

    def test_unknown_task_kind_denied(self):
        env = _env()
        result = GATE.check_task_kind(env, "hack_all_things", KNOWN_TASK_KINDS)
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_TASK_KIND_UNKNOWN

    def test_known_task_kind_allowed(self):
        env = _env()
        result = GATE.check_task_kind(env, "plan", KNOWN_TASK_KINDS)
        assert result.allowed

    def test_denied_operation_blocked(self):
        env = _env(denied_operations=["shell_execute"])
        result = GATE.check_operation(env, "shell_execute")
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_DENIED_OPERATION

    def test_operation_not_in_allowlist_blocked(self):
        env = _env(allowed_operations=["read_file"])
        result = GATE.check_operation(env, "write_file")
        assert result.decision == PreflightDecision.blocked

    def test_operation_in_allowlist_passes(self):
        env = _env(allowed_operations=["read_file"])
        result = GATE.check_operation(env, "read_file")
        assert result.allowed

    def test_empty_allowed_operations_allows_all(self):
        env = _env(allowed_operations=[])
        result = GATE.check_operation(env, "anything")
        assert result.allowed

    def test_denied_overrides_allowed_in_operation_check(self):
        env = _env(
            allowed_operations=["shell_execute"],
            denied_operations=["shell_execute"],
        )
        result = GATE.check_operation(env, "shell_execute")
        assert result.decision == PreflightDecision.blocked


# ── EW-T010: Capability snapshot integrity ────────────────────────────────────

class TestSnapshotIntegrity:
    def test_matching_snapshot_passes(self):
        env = _env()
        trace = make_trace(env)
        result = verify_snapshot_integrity(env, trace)
        assert result.allowed

    def test_tampered_snapshot_detected(self):
        env = _env()
        trace = make_trace(env)
        # Simulate mid-execution tampering
        object.__setattr__(trace, "capability_snapshot_hash", "tampered_hash_xyz")
        result = verify_snapshot_integrity(env, trace)
        assert result.decision == PreflightDecision.blocked
        assert result.reason_code == REASON_SNAPSHOT_MISMATCH

    def test_snapshot_hash_tied_to_capabilities(self):
        env1 = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        env2 = _env(capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]))
        # Trace from env1 should not match env2's snapshot
        trace1 = make_trace(env1)
        result = verify_snapshot_integrity(env2, trace1)
        assert result.decision == PreflightDecision.blocked

    def test_correct_trace_for_env_with_multiple_caps(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
            approval_refs=[],
        )
        trace = make_trace(env)
        result = verify_snapshot_integrity(env, trace)
        assert result.allowed


# ── to_worker_result adapter ──────────────────────────────────────────────────

class TestToWorkerResult:
    def test_confirm_required_becomes_needs_approval(self):
        from worker.core.preflight import PreflightResult
        env = _env()
        trace = make_trace(env)
        pr = PreflightResult(
            decision=PreflightDecision.confirm_required,
            reason_code=REASON_APPROVAL_MISSING,
            detail="patch_apply",
        )
        result = GATE.to_worker_result(env, pr, trace)
        assert result.status == WorkerResultStatus.needs_approval
        assert result.no_side_effects_confirmed is True

    def test_blocked_becomes_denied(self):
        from worker.core.preflight import PreflightResult
        env = _env()
        trace = make_trace(env)
        pr = PreflightResult(
            decision=PreflightDecision.blocked,
            reason_code=REASON_MISSING_CAPABILITY,
        )
        result = GATE.to_worker_result(env, pr, trace)
        assert result.status == WorkerResultStatus.denied
        assert result.no_side_effects_confirmed is True

    def test_trace_bundle_always_present_in_result(self):
        from worker.core.preflight import PreflightResult
        env = _env()
        trace = make_trace(env)
        pr = PreflightResult(decision=PreflightDecision.blocked, reason_code="x")
        result = GATE.to_worker_result(env, pr, trace)
        assert result.trace_bundle is not None


# ── Integration: full envelope-through-gate flow ──────────────────────────────

class TestIntegration:
    def test_low_risk_envelope_passes_all_checks(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
            model_policy=ModelPolicy(allowed_providers=["ollama"], cloud_allowed=False),
            tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]),
            allowed_operations=["read_file"],
        )
        assert GATE.check(env).allowed
        assert GATE.check_provider(env, "ollama").allowed
        assert GATE.check_tool(env, "read_file").allowed
        assert GATE.check_operation(env, "read_file").allowed
        assert GATE.check_task_kind(env, "plan", KNOWN_TASK_KINDS).allowed
        trace = make_trace(env)
        assert verify_snapshot_integrity(env, trace).allowed

    def test_patch_apply_requires_approval_end_to_end(self):
        env_no_approval = _env(
            capability_grant=CapabilityGrant(capabilities=["patch_apply"]),
        )
        result = GATE.check(env_no_approval)
        assert result.decision == PreflightDecision.confirm_required

        env_with_approval = _env(
            capability_grant=CapabilityGrant(capabilities=["patch_apply"]),
            approval_refs=[_approval("patch_apply")],
        )
        result2 = GATE.check(env_with_approval)
        assert result2.allowed

    def test_cloud_call_blocked_end_to_end(self):
        env = _env(model_policy=ModelPolicy(cloud_allowed=False))
        result = GATE.check_provider(env, "openai")
        trace = make_trace(env)
        worker_result = GATE.to_worker_result(env, result, trace)
        assert worker_result.status == WorkerResultStatus.denied
        assert worker_result.no_side_effects_confirmed is True
