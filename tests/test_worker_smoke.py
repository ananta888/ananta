"""Live local worker smoke tests (EW-T058).

Tests the complete plan_only and patch_propose flows using mock provider,
verifying the integration of envelope → preflight → execution → result.
"""
import time
import pytest

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    ModelPolicy,
    ToolPolicy,
    WorkerResult,
    WorkerResultStatus,
)
from worker.core.preflight import PreflightGate
from worker.core.artifact_enforcer import ArtifactEnforcer
from worker.core.diagnostics import AuditEmitter
from worker.core.trace_v2 import TraceBundleV2, ExecutionOutcome
from worker.core.sanitizer import OutputSanitizer
from worker.core.context_scanner import ContextScanner
from worker.core.provider_registry import build_default_provider_registry
from worker.core.tool_registry import build_default_registry
from worker.core.context_resolver import ContextBlock, ContextSensitivity


# ── Mock provider response helpers ───────────────────────────────────────────

MOCK_PLAN_RESPONSE = """
I'll analyze the codebase and create a plan.

1. Read main.py to understand structure
2. Identify the bug in the parse_config function
3. Propose a targeted fix

plan_artifact_id: plan-mock-001
"""

MOCK_PATCH_RESPONSE = """
Here is the fix:

```diff
--- a/main.py
+++ b/main.py
@@ -10,3 +10,3 @@
-    return config.get('key')
+    return config.get('key', None)
```

patch_artifact_id: patch-mock-001
"""


def _make_envelope(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id=f"smoke-{int(time.time())}",
        actor_ref="hub:smoke-test",
        capability_grant=CapabilityGrant(capabilities=["planning"]),
        context_envelope_ref="ctx:smoke",
        audit_correlation_id="audit:smoke",
        model_policy=ModelPolicy(cloud_allowed=False, allowed_providers=["local_mock"]),
        tool_policy=ToolPolicy(allowed_tool_ids=["read_file", "list_files"]),
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


# ── Smoke: plan_only flow ─────────────────────────────────────────────────────

class TestPlanOnlySmoke:
    """Smoke: planning capability → plan_artifact produced."""

    def setup_method(self):
        self.gate = PreflightGate()
        self.enforcer = ArtifactEnforcer()
        self.audit = AuditEmitter()
        self.sanitizer = OutputSanitizer()
        self.scanner = ContextScanner()

    def test_plan_only_flow_complete(self):
        env = _make_envelope(
            capability_grant=CapabilityGrant(capabilities=["planning"]),
        )

        # 1. Preflight
        pre = self.gate.check(
            env, provider_id="local_mock", tool_id="read_file",
            operation="planning", task_kind="task",
        )
        assert pre.allowed, f"Preflight denied: {pre.reason_code}"

        # 2. Audit preflight
        self.audit.emit_preflight(
            "allow",
            correlation_id=env.audit_correlation_id,
            reason_code=None,
            task_id=env.task_id,
            actor_ref=env.actor_ref,
        )

        # 3. Context scan (clean input)
        scan = self.scanner.scan("task", env.task_id, "Fix the parse_config bug")
        assert not scan.blocked

        # 4. Mock provider call → sanitize response
        raw_response = MOCK_PLAN_RESPONSE
        clean_response = self.sanitizer.sanitize(raw_response)
        assert clean_response == raw_response  # no secrets in clean response

        # 5. Produce artifact
        artifact = {"kind": "plan_artifact", "artifact_id": "plan-mock-001"}

        # 6. Enforce artifact-first
        enforcement = self.enforcer.check(
            ["planning"], [artifact],
            summary=self.enforcer.build_summary_with_refs("Plan created", [artifact]),
        )
        assert enforcement.compliant, enforcement.violations

        # 7. Build WorkerResult
        result = WorkerResult(
            status=WorkerResultStatus.success,
            summary=self.enforcer.build_summary_with_refs("Fix plan created", [artifact]),
            artifacts=[artifact],
        )
        assert result.status == WorkerResultStatus.success

        # 8. Verify audit events flushed
        events = self.audit.flush()
        assert len(events) >= 1

    def test_plan_only_cloud_blocked(self):
        """Planning with cloud_allowed=False must not reach a cloud provider."""
        env = _make_envelope(
            capability_grant=CapabilityGrant(capabilities=["planning"]),
            model_policy=ModelPolicy(cloud_allowed=False),
        )
        pre = self.gate.check(
            env, provider_id="openai", tool_id="read_file",
            operation="planning", task_kind="task",
        )
        assert not pre.allowed

    def test_plan_only_local_provider_allowed(self):
        env = _make_envelope()
        pre = self.gate.check(
            env, provider_id="local_mock", tool_id="read_file",
            operation="planning", task_kind="task",
        )
        assert pre.allowed

    def test_plan_only_missing_capability_blocked(self):
        env = _make_envelope(
            capability_grant=CapabilityGrant(capabilities=[]),
        )
        pre = self.gate.check(
            env, provider_id="local_mock", tool_id="read_file",
            operation="planning", task_kind="task",
        )
        assert not pre.allowed

    def test_plan_only_free_text_result_rejected(self):
        enforcement = self.enforcer.check(
            ["planning"], [], summary="I made a great plan"
        )
        assert not enforcement.compliant


# ── Smoke: patch_propose flow ─────────────────────────────────────────────────

class TestPatchProposeSmoke:
    """Smoke: patch_propose capability → patch_artifact produced."""

    def setup_method(self):
        self.gate = PreflightGate()
        self.enforcer = ArtifactEnforcer()
        self.audit = AuditEmitter()
        self.sanitizer = OutputSanitizer()

    def test_patch_propose_flow_complete(self):
        env = _make_envelope(
            capability_grant=CapabilityGrant(capabilities=["patch_propose", "code_read"]),
        )

        # 1. Preflight for code_read
        pre = self.gate.check(
            env, provider_id="local_mock", tool_id="read_file",
            operation="code_read", task_kind="task",
        )
        assert pre.allowed

        # 2. Mock provider returns diff → parse to artifact
        from worker.core.file_policy import FilePolicy
        fp = FilePolicy()
        diff = "--- a/main.py\n+++ b/main.py\n@@ -10,1 +10,1 @@\n-    return config.get('key')\n+    return config.get('key', None)"
        artifact, scope_result = fp.build_patch_artifact(
            artifact_id="patch-mock-001",
            task_id=env.task_id,
            provenance=f"{env.task_id}:patch_propose",
            raw_diff=diff,
            workspace_root="/workspace",
        )
        assert scope_result.allowed

        # 3. Audit patch operation
        self.audit.emit(
            "patch_apply",
            correlation_id=env.audit_correlation_id,
            reason_code=None,
            task_id=env.task_id,
            artifact_id=artifact.artifact_id,
        )

        # 4. Enforce artifact-first
        enforcement = self.enforcer.check(
            ["patch_propose"], [artifact.as_dict()],
            summary=f"Patch {artifact.artifact_id}",
        )
        assert enforcement.compliant, enforcement.violations

        # 5. WorkerResult with artifact ref
        result = WorkerResult(
            status=WorkerResultStatus.success,
            summary=f"Patch proposed: {artifact.artifact_id}",
            artifacts=[artifact.as_dict()],
        )
        assert result.status == WorkerResultStatus.success

    def test_patch_without_approval_blocked_at_apply(self):
        """patch_apply (not patch_propose) requires approval ref."""
        env = _make_envelope(
            capability_grant=CapabilityGrant(capabilities=["patch_apply"]),
            approval_refs=[],
        )
        pre = self.gate.check(
            env, provider_id="local_mock", tool_id="any",
            operation="patch_apply", task_kind="task",
        )
        assert not pre.allowed

    def test_patch_propose_requires_no_approval(self):
        """patch_propose does NOT require an approval ref (only patch_apply does)."""
        env = _make_envelope(
            capability_grant=CapabilityGrant(capabilities=["patch_propose"]),
            approval_refs=[],
        )
        pre = self.gate.check(
            env, provider_id="local_mock", tool_id="any",
            operation="patch_propose", task_kind="task",
        )
        assert pre.allowed


# ── Smoke: trace bundle flow ──────────────────────────────────────────────────

class TestTraceBundleSmoke:
    def test_trace_bundle_produced_for_success(self):
        env = _make_envelope()
        trace = TraceBundleV2.from_envelope(
            env,
            goal_id="goal-001",
            model_id="local_mock/llama3",
        )
        trace.finish(ExecutionOutcome.success)
        d = trace.as_dict()
        assert d["outcome"] == "success"
        assert d["execution_id"] != ""
        assert d["task_id"] == env.task_id

    def test_trace_bundle_produced_for_denial(self):
        env = _make_envelope()
        trace = TraceBundleV2.from_envelope(
            env,
            goal_id="goal-002",
            model_id="local_mock/llama3",
        )
        trace.finish(ExecutionOutcome.denial, reason_code="MISSING_CAPABILITY")
        d = trace.as_dict()
        assert d["outcome"] == "denial"
        assert d["reason_code"] == "MISSING_CAPABILITY"

    def test_trace_bundle_no_raw_content(self):
        env = _make_envelope()
        trace = TraceBundleV2.from_envelope(env, goal_id="g1", model_id="m1")
        trace.finish(ExecutionOutcome.success)
        d = trace.as_dict()
        d_str = str(d)
        # Should never contain raw prompt/response
        assert "prompt" not in d_str or "raw_prompt" not in d_str


# ── Smoke: tool registry integration ─────────────────────────────────────────

class TestToolRegistrySmoke:
    def test_default_registry_has_tools(self):
        registry = build_default_registry()
        catalog = registry.capability_catalog()
        assert len(catalog) > 0

    def test_tool_invocation_respects_resource_limits(self):
        registry = build_default_registry()
        entry = registry.get("read_file")
        if entry is None:
            pytest.skip("read_file not in default registry")
        assert entry.resource_limits.timeout_seconds > 0
        assert entry.resource_limits.max_output_chars > 0

    def test_unknown_tool_returns_none(self):
        registry = build_default_registry()
        assert registry.get("nonexistent_tool_xyz") is None


# ── Smoke: provider registry integration ─────────────────────────────────────

class TestProviderRegistrySmoke:
    def test_default_registry_has_local_providers(self):
        registry = build_default_provider_registry()
        local = registry.local_providers_by_priority()
        assert len(local) > 0

    def test_diagnostics_safe(self):
        registry = build_default_provider_registry()
        info = registry.provider_info()
        for entry in info:
            assert "api_key" not in str(entry).lower()
            assert "secret" not in str(entry).lower()
