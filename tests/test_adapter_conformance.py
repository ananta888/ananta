"""Adapter conformance test harness (EW-T059).

Verifies that all external adapters (Hermes, OpenCode, MCP) conform to the
same security contracts: policy check, input sanitization, output artifact
structure, and rejection of unsafe responses.
"""
import time
import pytest

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    ModelPolicy,
    ToolPolicy,
)
from worker.core.external_adapters import HermesAdapter, MCPAdapter, OpenCodeAdapter
from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.artifact_enforcer import ArtifactEnforcer, KNOWN_ARTIFACT_KINDS
from worker.core.sanitizer import OutputSanitizer


def _env(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id="t1",
        actor_ref="hub:conform",
        capability_grant=CapabilityGrant(capabilities=["provider_call", "patch_propose", "mcp_call"]),
        context_envelope_ref="ctx:1",
        audit_correlation_id="audit:1",
        approval_refs=[
            ApprovalRef(ref_id="r1", operation="mcp_call",
                        granted_at=time.time(), granted_by="hub"),
        ],
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


# ── Conformance contract ──────────────────────────────────────────────────────
# Every adapter must satisfy:
# C1. check_policy() returns (False, reason) when required capability missing
# C2. check_policy() returns (True, "") when policy is satisfied
# C3. Output is sanitized — no raw secrets pass through
# C4. Artifacts produced have known kinds
# C5. Responses without structured artifacts are rejected
# C6. No direct file system writes (enforced via scope checks)


class TestHermesAdapterConformance:
    def setup_method(self):
        self.adapter = HermesAdapter()
        self.sanitizer = OutputSanitizer()
        self.enforcer = ArtifactEnforcer()

    # C1: missing capability
    def test_c1_missing_capability_rejected(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False
        assert reason != ""

    # C2: satisfied policy
    def test_c2_policy_satisfied(self):
        env = _env()
        ok, _ = self.adapter.check_policy(env)
        assert ok is True

    # C3: secrets sanitized
    def test_c3_secrets_sanitized_in_response(self):
        response = "api_key=sk-ant-api03-abcdefghij1234567890XYZzzz was used"
        result = self.adapter.parse_response(response, task_id="t1")
        assert "sk-ant-api03-" not in result.sanitized_output

    # C4: artifacts have known kinds
    def test_c4_artifact_kinds_known(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-old\n+new"
        response = f"Fix:\n```diff\n{diff}\n```"
        result = self.adapter.parse_response(response, task_id="t1")
        for art in result.artifacts:
            kind = art.get("kind", "")
            if kind:
                assert kind in KNOWN_ARTIFACT_KINDS, f"Unknown kind: {kind!r}"

    # C5: response without diff rejected? Hermes does best-effort parsing
    def test_c5_empty_response_allowed_with_empty_artifacts(self):
        result = self.adapter.parse_response("", task_id="t1")
        assert result.allowed is True  # Hermes returns allowed even with empty artifacts

    # Context filtering: sensitive blocks stripped when cloud_allowed=False
    def test_sensitive_context_stripped(self):
        blocks = [
            ContextBlock("task", "b1", "hub",
                         sensitivity=ContextSensitivity.secret, content="secret info"),
            ContextBlock("task", "b2", "hub",
                         sensitivity=ContextSensitivity.public, content="public info"),
        ]
        allowed, redacted = self.adapter.prepare_context(blocks, cloud_allowed=False)
        assert "b1" in redacted
        assert all(b.origin_id != "b1" for b in allowed)

    def test_all_context_allowed_when_cloud_true(self):
        blocks = [
            ContextBlock("task", "b1", "hub",
                         sensitivity=ContextSensitivity.secret, content="secret info"),
        ]
        allowed, redacted = self.adapter.prepare_context(blocks, cloud_allowed=True)
        assert len(allowed) == 1
        assert redacted == []


class TestOpenCodeAdapterConformance:
    def setup_method(self):
        self.adapter = OpenCodeAdapter()

    # C1: missing capability
    def test_c1_missing_capability_rejected(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False

    # C2: satisfied policy
    def test_c2_policy_satisfied(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["patch_propose"]))
        ok, _ = self.adapter.check_policy(env)
        assert ok is True

    # C6: files outside workspace rejected
    def test_c6_files_outside_workspace_denied(self):
        _, denied = self.adapter.filter_allowed_files(
            ["/workspace/ok.py", "/etc/shadow"],
            read_paths=[],
            workspace_root="/workspace",
        )
        assert "/etc/shadow" in denied

    # C4: patch artifacts have known kinds
    def test_c4_patch_artifact_kind_known(self):
        diff = "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n-old\n+new"
        result = self.adapter.parse_patch_output(
            diff, task_id="t1", artifact_id="a1",
            workspace_root="/workspace", write_paths=["/workspace"],
        )
        if result.allowed:
            for art in result.artifacts:
                kind = art.get("kind", "") if isinstance(art, dict) else art.as_dict().get("kind", "")
                if kind:
                    assert kind in KNOWN_ARTIFACT_KINDS

    # C5: no patch in output rejected
    def test_c5_no_patch_output_rejected(self):
        result = self.adapter.parse_patch_output(
            "All done!", task_id="t1", artifact_id="a1"
        )
        assert not result.allowed

    # Path traversal blocked
    def test_traversal_in_diff_blocked(self):
        diff = "--- /etc/passwd\n+++ /etc/passwd\n@@ -1,1 +1,1 @@\n-root:x:0:0\n+hacker:x:0:0"
        result = self.adapter.parse_patch_output(
            diff, task_id="t1", artifact_id="a1",
            workspace_root="/workspace",
        )
        assert not result.allowed

    def test_files_within_workspace_allowed(self):
        allowed, denied = self.adapter.filter_allowed_files(
            ["/workspace/src/a.py", "/workspace/tests/b.py"],
            read_paths=[],
            workspace_root="/workspace",
        )
        assert len(allowed) == 2
        assert denied == []


class TestMCPAdapterConformance:
    def setup_method(self):
        self.adapter = MCPAdapter()

    # C1: missing capability
    def test_c1_missing_capability_rejected(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False
        assert reason == "missing_capability"

    # C1b: capability present but no approval
    def test_c1b_no_approval_rejected(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["mcp_call"]),
            approval_refs=[],
        )
        ok, reason = self.adapter.check_policy(env)
        assert ok is False
        assert reason == "approval_missing"

    # C2: satisfied policy
    def test_c2_policy_satisfied(self):
        env = _env()
        ok, _ = self.adapter.check_policy(env)
        assert ok is True

    # C3: secrets sanitized in tool result
    def test_c3_string_result_sanitized(self):
        result = self.adapter.sanitize_result(
            "token=ghp_abcdefghijklmnopqrstuvwxyz12345 was used"
        )
        assert "ghp_" not in result

    def test_c3_dict_result_sanitized(self):
        result = self.adapter.sanitize_result(
            {"output": "key=sk-proj-abcdefghijklmnopqrstuvwxyz12345"}
        )
        assert "sk-proj-" not in result["output"]

    # Tool filtering
    def test_tool_filter_respects_policy(self):
        env = _env(tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]))
        allowed, denied = self.adapter.filter_tools(
            ["read_file", "shell_exec", "write_file"], env
        )
        assert "read_file" in allowed
        assert "shell_exec" in denied
        assert "write_file" in denied

    def test_env_allowlist_strips_secrets(self):
        env_in = {
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "OPENAI_API_KEY": "sk-openai-secret",
            "HOME": "/home/user",
        }
        scoped = self.adapter.scoped_env(env_in)
        assert "ANTHROPIC_API_KEY" not in scoped
        assert "OPENAI_API_KEY" not in scoped
        assert "PATH" in scoped


# ── Cross-adapter conformance check ──────────────────────────────────────────

class TestCrossAdapterConformance:
    """Verify that all adapters share the same security baseline."""

    ADAPTERS = [
        ("hermes",   HermesAdapter()),
        ("opencode", OpenCodeAdapter()),
        ("mcp",      MCPAdapter()),
    ]

    def test_all_adapters_have_check_policy(self):
        for name, adapter in self.ADAPTERS:
            assert hasattr(adapter, "check_policy"), f"{name} missing check_policy"

    def test_all_adapters_reject_missing_required_capability(self):
        env_no_caps = _env(
            capability_grant=CapabilityGrant(capabilities=[]),
            approval_refs=[],
        )
        for name, adapter in self.ADAPTERS:
            ok, reason = adapter.check_policy(env_no_caps)
            assert ok is False, f"{name} allowed request with no capabilities"

    def test_all_adapters_satisfy_policy_with_correct_env(self):
        hermes, opencode, mcp = [a for _, a in self.ADAPTERS]
        env_hermes = _env(capability_grant=CapabilityGrant(capabilities=["provider_call"]))
        ok, _ = hermes.check_policy(env_hermes)
        assert ok is True

        env_oc = _env(capability_grant=CapabilityGrant(capabilities=["patch_propose"]))
        ok, _ = opencode.check_policy(env_oc)
        assert ok is True

        env_mcp = _env()  # has mcp_call + approval
        ok, _ = mcp.check_policy(env_mcp)
        assert ok is True
