"""AWF-T001 – AWF-T010: Worker Fixup — Hub-issued decisions, PreflightGate, CapabilityGrant,
capability vocabulary normalization, CapabilitySnapshot, fail-closed audit, WorkerToolRegistry,
ToolInvocationEnvelope, tool-registry gate, and ToolResult contract.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from worker.cli.standalone_worker_cli import _StaticPolicyPort
from worker.core.tool_registry import (
    ResourceLimits,
    ToolInvocationEnvelope,
    ToolResult,
    WorkerToolEntry,
    WorkerToolRegistry,
    build_default_registry,
)
from worker.runtime.standalone_runtime import (
    StandaloneRuntime,
    _extract_hub_decision,
    _normalize_legacy_capability_id,
    _todo_mode_for_executor,
)
from worker.shell.command_executor import execute_command_plan
from worker.shell.command_planner import build_command_plan_artifact


# ── Fakes ──────────────────────────────────────────────────────────────────────

class _AllowPolicyPort:
    def classify_command(self, *, command: str, profile: str, hub_decision: str = "allow") -> dict[str, Any]:
        if hub_decision == "deny":
            return {"decision": "deny", "risk_classification": "critical", "required_approval": True}
        return {"decision": "allow", "risk_classification": "low", "required_approval": False}


class _DenyPolicyPort:
    def classify_command(self, *, command: str, profile: str, hub_decision: str = "allow") -> dict[str, Any]:
        return {"decision": "deny", "risk_classification": "critical", "required_approval": True}


class _ListTracePort:
    def __init__(self, *, raise_on: str | None = None):
        self.events: list[dict] = []
        self._raise_on = raise_on

    def emit(self, *, event_type: str, payload: dict) -> None:
        if self._raise_on and event_type == self._raise_on:
            raise RuntimeError(f"simulated audit failure on {event_type}")
        self.events.append({"event_type": event_type, "payload": payload})

    def event_types(self) -> list[str]:
        return [e["event_type"] for e in self.events]


class _ManifestArtifactPort:
    def __init__(self) -> None:
        self.artifacts: list[dict] = []

    def publish(self, *, artifact: dict) -> dict:
        self.artifacts.append(dict(artifact))
        return dict(artifact)


def _make_runtime(
    *,
    policy_port=None,
    trace_port=None,
    artifact_port=None,
    tool_registry=None,
) -> tuple[StandaloneRuntime, _ListTracePort, _ManifestArtifactPort]:
    tp = trace_port or _ListTracePort()
    ap = artifact_port or _ManifestArtifactPort()
    pp = policy_port or _AllowPolicyPort()
    rt = StandaloneRuntime(
        policy_port=pp,
        trace_port=tp,
        artifact_port=ap,
        tool_registry=tool_registry,
    )
    return rt, tp, ap


def _standalone_contract(command: str = "ls .", hub_decision: str = "allow", **extra) -> dict:
    return {"task_id": "t-001", "command": command, "hub_decision": hub_decision, **extra}


def _todo_contract(*, mode: str = "assistant_execute", hub_decision: str = "allow") -> dict:
    return {
        "schema": "worker_todo_contract.v1",
        "task_id": "todo-001",
        "goal_id": "goal-001",
        "trace_id": "tr-001",
        "expected_result_schema": "worker_todo_result.v1",
        "control_manifest": {
            "hub_decision": hub_decision,
            "capability_id": "worker.command.execute",
            "trace_id": "tr-001",
            "context_hash": "ctx-hash-abc",
        },
        "worker": {"executor_kind": "custom", "worker_profile": "balanced", "profile_source": "agent_default"},
        "execution": {
            "mode": mode,
            "command": "ls .",
            "runner_prompt": "run ls",
            "enforce_artifacts": False,
        },
        "todo": {
            "version": "v1",
            "track": "test-track",
            "tasks": [
                {
                    "id": "item-1",
                    "title": "run ls",
                    "status": "todo",
                    "instructions": "run ls",
                    "acceptance_criteria": ["ls exits 0"],
                    "expected_artifacts": [{"kind": "test_result", "required": True}],
                }
            ],
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T001: Hub-issued decisions
# ══════════════════════════════════════════════════════════════════════════════

class TestT001HubIssuedDecision:
    def test_hub_deny_blocks_standalone(self):
        rt, tp, ap = _make_runtime()
        result = rt.run(task_contract=_standalone_contract(hub_decision="deny"), workspace_dir="/tmp")
        assert result["status"] == "degraded"
        assert result["reason"] == "policy_denied"
        assert len(ap.artifacts) == 0

    def test_hub_allow_proceeds(self):
        rt, tp, ap = _make_runtime()
        result = rt.run(task_contract=_standalone_contract(hub_decision="allow"), workspace_dir="/tmp")
        assert result["status"] == "completed"

    def test_hub_decision_forwarded_to_policy_port(self):
        calls = []

        class _CapturingPort:
            def classify_command(self, *, command, profile, hub_decision="allow"):
                calls.append(hub_decision)
                return {"decision": "allow", "risk_classification": "low", "required_approval": False}

        rt, _, _ = _make_runtime(policy_port=_CapturingPort())
        rt.run(task_contract=_standalone_contract(hub_decision="allow"), workspace_dir="/tmp")
        assert calls and calls[0] == "allow"

    def test_hub_deny_forwarded_to_policy_port(self):
        calls = []

        class _CapturingPort:
            def classify_command(self, *, command, profile, hub_decision="allow"):
                calls.append(hub_decision)
                return {"decision": hub_decision, "risk_classification": "low", "required_approval": False}

        rt, _, _ = _make_runtime(policy_port=_CapturingPort())
        rt.run(task_contract=_standalone_contract(hub_decision="deny"), workspace_dir="/tmp")
        assert calls and calls[0] == "deny"

    def test_extract_hub_decision_from_top_level(self):
        assert _extract_hub_decision({"hub_decision": "deny"}) == "deny"

    def test_extract_hub_decision_from_control_manifest(self):
        assert _extract_hub_decision({}, control_manifest={"hub_decision": "approval_required"}) == "approval_required"

    def test_extract_hub_decision_defaults_allow(self):
        assert _extract_hub_decision({}) == "allow"

    def test_extract_hub_decision_from_policy_decision_ref(self):
        assert _extract_hub_decision({"policy_decision_ref": {"decision": "deny"}}) == "deny"

    def test_static_policy_port_respects_hub_deny(self):
        port = _StaticPolicyPort()
        result = port.classify_command(command="ls", profile="balanced", hub_decision="deny")
        assert result["decision"] == "deny"

    def test_static_policy_port_hub_allow(self):
        port = _StaticPolicyPort()
        result = port.classify_command(command="ls", profile="balanced", hub_decision="allow")
        assert result["decision"] == "allow"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T002: PreflightGate wired into runtime
# ══════════════════════════════════════════════════════════════════════════════

class TestT002PreflightGate:
    def test_preflight_gate_exists_on_runtime(self):
        rt, _, _ = _make_runtime()
        assert rt._preflight_gate is not None

    def test_approval_required_command_blocked_by_preflight(self):
        class _ApprovalPort:
            def classify_command(self, *, command, profile, hub_decision="allow"):
                return {"decision": "allow", "risk_classification": "high", "required_approval": True}

        rt, tp, ap = _make_runtime(policy_port=_ApprovalPort())
        result = rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        # required_approval=True → no auto-approval_ref → preflight confirm_required
        assert result["status"] == "degraded"
        assert result["reason"] == "approval_required"
        assert len(ap.artifacts) == 0

    def test_safe_command_passes_preflight(self):
        rt, _, ap = _make_runtime()
        result = rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert result["status"] == "completed"
        assert len(ap.artifacts) == 1

    def test_preflight_fires_before_artifact_publish(self):
        """Artifact must not be published if preflight blocks."""
        class _ApprovalPort:
            def classify_command(self, *, command, profile, hub_decision="allow"):
                return {"decision": "allow", "risk_classification": "high", "required_approval": True}

        rt, _, ap = _make_runtime(policy_port=_ApprovalPort())
        rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert len(ap.artifacts) == 0


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T003: CapabilityGrant enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestT003CapabilityGrant:
    def test_envelope_has_shell_capabilities(self):
        from worker.runtime.standalone_runtime import _build_standalone_envelope
        env = _build_standalone_envelope(
            task_id="t-1",
            task_contract={"task_id": "t-1"},
            hub_decision="allow",
            required_approval=False,
        )
        assert env.has_capability("shell_plan")
        assert env.has_capability("shell_execute")

    def test_envelope_capability_grant_snapshot_hash_computed(self):
        from worker.runtime.standalone_runtime import _build_standalone_envelope
        env = _build_standalone_envelope(
            task_id="t-2",
            task_contract={},
            hub_decision="allow",
            required_approval=False,
        )
        assert len(env.capability_grant.snapshot_hash) == 64  # sha256 hex

    def test_legacy_adapter_maps_command_execute_mode(self):
        assert _todo_mode_for_executor("command_execute", "ananta_worker") == "command_execute"

    def test_legacy_adapter_plan_only_mode(self):
        assert _todo_mode_for_executor("plan_only", "custom") == "plan_only"

    def test_legacy_adapter_unknown_mode_uses_executor_kind(self):
        assert _todo_mode_for_executor("assistant_execute", "ananta_worker") == "command_execute"
        assert _todo_mode_for_executor("assistant_execute", "custom") == "plan_only"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T004: Capability vocabulary normalization
# ══════════════════════════════════════════════════════════════════════════════

class TestT004CapabilityVocab:
    def test_normalize_worker_command_plan(self):
        assert _normalize_legacy_capability_id("worker.command.plan") == "shell_plan"

    def test_normalize_worker_command_execute(self):
        assert _normalize_legacy_capability_id("worker.command.execute") == "shell_execute"

    def test_normalize_worker_code_read(self):
        assert _normalize_legacy_capability_id("worker.code.read") == "code_read"

    def test_normalize_worker_code_patch(self):
        assert _normalize_legacy_capability_id("worker.code.patch") == "patch_propose"

    def test_normalize_already_canonical(self):
        assert _normalize_legacy_capability_id("shell_execute") == "shell_execute"

    def test_normalize_empty_returns_empty(self):
        assert _normalize_legacy_capability_id("") == ""

    def test_artifact_uses_canonical_capability_id(self):
        rt, _, ap = _make_runtime()
        rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert len(ap.artifacts) == 1
        assert ap.artifacts[0]["capability_id"] == "shell_plan"  # not "worker.command.plan"

    def test_native_service_uses_shell_execute(self):
        from agent.services.native_worker_runtime_service import NativeWorkerRuntimeService
        svc = NativeWorkerRuntimeService()
        cfg = {"worker_runtime": {"native_worker_runtime": {"enabled": True}}}
        out = svc.prepare_native_command_plan(
            tid="t-1", task={}, command="ls", reason="test", worker_profile="balanced",
            profile_source="agent_default", trace_id="tr-1", context_bundle_id="ctx-1", agent_cfg=cfg,
        )
        plan = (out.get("worker_context_updates") or {}).get("native_runtime", {}).get("command_plan_artifact") or {}
        assert plan.get("capability_id") == "shell_plan"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T005: CapabilitySnapshot in trace events
# ══════════════════════════════════════════════════════════════════════════════

class TestT005CapabilitySnapshot:
    def test_snapshot_hash_in_started_event(self):
        rt, tp, _ = _make_runtime()
        rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        started = next(e for e in tp.events if e["event_type"] == "standalone_runtime_started")
        assert "capability_snapshot_hash" in started["payload"]
        assert len(started["payload"]["capability_snapshot_hash"]) == 64

    def test_snapshot_hash_is_deterministic(self):
        rt, tp, _ = _make_runtime()
        rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        started = next(e for e in tp.events if e["event_type"] == "standalone_runtime_started")
        hash1 = started["payload"]["capability_snapshot_hash"]

        rt2, tp2, _ = _make_runtime()
        rt2.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        started2 = next(e for e in tp2.events if e["event_type"] == "standalone_runtime_started")
        hash2 = started2["payload"]["capability_snapshot_hash"]
        assert hash1 == hash2

    def test_todo_contract_snapshot_hash_in_started_event(self):
        rt, tp, _ = _make_runtime()
        rt.run(task_contract=_todo_contract(), workspace_dir="/tmp")
        started = next(e for e in tp.events if e["event_type"] == "standalone_todo_runtime_started")
        assert "capability_snapshot_hash" in started["payload"]

    def test_hub_decision_in_started_event(self):
        rt, tp, _ = _make_runtime()
        rt.run(task_contract=_standalone_contract(hub_decision="allow"), workspace_dir="/tmp")
        started = next(e for e in tp.events if e["event_type"] == "standalone_runtime_started")
        assert started["payload"]["hub_decision"] == "allow"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T006: Fail-closed audit pipeline
# ══════════════════════════════════════════════════════════════════════════════

class TestT006FailClosedAudit:
    def test_audit_preflight_event_emitted_before_publish(self):
        rt, tp, ap = _make_runtime()
        rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        event_types = tp.event_types()
        audit_idx = event_types.index("mutation_audit_preflight")
        # artifact publish happens after audit check — trace has no direct event for it,
        # but audit must come before finished
        finished_idx = event_types.index("standalone_runtime_finished")
        assert audit_idx < finished_idx

    def test_audit_failure_blocks_mutation(self):
        tp = _ListTracePort(raise_on="mutation_audit_preflight")
        rt, _, ap = _make_runtime(trace_port=tp)
        result = rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert result["status"] == "degraded"
        assert result["reason"] == "audit_pipeline_unavailable"
        assert len(ap.artifacts) == 0

    def test_audit_failure_todo_contract(self):
        tp = _ListTracePort(raise_on="mutation_audit_preflight")
        rt, _, ap = _make_runtime(trace_port=tp)
        result = rt.run(task_contract=_todo_contract(), workspace_dir="/tmp")
        assert result["status"] == "degraded"
        assert result["reason"] == "audit_pipeline_unavailable"

    def test_no_side_effects_on_audit_failure(self):
        tp = _ListTracePort(raise_on="mutation_audit_preflight")
        rt, _, ap = _make_runtime(trace_port=tp)
        rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert len(ap.artifacts) == 0


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T007: WorkerToolRegistry in runtime
# ══════════════════════════════════════════════════════════════════════════════

class TestT007WorkerToolRegistry:
    def test_default_registry_loaded_on_init(self):
        rt, _, _ = _make_runtime()
        assert rt._tool_registry is not None
        assert rt._tool_registry.is_registered("plan_shell")
        assert rt._tool_registry.is_registered("run_shell")

    def test_custom_registry_accepted(self):
        custom = WorkerToolRegistry()
        custom.register(WorkerToolEntry(
            id="plan_shell", kind="shell",
            capability_classes=("shell_plan",), risk_class="low",
        ))
        rt, _, _ = _make_runtime(tool_registry=custom)
        assert rt._tool_registry is custom

    def test_missing_plan_shell_blocks_standalone(self):
        empty = WorkerToolRegistry()
        rt, _, ap = _make_runtime(tool_registry=empty)
        result = rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert result["status"] == "degraded"
        assert "tool_not_registered" in result["reason"]
        assert len(ap.artifacts) == 0

    def test_registry_with_all_tools_allows_execution(self):
        rt, _, ap = _make_runtime(tool_registry=build_default_registry())
        result = rt.run(task_contract=_standalone_contract(), workspace_dir="/tmp")
        assert result["status"] == "completed"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T008: ToolInvocationEnvelope
# ══════════════════════════════════════════════════════════════════════════════

class TestT008ToolInvocationEnvelope:
    def test_invocation_envelope_construction(self):
        inv = ToolInvocationEnvelope(
            execution_id="exec-1",
            tool_id="run_shell",
            arguments={"command": "ls", "cwd": "."},
            capability_ref="shell_execute",
        )
        assert inv.tool_id == "run_shell"
        assert inv.arguments["command"] == "ls"

    def test_invocation_envelope_output_limit(self):
        inv = ToolInvocationEnvelope(
            execution_id="exec-2",
            tool_id="run_shell",
            resource_limits=ResourceLimits(max_output_chars=10),
        )
        out, truncated = inv.apply_output_limit("hello world this is long")
        assert truncated
        assert len(out) == 10

    def test_invocation_envelope_no_truncation(self):
        inv = ToolInvocationEnvelope(
            execution_id="exec-3",
            tool_id="run_shell",
            resource_limits=ResourceLimits(max_output_chars=1000),
        )
        out, truncated = inv.apply_output_limit("short")
        assert not truncated
        assert out == "short"

    def test_invocation_envelope_requires_non_empty_execution_id(self):
        with pytest.raises(Exception):
            ToolInvocationEnvelope(execution_id="", tool_id="run_shell")

    def test_execute_command_plan_uses_resource_limits(self, tmp_path):
        registry = build_default_registry()
        plan = {
            "schema": "command_plan_artifact.v1",
            "task_id": "t-1", "capability_id": "shell_execute",
            "command": "echo hello",
            "command_hash": "abc",
            "explanation": "test",
            "risk_classification": "low",
            "required_approval": False,
            "working_directory": ".",
            "expected_effects": [],
        }
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=plan,
            task_id="t-1",
            capability_id="shell_execute",
            context_hash="ctx-hash",
            shell_policy={"allowlist": ["echo"], "approval_required_commands": [], "denylist_tokens": []},
            hub_policy_decision="allow",
            tool_registry=registry,
        )
        assert isinstance(result, ToolResult)
        assert result.tool_id == "run_shell"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T009: Tool registry gate in command_executor and command_planner
# ══════════════════════════════════════════════════════════════════════════════

class TestT009ToolRegistryGate:
    def _make_plan(self, command: str = "echo hi") -> dict:
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": "t-1", "capability_id": "shell_execute",
            "command": command, "command_hash": "abc",
            "explanation": "test", "risk_classification": "low",
            "required_approval": False, "working_directory": ".",
            "expected_effects": [],
        }

    def _shell_policy(self) -> dict:
        return {"allowlist": ["echo", "ls"], "approval_required_commands": [], "denylist_tokens": []}

    def test_run_shell_not_registered_returns_denied(self, tmp_path):
        empty = WorkerToolRegistry()
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan(),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
            tool_registry=empty,
        )
        assert isinstance(result, ToolResult)
        assert not result.success
        assert result.reason_code == "tool_not_registered"

    def test_run_shell_registered_proceeds(self, tmp_path):
        registry = build_default_registry()
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan(),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
            tool_registry=registry,
        )
        assert isinstance(result, ToolResult)
        assert result.tool_id == "run_shell"

    def test_plan_shell_not_registered_returns_critical_plan(self):
        empty = WorkerToolRegistry()
        plan = build_command_plan_artifact(
            task_id="t-1", capability_id="shell_plan",
            command="ls", explanation="test", expected_effects=["ls"],
            policy={"allowlist": ["ls"], "approval_required_commands": [], "denylist_tokens": []},
            tool_registry=empty,
        )
        assert plan["risk_classification"] == "critical"
        assert plan["required_approval"] is True
        assert "not registered" in plan["explanation"]

    def test_plan_shell_registered_produces_normal_plan(self):
        registry = build_default_registry()
        plan = build_command_plan_artifact(
            task_id="t-1", capability_id="shell_plan",
            command="ls", explanation="test", expected_effects=["ls"],
            policy={"allowlist": ["ls"], "approval_required_commands": [], "denylist_tokens": []},
            tool_registry=registry,
        )
        assert plan["risk_classification"] != "critical"

    def test_no_registry_no_gate(self, tmp_path):
        """Without tool_registry, executor skips T009 gate (backward compat)."""
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan(),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
            tool_registry=None,
        )
        assert isinstance(result, ToolResult)


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T010: ToolResult contract
# ══════════════════════════════════════════════════════════════════════════════

class TestT010ToolResult:
    def _make_plan(self, command: str = "echo hello") -> dict:
        return {
            "schema": "command_plan_artifact.v1",
            "task_id": "t-1", "capability_id": "shell_execute",
            "command": command, "command_hash": "abc",
            "explanation": "test", "risk_classification": "low",
            "required_approval": False, "working_directory": ".",
            "expected_effects": [],
        }

    def _shell_policy(self, allowlist=None) -> dict:
        return {
            "allowlist": allowlist or ["echo", "ls"],
            "approval_required_commands": [],
            "denylist_tokens": [],
        }

    def test_execute_returns_tool_result(self, tmp_path):
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan(),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
        )
        assert isinstance(result, ToolResult)

    def test_tool_result_success_true_on_zero_exit(self, tmp_path):
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan("echo hello"),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
        )
        assert result.success
        assert result.exit_code == 0

    def test_tool_result_has_stdout(self, tmp_path):
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan("echo hello"),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
        )
        assert "hello" in result.stdout

    def test_tool_result_failure_on_nonzero_exit(self, tmp_path):
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan("ls /nonexistent_path_xyz"),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(["ls"]), hub_policy_decision="allow",
        )
        assert not result.success
        assert result.exit_code != 0

    def test_tool_result_duration_set(self, tmp_path):
        result = execute_command_plan(
            repository_root=tmp_path,
            command_plan_artifact=self._make_plan("echo hi"),
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy=self._shell_policy(), hub_policy_decision="allow",
        )
        assert result.duration_seconds is not None
        assert result.duration_seconds >= 0

    def test_to_test_result_artifact_passed(self):
        tr = ToolResult(tool_id="run_shell", execution_id="exec-1", success=True,
                        stdout="hello\n", stderr="", exit_code=0, duration_seconds=0.1)
        artifact = tr.to_test_result_artifact(task_id="t-1", command="echo hello")
        assert artifact["schema"] == "test_result_artifact.v1"
        assert artifact["status"] == "passed"
        assert artifact["exit_code"] == 0
        assert artifact["task_id"] == "t-1"

    def test_to_test_result_artifact_failed(self):
        tr = ToolResult(tool_id="run_shell", execution_id="exec-2", success=False,
                        stdout="", stderr="err", exit_code=1)
        artifact = tr.to_test_result_artifact(task_id="t-1", command="ls /bad")
        assert artifact["status"] == "failed"
        assert artifact["exit_code"] == 1

    def test_to_test_result_artifact_timeout(self):
        tr = ToolResult.timeout("run_shell", "exec-3", partial_stdout="partial")
        artifact = tr.to_test_result_artifact(task_id="t-1", command="sleep 999")
        assert artifact["status"] == "degraded"
        assert artifact["failure_hints"] == ["tool_timeout"]

    def test_tool_result_denied_factory(self):
        tr = ToolResult.denied("run_shell", "exec-4", "tool_not_registered")
        assert not tr.success
        assert tr.reason_code == "tool_not_registered"

    def test_native_service_uses_tool_result(self, tmp_path):
        from agent.services.native_worker_runtime_service import NativeWorkerRuntimeService
        svc = NativeWorkerRuntimeService()
        cfg = {"worker_runtime": {"native_worker_runtime": {"enabled": True}}}
        result = svc.execute_and_verify_command(
            tid="t-1", task={}, command="echo hello", trace_id="tr-1",
            worker_profile="balanced", profile_source="test",
            timeout_seconds=30, workspace_dir=tmp_path,
            native_runtime_payload=None, agent_cfg=cfg,
        )
        assert result["status"] in {"completed", "failed"}
        assert "native_runtime" in result
