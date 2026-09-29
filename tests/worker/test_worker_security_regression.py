"""Worker security regression suite. AWF-T044.

Each test names the bypass class it prevents.
Runs without external services or API keys.
"""
from __future__ import annotations

import pytest


def _make_envelope(**kwargs):
    from tests.test_awf_worker_fixup_t031_t045 import _make_envelope as _env
    return _env(**kwargs)


class TestWorkerSecurityRegression:

    def test_policy_missing_denies_execution(self):
        # BYPASS: missing policy → worker executes without classification
        from tests.test_awf_worker_fixup_t001_t010 import _DenyPolicyPort, _ListTracePort, _ManifestArtifactPort
        from worker.runtime.standalone_runtime import StandaloneRuntime
        from worker.core.tool_registry import build_default_registry
        rt = StandaloneRuntime(
            policy_port=_DenyPolicyPort(),
            trace_port=_ListTracePort(),
            artifact_port=_ManifestArtifactPort(),
            tool_registry=build_default_registry(),
        )
        result = rt.run(
            task_contract={
                "schema": "standalone_task_contract.v1",
                "task_id": "t-sec",
                "command": "rm -rf /",
                "worker": {"profile": "balanced", "profile_source": "agent_default"},
                "execution": {"mode": "command_execute"},
                "control_manifest": {"trace_id": "tr-1", "capability_id": "shell_execute"},
                "expected_result_schema": "worker_execution_result.v1",
            },
            workspace_dir="/tmp",
        )
        assert result["status"] in {"degraded", "failed", "denied"}

    def test_approval_missing_blocks_shell_execute(self):
        # BYPASS: shell_execute without approval_ref → PreflightGate must block
        from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
        from worker.core.preflight import PreflightGate
        env = ExecutionEnvelope(
            task_id="t-1", actor_ref="hub", audit_correlation_id="a",
            context_envelope_ref="ctx",
            capability_grant=CapabilityGrant(capabilities=["shell_execute"]),
        )
        gate = PreflightGate()
        decision = gate.check(env)
        assert not decision.allowed

    def test_tool_not_registered_denied(self):
        # BYPASS: calling unregistered tool skips all resource/policy enforcement
        from worker.core.tool_registry import WorkerToolRegistry
        reg = WorkerToolRegistry()
        assert reg.is_registered("custom_dangerous_tool") is False

    def test_cloud_block_on_confidential_context(self):
        # BYPASS: confidential context leaks to cloud provider
        from worker.core.context_resolver import (
            ContextSensitivityFilter, ContextBlock, ContextSensitivity
        )
        f = ContextSensitivityFilter()
        secret_block = ContextBlock(
            source_type="file", origin_id="creds.json", provenance="p",
            sensitivity=ContextSensitivity.secret, content="SECRET_TOKEN=abc",
        )
        kept, redacted = f.filter_for_cloud([secret_block])
        assert kept == []
        assert len(redacted) == 1

    def test_memory_redaction_before_persist(self):
        # BYPASS: raw secrets written to memory DB
        from worker.core.redaction import redact_text
        raw = "API_KEY=sk-supersecret123"
        redacted = redact_text(raw)
        assert "sk-supersecret123" not in redacted

    def test_subworker_cannot_escalate_capabilities(self):
        # BYPASS: child worker grants itself more power than parent
        env, errors = _make_envelope(
            parent_capabilities=["code_read"],
            reduced_capabilities=["code_read", "shell_execute"],
        )
        assert any("subworker_capability_escalation" in e for e in errors)

    def test_skill_unsafe_manifest_rejected(self):
        # BYPASS: high-risk shell skill loaded with low risk_class
        from worker.skills.skill_manifest import SkillManifest, validate_skill_manifest
        m = SkillManifest(
            id="evil_skill", version="1.0", name="Evil", description="bad",
            required_capabilities=[], allowed_tools=["run_shell"],
            denied_tools=[], risk_class="low",
        )
        errors = validate_skill_manifest(m)
        assert any("risk_class_too_low" in e for e in errors)

    def test_skill_disabled_by_default_cannot_run(self):
        # BYPASS: skill auto-enabled and executed before review
        from worker.skills.skill_registry import SkillRegistry
        from worker.skills.skill_runner import SkillRunner
        from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
        from tests.test_awf_worker_fixup_t021_t030 import _minimal_manifest
        reg = SkillRegistry()
        reg.register(_minimal_manifest())
        runner = SkillRunner(reg)
        env = ExecutionEnvelope(
            task_id="t-1", actor_ref="hub", audit_correlation_id="a",
            context_envelope_ref="ctx",
            capability_grant=CapabilityGrant(capabilities=["skill_execute"]),
        )
        result = runner.run("test_skill", inputs={}, envelope=env)
        assert result.status == "denied"
        assert result.reason == "skill_disabled"

    def test_unknown_audit_event_raises(self):
        # BYPASS: arbitrary event bypasses audit gate
        from worker.core.audit_events import emit_worker_audit_event
        with pytest.raises(ValueError):
            emit_worker_audit_event(event_type="not_a_real_event", task_id="t-1")

    def test_code_aware_mode_without_context_denied(self):
        # BYPASS: code-aware mode runs without context ref (no grounding)
        from tests.test_awf_worker_fixup_t001_t010 import (
            _AllowPolicyPort, _ListTracePort, _ManifestArtifactPort, _todo_contract,
        )
        from worker.runtime.standalone_runtime import StandaloneRuntime
        from worker.core.tool_registry import build_default_registry
        rt = StandaloneRuntime(
            policy_port=_AllowPolicyPort(),
            trace_port=_ListTracePort(),
            artifact_port=_ManifestArtifactPort(),
            tool_registry=build_default_registry(),
        )
        contract = _todo_contract()
        contract["control_manifest"].pop("context_ref", None)
        contract["control_manifest"].pop("context_hash", None)
        contract["worker"]["executor_kind"] = "ananta_worker"
        contract["execution"]["mode"] = "patch_apply"
        result = rt.run(task_contract=contract, workspace_dir="/tmp")
        assert result.get("reason") == "code_context_required"
