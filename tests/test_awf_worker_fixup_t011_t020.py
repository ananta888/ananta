"""AWF-T011 – AWF-T020: resource limits, provider registry/selection/health/provenance,
code-context gate, ContextEnvelope adapter, context budget, context sensitivity filter.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from worker.core.context_bundle_adapter import ContextEnvelopeAdapter, ContextEnvelopeRef
from worker.core.context_resolver import (
    ContextBlock,
    ContextBudgetGate,
    ContextSensitivityFilter,
    ContextSensitivity,
    TokenBudget,
)
from worker.core.provider_registry import (
    CredentialIsolationProof,
    ModelPolicy,
    ProviderDiagnostic,
    ProviderEntry,
    ProviderHealthGate,
    ProviderKind,
    ProviderProvenanceRef,
    ProviderSelectionGate,
    ProviderStatus,
    WorkerProviderRegistry,
    build_default_provider_registry,
)
from worker.core.tool_registry import ResourceLimitEnforcer, ResourceLimits, WorkerToolEntry, WorkerToolRegistry, build_default_registry


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T011: Resource limits per tool call
# ══════════════════════════════════════════════════════════════════════════════

class TestT011ResourceLimits:
    def test_enforcer_reads_limits_from_registry(self):
        registry = build_default_registry()
        enforcer = ResourceLimitEnforcer(registry)
        limits = enforcer.limits_for("run_shell")
        assert limits.timeout_seconds > 0
        assert limits.max_output_chars > 0

    def test_enforcer_unknown_tool_returns_defaults(self):
        enforcer = ResourceLimitEnforcer(build_default_registry())
        limits = enforcer.limits_for("nonexistent_tool")
        assert limits == ResourceLimits()

    def test_bound_output_truncates(self):
        registry = WorkerToolRegistry()
        registry.register(WorkerToolEntry(
            id="run_shell", kind="shell",
            capability_classes=("shell_execute",), risk_class="high",
            resource_limits=ResourceLimits(max_output_chars=20),
        ))
        enforcer = ResourceLimitEnforcer(registry)
        out, truncated = enforcer.bound_output("a" * 100, "run_shell")
        assert truncated
        assert len(out) == 20

    def test_bound_output_no_truncation(self):
        enforcer = ResourceLimitEnforcer(build_default_registry())
        out, truncated = enforcer.bound_output("short", "run_shell")
        assert not truncated
        assert out == "short"

    def test_effective_timeout_respects_registry(self):
        registry = WorkerToolRegistry()
        registry.register(WorkerToolEntry(
            id="run_shell", kind="shell",
            capability_classes=("shell_execute",), risk_class="high",
            resource_limits=ResourceLimits(timeout_seconds=10.0),
        ))
        enforcer = ResourceLimitEnforcer(registry)
        assert enforcer.effective_timeout("run_shell", 999.0) == 10.0

    def test_effective_timeout_requested_lower(self):
        enforcer = ResourceLimitEnforcer(build_default_registry())
        assert enforcer.effective_timeout("run_shell", 1.0) == 1.0

    def test_native_service_uses_registry_limits(self, tmp_path):
        from agent.services.native_worker_runtime_service import _RESOURCE_ENFORCER
        limits = _RESOURCE_ENFORCER.limits_for("run_shell")
        assert limits.timeout_seconds > 0

    def test_native_service_output_bounded(self, tmp_path):
        from agent.services.native_worker_runtime_service import NativeWorkerRuntimeService
        svc = NativeWorkerRuntimeService()
        cfg = {"worker_runtime": {"native_worker_runtime": {"enabled": True}}}
        result = svc.execute_and_verify_command(
            tid="t-1", task={}, command="echo hello", trace_id="tr-1",
            worker_profile="balanced", profile_source="agent_default",
            timeout_seconds=30, workspace_dir=tmp_path,
            native_runtime_payload=None, agent_cfg=cfg,
        )
        assert result["status"] in {"completed", "failed"}
        # stdout/stderr in execution_result are bounded
        exec_r = result.get("native_runtime", {}).get("execution_result") or {}
        if exec_r:
            assert len(str(exec_r.get("stdout_ref", ""))) <= 32_100  # some slack


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T012 + AWF-T013: Provider selection via registry + policy
# ══════════════════════════════════════════════════════════════════════════════

class TestT012T013ProviderSelection:
    def _make_registry(self) -> WorkerProviderRegistry:
        registry = WorkerProviderRegistry()
        registry.register(ProviderEntry(id="ollama", kind=ProviderKind.local, priority=10))
        registry.register(ProviderEntry(id="openai", kind=ProviderKind.cloud, priority=100))
        registry.register(ProviderEntry(id="local_mock", kind=ProviderKind.local_mock, priority=99))
        return registry

    def test_local_provider_selected_by_default(self):
        gate = ProviderSelectionGate(self._make_registry())
        entry, reason = gate.select(policy=ModelPolicy(cloud_allowed=False))
        assert entry is not None
        assert entry.kind in {ProviderKind.local, ProviderKind.local_mock}
        assert "allow" in reason

    def test_cloud_blocked_when_policy_disallows(self):
        gate = ProviderSelectionGate(self._make_registry())
        entry, reason = gate.select(
            policy=ModelPolicy(cloud_allowed=False),
            preferred_provider="openai",
        )
        assert entry is None or entry.kind != ProviderKind.cloud

    def test_cloud_selected_when_allowed(self):
        registry = WorkerProviderRegistry()
        registry.register(ProviderEntry(id="openai", kind=ProviderKind.cloud, priority=10))
        gate = ProviderSelectionGate(registry)
        entry, reason = gate.select(
            policy=ModelPolicy(cloud_allowed=True, allowed_providers=["openai"]),
            preferred_provider="openai",
        )
        assert entry is not None
        assert entry.id == "openai"

    def test_allowlist_filters_providers(self):
        gate = ProviderSelectionGate(self._make_registry())
        entry, reason = gate.select(
            policy=ModelPolicy(cloud_allowed=False, allowed_providers=["ollama"]),
        )
        assert entry is not None
        assert entry.id == "ollama"

    def test_no_provider_available_returns_none(self):
        empty = WorkerProviderRegistry()
        gate = ProviderSelectionGate(empty)
        entry, reason = gate.select(policy=ModelPolicy(cloud_allowed=False))
        assert entry is None
        assert reason == "no_provider_available"

    def test_select_from_envelope_convenience(self):
        gate = ProviderSelectionGate(self._make_registry())
        entry, reason = gate.select_from_envelope(
            cloud_allowed=False,
            allowed_providers=[],
            preferred_provider="ollama",
        )
        assert entry is not None

    def test_model_policy_is_cloud_allowed(self):
        assert ModelPolicy(cloud_allowed=True).is_cloud_allowed()
        assert not ModelPolicy(cloud_allowed=False).is_cloud_allowed()

    def test_model_policy_provider_allowed(self):
        policy = ModelPolicy(allowed_providers=["ollama", "local_mock"])
        assert policy.is_provider_allowed("ollama")
        assert not policy.is_provider_allowed("openai")

    def test_model_policy_empty_allowlist_allows_all(self):
        policy = ModelPolicy(allowed_providers=[])
        assert policy.is_provider_allowed("openai")
        assert policy.is_provider_allowed("anything")

    def test_default_registry_has_local_and_cloud(self):
        registry = build_default_provider_registry()
        gate = ProviderSelectionGate(registry)
        # Local-only policy should find at least one provider
        entry, reason = gate.select(policy=ModelPolicy(cloud_allowed=False))
        assert entry is not None


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T014: Credential isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestT014CredentialIsolation:
    def test_proof_isolated_with_clean_env(self):
        env = {"PATH": "/usr/bin", "LANG": "C.UTF-8"}
        proof = CredentialIsolationProof.verify("ollama", env)
        assert proof.is_isolated
        assert len(proof.credential_vars_leaked) == 0

    def test_proof_detects_leaked_foreign_key(self):
        env = {"OPENAI_API_KEY": "sk-test", "PATH": "/usr/bin"}
        proof = CredentialIsolationProof.verify("ollama", env)
        assert not proof.is_isolated
        assert "OPENAI_API_KEY" in proof.credential_vars_leaked

    def test_proof_allows_scoped_key_for_own_provider(self):
        env = {"OPENAI_API_KEY": "sk-test", "PATH": "/usr/bin"}
        proof = CredentialIsolationProof.verify("openai", env)
        # OPENAI_API_KEY is the scoped key for openai — not leaked
        assert proof.is_isolated
        assert len(proof.credential_vars_leaked) == 0

    def test_subprocess_env_from_registry_is_clean(self):
        registry = build_default_provider_registry()
        env = registry.subprocess_env("ollama")
        proof = CredentialIsolationProof.verify("ollama", env)
        assert proof.is_isolated

    def test_command_executor_env_has_no_credential_vars(self, tmp_path):
        from worker.shell.command_executor import execute_command_plan
        plan = {
            "schema": "command_plan_artifact.v1",
            "task_id": "t-1", "capability_id": "shell_execute",
            "command": "env", "command_hash": "abc",
            "explanation": "test", "risk_classification": "low",
            "required_approval": False, "working_directory": ".",
            "expected_effects": [],
        }
        result = execute_command_plan(
            repository_root=tmp_path, command_plan_artifact=plan,
            task_id="t-1", capability_id="shell_execute", context_hash="ctx",
            shell_policy={"allowlist": ["env"], "approval_required_commands": [], "denylist_tokens": []},
            hub_policy_decision="allow",
        )
        # The subprocess env should not contain API keys
        output = result.stdout
        for cred_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"):
            assert cred_var not in output


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T015: Provider health gate
# ══════════════════════════════════════════════════════════════════════════════

class TestT015ProviderHealthGate:
    def test_healthy_provider_passes(self):
        registry = build_default_provider_registry()
        gate = ProviderHealthGate()
        healthy, reason = gate.check_from_registry("ollama", registry)
        assert healthy

    def test_unregistered_provider_fails(self):
        registry = WorkerProviderRegistry()
        gate = ProviderHealthGate()
        healthy, reason = gate.check_from_registry("nonexistent", registry)
        assert not healthy
        assert reason == "provider_not_registered"

    def test_recorded_failure_propagates(self):
        registry = build_default_provider_registry()
        gate = ProviderHealthGate()
        gate.record_failure("ollama", registry, status=ProviderStatus.unavailable, error_detail="connection refused")
        healthy, reason = gate.check_from_registry("ollama", registry)
        assert not healthy
        assert "unavailable" in reason

    def test_record_success_clears_failure(self):
        registry = build_default_provider_registry()
        gate = ProviderHealthGate()
        gate.record_failure("ollama", registry, status=ProviderStatus.timeout)
        gate.record_success("ollama", registry, latency_ms=50.0)
        healthy, reason = gate.check_from_registry("ollama", registry)
        assert healthy

    def test_unauthorized_provider_blocked(self):
        registry = build_default_provider_registry()
        gate = ProviderHealthGate()
        gate.record_failure("openai", registry, status=ProviderStatus.unauthorized, error_detail="invalid key")
        healthy, reason = gate.check_from_registry("openai", registry)
        assert not healthy

    def test_diagnostic_recorded_in_registry(self):
        registry = build_default_provider_registry()
        gate = ProviderHealthGate()
        diag = gate.record_failure("ollama", registry, error_detail="port closed")
        diags = registry.diagnostics()
        assert any(d["provider_id"] == "ollama" for d in diags)

    def test_cloud_provider_assumed_available_without_probe(self):
        registry = build_default_provider_registry()
        gate = ProviderHealthGate()
        healthy, reason = gate.check_from_registry("openai", registry)
        assert healthy
        assert "unprobed" in reason or "available" in reason


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T016: Provider provenance in artifacts
# ══════════════════════════════════════════════════════════════════════════════

class TestT016ProviderProvenance:
    def test_native_worker_provenance(self):
        ref = ProviderProvenanceRef.native_worker()
        assert ref.provider_id == "native_worker"
        assert ref.model_id == "native_command_runtime"

    def test_from_entry_includes_hash(self):
        entry = ProviderEntry(id="ollama", kind=ProviderKind.local, base_url="http://localhost:11434")
        ref = ProviderProvenanceRef.from_entry(entry, model_id="llama3")
        assert ref.provider_id == "ollama"
        assert ref.model_id == "llama3"
        assert len(ref.entry_hash) == 16  # truncated sha256

    def test_provenance_as_dict_no_secrets(self):
        ref = ProviderProvenanceRef.native_worker()
        d = ref.as_dict()
        assert "provider_id" in d
        assert "model_id" in d
        assert "api_key" not in str(d).lower()
        assert "secret" not in str(d).lower()

    def test_from_entry_deterministic(self):
        entry = ProviderEntry(id="ollama", kind=ProviderKind.local)
        ref1 = ProviderProvenanceRef.from_entry(entry, model_id="llama3")
        ref2 = ProviderProvenanceRef.from_entry(entry, model_id="llama3")
        assert ref1.entry_hash == ref2.entry_hash

    def test_native_service_has_provenance_in_model_metadata(self, tmp_path):
        from agent.services.native_worker_runtime_service import NativeWorkerRuntimeService
        svc = NativeWorkerRuntimeService()
        cfg = {"worker_runtime": {"native_worker_runtime": {"enabled": True}}}
        result = svc.execute_and_verify_command(
            tid="t-1", task={}, command="echo hi", trace_id="tr-1",
            worker_profile="balanced", profile_source="agent_default",
            timeout_seconds=30, workspace_dir=tmp_path,
            native_runtime_payload=None, agent_cfg=cfg,
        )
        exec_r = result.get("native_runtime", {}).get("execution_result") or {}
        if exec_r:
            meta = exec_r.get("model_metadata") or {}
            assert meta.get("provider") == "native_worker"
            assert meta.get("provider_hash") == "native"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T017: CodeCompass/RAG context mandatory for code-aware modes
# ══════════════════════════════════════════════════════════════════════════════

class TestT017CodeContextRequired:
    def _make_runtime(self, **kwargs):
        from tests.test_awf_worker_fixup_t001_t010 import (
            _AllowPolicyPort, _ListTracePort, _ManifestArtifactPort,
        )
        from worker.runtime.standalone_runtime import StandaloneRuntime
        from worker.core.tool_registry import build_default_registry
        tp = _ListTracePort()
        ap = _ManifestArtifactPort()
        rt = StandaloneRuntime(
            policy_port=_AllowPolicyPort(),
            trace_port=tp,
            artifact_port=ap,
            tool_registry=build_default_registry(),
            **kwargs,
        )
        return rt, tp, ap

    def _code_todo_contract(self, *, with_context: bool) -> dict:
        from tests.test_awf_worker_fixup_t001_t010 import _todo_contract
        contract = _todo_contract()
        if with_context:
            contract["control_manifest"]["context_ref"] = "ctx-bundle-123"
        else:
            contract["control_manifest"].pop("context_ref", None)
            contract["control_manifest"].pop("context_hash", None)
        # Use ananta_worker executor with patch_apply mode to trigger code-aware
        contract["worker"]["executor_kind"] = "ananta_worker"
        contract["execution"]["mode"] = "patch_apply"
        return contract

    def test_code_aware_mode_requires_context(self):
        rt, _, _ = self._make_runtime()
        result = rt.run(task_contract=self._code_todo_contract(with_context=False), workspace_dir="/tmp")
        assert result["status"] == "degraded"
        assert result["reason"] == "code_context_required"

    def test_code_aware_mode_with_context_passes_gate(self):
        rt, _, _ = self._make_runtime()
        result = rt.run(task_contract=self._code_todo_contract(with_context=True), workspace_dir="/tmp")
        # Should NOT fail with code_context_required (may fail for other reasons)
        assert result.get("reason") != "code_context_required"

    def test_non_code_mode_no_context_required(self):
        from tests.test_awf_worker_fixup_t001_t010 import _todo_contract
        rt, _, _ = self._make_runtime()
        contract = _todo_contract()  # assistant_execute mode — not code-aware
        result = rt.run(task_contract=contract, workspace_dir="/tmp")
        assert result.get("reason") != "code_context_required"

    def test_is_code_aware_mode_patch_apply(self):
        from worker.runtime.standalone_runtime import _is_code_aware_mode
        from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
        env = ExecutionEnvelope(
            task_id="t-1", actor_ref="hub", context_envelope_ref="ctx",
            audit_correlation_id="audit",
            capability_grant=CapabilityGrant(capabilities=["patch_apply"]),
        )
        assert _is_code_aware_mode(env)

    def test_is_code_aware_mode_shell_execute_not_code_aware(self):
        from worker.runtime.standalone_runtime import _is_code_aware_mode
        from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
        env = ExecutionEnvelope(
            task_id="t-1", actor_ref="hub", context_envelope_ref="ctx",
            audit_correlation_id="audit",
            capability_grant=CapabilityGrant(capabilities=["shell_execute", "shell_plan"]),
        )
        assert not _is_code_aware_mode(env)


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T018: ContextEnvelope compatibility layer
# ══════════════════════════════════════════════════════════════════════════════

class TestT018ContextEnvelopeAdapter:
    def test_from_raw_string(self):
        ref = ContextEnvelopeRef.from_raw("ctx-bundle-123")
        assert ref.bundle_id == "ctx-bundle-123"
        assert ref.context_hash == ""

    def test_from_raw_dict(self):
        ref = ContextEnvelopeRef.from_raw({
            "context_bundle_id": "bundle-1",
            "context_hash": "abc123",
            "retrieval_refs": [{"source_type": "task_description"}],
        })
        assert ref.bundle_id == "bundle-1"
        assert ref.context_hash == "abc123"
        assert len(ref.retrieval_refs) == 1

    def test_empty_ref_detected(self):
        ref = ContextEnvelopeRef.from_raw("")
        assert ref.is_empty()

    def test_adapter_returns_stub_for_no_retrieval_refs(self):
        adapter = ContextEnvelopeAdapter()
        blocks, errors = adapter.resolve("ctx-bundle-456")
        assert len(errors) == 0
        assert len(blocks) == 1
        assert blocks[0].source_type == "task_description"
        assert blocks[0].origin_id == "ctx-bundle-456"

    def test_adapter_empty_ref_returns_error(self):
        adapter = ContextEnvelopeAdapter()
        blocks, errors = adapter.resolve("")
        assert len(blocks) == 0
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_adapter_uses_preloaded_blocks(self):
        adapter = ContextEnvelopeAdapter()
        preloaded = [ContextBlock("file", "main.py", "test", content="x = 1")]
        blocks, errors = adapter.resolve("ctx-1", preloaded_blocks=preloaded)
        assert len(errors) == 0
        assert blocks[0].origin_id == "main.py"

    def test_adapter_resolves_retrieval_refs(self):
        adapter = ContextEnvelopeAdapter()
        raw = {
            "context_bundle_id": "b1",
            "context_hash": "h1",
            "retrieval_refs": [
                {"source_type": "task_description", "origin_id": "task-1", "content": "fix bug", "provenance": "hub"},
            ],
        }
        blocks, errors = adapter.resolve(raw)
        assert len(errors) == 0
        assert any(b.origin_id == "task-1" for b in blocks)

    def test_resolve_from_bundle_builds_block(self):
        adapter = ContextEnvelopeAdapter()
        block = adapter.resolve_from_bundle("bundle-7", content="def foo(): pass")
        assert block.source_type == "task_description"
        assert block.origin_id == "bundle-7"
        assert block.token_estimate > 0

    def test_ref_as_dict_round_trip(self):
        ref = ContextEnvelopeRef.from_raw({"context_bundle_id": "b1", "context_hash": "h1"})
        d = ref.as_dict()
        assert d["context_bundle_id"] == "b1"
        assert d["context_hash"] == "h1"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T019: Context budget enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestT019ContextBudget:
    def _block(self, tokens: int, priority: int = 50, p0: bool = False) -> ContextBlock:
        return ContextBlock(
            source_type="test", origin_id=f"b{tokens}",
            provenance="test",
            token_estimate=tokens,
            priority=0 if p0 else priority,
        )

    def test_budget_gate_reserves_output_tokens(self):
        budget = TokenBudget(global_limit=1000)
        gate = ContextBudgetGate(budget, output_reserve_tokens=200)
        assert gate.effective_limit == 800

    def test_budget_gate_drops_overflow_blocks(self):
        budget = TokenBudget(global_limit=500)
        gate = ContextBudgetGate(budget, output_reserve_tokens=0)
        blocks = [self._block(300), self._block(300)]
        kept, warnings = gate.check(blocks)
        total = sum(b.token_estimate for b in kept)
        assert total <= 500
        assert len(warnings) > 0

    def test_budget_gate_never_drops_p0(self):
        budget = TokenBudget(global_limit=100)
        gate = ContextBudgetGate(budget, output_reserve_tokens=0)
        p0 = self._block(200, p0=True)   # P0, exceeds budget
        low = self._block(50, priority=90)
        kept, warnings = gate.check([p0, low])
        assert any(b.is_p0 for b in kept)

    def test_budget_gate_is_over_budget(self):
        budget = TokenBudget(global_limit=100)
        gate = ContextBudgetGate(budget, output_reserve_tokens=20)
        blocks = [self._block(200)]
        assert gate.is_over_budget(blocks)

    def test_budget_gate_not_over_budget(self):
        budget = TokenBudget(global_limit=1000)
        gate = ContextBudgetGate(budget, output_reserve_tokens=100)
        blocks = [self._block(50), self._block(50)]
        assert not gate.is_over_budget(blocks)

    def test_budget_gate_with_output_reserve(self):
        budget = TokenBudget(global_limit=200)
        gate = ContextBudgetGate(budget, output_reserve_tokens=100)
        # effective limit = 100; 3 × 50 = 150 > 100 → some dropped
        blocks = [self._block(50, priority=10), self._block(50, priority=20), self._block(50, priority=30)]
        kept, warnings = gate.check(blocks)
        total = sum(b.token_estimate for b in kept)
        assert total <= 100


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T020: Context sensitivity filter
# ══════════════════════════════════════════════════════════════════════════════

class TestT020ContextSensitivity:
    def _block(self, sensitivity: ContextSensitivity) -> ContextBlock:
        return ContextBlock(
            source_type="file", origin_id=f"f-{sensitivity.value}",
            provenance="test", sensitivity=sensitivity,
        )

    def test_cloud_filter_removes_confidential(self):
        flt = ContextSensitivityFilter()
        blocks = [self._block(ContextSensitivity.confidential)]
        kept, redacted = flt.filter_for_cloud(blocks)
        assert len(kept) == 0
        assert len(redacted) == 1

    def test_cloud_filter_removes_secret(self):
        flt = ContextSensitivityFilter()
        blocks = [self._block(ContextSensitivity.secret)]
        kept, redacted = flt.filter_for_cloud(blocks)
        assert len(kept) == 0

    def test_cloud_filter_keeps_internal(self):
        flt = ContextSensitivityFilter()
        blocks = [self._block(ContextSensitivity.internal)]
        kept, redacted = flt.filter_for_cloud(blocks)
        assert len(kept) == 1
        assert len(redacted) == 0

    def test_cloud_filter_keeps_public(self):
        flt = ContextSensitivityFilter()
        blocks = [self._block(ContextSensitivity.public)]
        kept, _ = flt.filter_for_cloud(blocks)
        assert len(kept) == 1

    def test_local_filter_keeps_all(self):
        flt = ContextSensitivityFilter()
        blocks = [
            self._block(ContextSensitivity.secret),
            self._block(ContextSensitivity.confidential),
            self._block(ContextSensitivity.internal),
        ]
        kept = flt.filter_for_local(blocks)
        assert len(kept) == 3

    def test_apply_routes_to_cloud_filter(self):
        flt = ContextSensitivityFilter()
        blocks = [self._block(ContextSensitivity.confidential), self._block(ContextSensitivity.public)]
        kept, redacted = flt.apply(blocks, cloud_allowed=True)
        assert len(kept) == 1  # only public
        assert len(redacted) == 1

    def test_apply_routes_to_local_filter(self):
        flt = ContextSensitivityFilter()
        blocks = [self._block(ContextSensitivity.confidential), self._block(ContextSensitivity.public)]
        kept, redacted = flt.apply(blocks, cloud_allowed=False)
        assert len(kept) == 2  # local: all kept
        assert len(redacted) == 0

    def test_mixed_sensitivity_partial_filter(self):
        flt = ContextSensitivityFilter()
        blocks = [
            self._block(ContextSensitivity.public),
            self._block(ContextSensitivity.internal),
            self._block(ContextSensitivity.confidential),
            self._block(ContextSensitivity.secret),
        ]
        kept, redacted = flt.filter_for_cloud(blocks)
        assert len(kept) == 2
        assert len(redacted) == 2
