"""Tests for worker_api.py (T043,T044,T048) and external_adapters.py (T045,T046,T047)."""
import time
import pytest
from pydantic import ValidationError

from worker.core.execution_envelope import (
    ApprovalRef, CapabilityGrant, ExecutionEnvelope, ModelPolicy, ToolPolicy
)
from worker.core.worker_api import (
    ApiExposureMode,
    ApiExposurePolicy,
    ChatCompletionRequest,
    ChatMessage,
    OpenAIChatFacade,
    WorkerRPCEndpoint,
    WorkerRPCMode,
    WorkerRPCRequest,
)
from worker.core.external_adapters import (
    HermesAdapter,
    MCPAdapter,
    OpenCodeAdapter,
)
from worker.core.context_resolver import ContextBlock, ContextSensitivity


def _env(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id="t1", actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=["planning", "provider_call",
                                                        "patch_propose", "mcp_call"]),
        context_envelope_ref="ctx:1", audit_correlation_id="audit:1",
        approval_refs=[ApprovalRef(
            ref_id="r1", operation="mcp_call",
            granted_at=time.time(), granted_by="admin",
        )],
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


# ── EW-T048: ApiExposurePolicy ────────────────────────────────────────────────

class TestApiExposurePolicy:
    def test_disabled_by_default(self):
        policy = ApiExposurePolicy()
        assert policy.is_enabled() is False

    def test_disabled_rejects_all(self):
        policy = ApiExposurePolicy(mode=ApiExposureMode.disabled)
        allowed, reason = policy.check_request()
        assert allowed is False and reason == "api_exposure_disabled"

    def test_local_only_allows_requests(self):
        policy = ApiExposurePolicy(mode=ApiExposureMode.local_only, instance_id="inst-1")
        allowed, reason = policy.check_request()
        assert allowed is True

    def test_self_loop_blocked(self):
        policy = ApiExposurePolicy(mode=ApiExposureMode.local_only, instance_id="inst-1")
        allowed, reason = policy.check_request(caller_instance_id="inst-1")
        assert allowed is False and reason == "self_loop_detected"

    def test_hop_count_exceeded_blocked(self):
        policy = ApiExposurePolicy(mode=ApiExposureMode.local_only, max_hops=3)
        allowed, reason = policy.check_request(hop_count=3)
        assert allowed is False and reason == "max_hops_exceeded"

    def test_hop_count_within_limit_allowed(self):
        policy = ApiExposurePolicy(mode=ApiExposureMode.local_only, max_hops=5)
        allowed, _ = policy.check_request(hop_count=2)
        assert allowed is True


# ── EW-T043: OpenAIChatFacade ─────────────────────────────────────────────────

class TestOpenAIChatFacade:
    def setup_method(self):
        self.policy = ApiExposurePolicy(mode=ApiExposureMode.local_only)
        self.facade = OpenAIChatFacade(self.policy)

    def test_disabled_api_rejects(self):
        facade = OpenAIChatFacade(ApiExposurePolicy(mode=ApiExposureMode.disabled))
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="fix the bug")],
            capability_token="tok:abc",
        )
        result = facade.handle(req)
        assert result.allowed is False

    def test_missing_token_rejected(self):
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="fix the bug")],
            capability_token="",
        )
        result = self.facade.handle(req)
        assert result.allowed is False and result.reason_code == "missing_capability_token"

    def test_mappable_request_accepted(self):
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="please fix the bug in main.py")],
            capability_token="tok:valid",
        )
        result = self.facade.handle(req)
        assert result.allowed is True
        assert result.envelope_task_id != ""

    def test_unmappable_request_rejected(self):
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="what is the capital of France?")],
            capability_token="tok:valid",
        )
        result = self.facade.handle(req)
        assert result.allowed is False and result.reason_code == "unmappable_chat_request"

    def test_self_loop_blocked(self):
        policy = ApiExposurePolicy(mode=ApiExposureMode.local_only, instance_id="inst-1")
        facade = OpenAIChatFacade(policy)
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="fix the bug")],
            capability_token="tok:valid",
        )
        result = facade.handle(req, caller_instance_id="inst-1")
        assert result.allowed is False and result.reason_code == "self_loop_detected"

    def test_empty_messages_rejected(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(messages=[], capability_token="tok")


# ── EW-T044: WorkerRPCEndpoint ────────────────────────────────────────────────

class TestWorkerRPCEndpoint:
    def setup_method(self):
        self.policy = ApiExposurePolicy(mode=ApiExposureMode.local_only)
        self.endpoint = WorkerRPCEndpoint(self.policy)

    def test_valid_envelope_accepted(self):
        req = WorkerRPCRequest(envelope={
            "task_id": "t1",
            "actor_ref": "hub",
            "capability_grant": {"capabilities": ["planning"]},
            "context_envelope_ref": "ctx:1",
            "audit_correlation_id": "audit:1",
        })
        result = self.endpoint.handle(req)
        assert result.accepted is True

    def test_disabled_api_rejected(self):
        endpoint = WorkerRPCEndpoint(ApiExposurePolicy(mode=ApiExposureMode.disabled))
        req = WorkerRPCRequest(envelope={"task_id": "t1", "actor_ref": "hub",
            "capability_grant": {"capabilities": []},
            "context_envelope_ref": "ctx", "audit_correlation_id": "a"})
        result = endpoint.handle(req)
        assert result.accepted is False

    def test_async_job_returns_job_id(self):
        req = WorkerRPCRequest(
            envelope={"task_id": "t2", "actor_ref": "hub",
                      "capability_grant": {"capabilities": []},
                      "context_envelope_ref": "ctx", "audit_correlation_id": "a"},
            mode=WorkerRPCMode.async_job,
        )
        result = self.endpoint.handle(req)
        assert result.accepted is True
        assert result.job_id != ""

    def test_empty_envelope_rejected(self):
        with pytest.raises(ValidationError):
            WorkerRPCRequest(envelope={})

    def test_envelope_missing_task_id_rejected(self):
        with pytest.raises(ValidationError):
            WorkerRPCRequest(envelope={"actor_ref": "hub"})

    def test_job_status_retrievable(self):
        req = WorkerRPCRequest(
            envelope={"task_id": "t3", "actor_ref": "hub",
                      "capability_grant": {"capabilities": []},
                      "context_envelope_ref": "ctx", "audit_correlation_id": "a"},
            mode=WorkerRPCMode.async_job,
        )
        result = self.endpoint.handle(req)
        status = self.endpoint.job_status(result.job_id)
        assert status is not None and status["task_id"] == "t3"


# ── EW-T045: HermesAdapter ────────────────────────────────────────────────────

class TestHermesAdapter:
    def setup_method(self):
        self.adapter = HermesAdapter()

    def test_sensitive_context_blocked_when_cloud_false(self):
        blocks = [
            ContextBlock("task", "t1", "hub",
                         sensitivity=ContextSensitivity.customer_confidential, content="secret data"),
            ContextBlock("task", "t2", "hub",
                         sensitivity=ContextSensitivity.public, content="public data"),
        ]
        allowed, redacted = self.adapter.prepare_context(blocks, cloud_allowed=False)
        assert len(allowed) == 1 and allowed[0].origin_id == "t2"
        assert "t1" in redacted

    def test_all_context_allowed_when_cloud_true(self):
        blocks = [
            ContextBlock("task", "t1", "hub",
                         sensitivity=ContextSensitivity.secret, content="secret"),
        ]
        allowed, redacted = self.adapter.prepare_context(blocks, cloud_allowed=True)
        assert len(allowed) == 1 and redacted == []

    def test_response_parsed_into_artifacts(self):
        response = "Here is a fix:\n```diff\n--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-old\n+new\n```"
        result = self.adapter.parse_response(response, task_id="t1")
        assert result.allowed is True
        assert len(result.artifacts) >= 1

    def test_response_secrets_sanitized(self):
        response = "Use key=sk-proj-abcdefghij1234567890XYZ to authenticate"
        result = self.adapter.parse_response(response, task_id="t1")
        assert "sk-proj-" not in result.sanitized_output

    def test_policy_check_requires_provider_call(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False and reason == "missing_capability"

    def test_policy_check_with_provider_call(self):
        env = _env()
        ok, _ = self.adapter.check_policy(env)
        assert ok is True


# ── EW-T046: OpenCodeAdapter ──────────────────────────────────────────────────

class TestOpenCodeAdapter:
    def setup_method(self):
        self.adapter = OpenCodeAdapter()

    def test_allowed_files_within_workspace(self):
        allowed, denied = self.adapter.filter_allowed_files(
            ["/workspace/src/main.py", "/etc/passwd"],
            read_paths=[], workspace_root="/workspace",
        )
        assert "/workspace/src/main.py" in allowed
        assert "/etc/passwd" in denied

    def test_patch_output_parsed(self):
        diff = "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n-old\n+new"
        result = self.adapter.parse_patch_output(
            diff, task_id="t1", artifact_id="a1", workspace_root="/workspace",
            write_paths=["/workspace"],
        )
        assert result.allowed is True
        assert len(result.artifacts) >= 1

    def test_patch_outside_workspace_blocked(self):
        # Absolute path in diff (no b/ prefix) → not treated as workspace-relative
        diff = "--- /etc/passwd\n+++ /etc/passwd\n@@ -1,1 +1,1 @@\n-root\n+hacker"
        result = self.adapter.parse_patch_output(
            diff, task_id="t1", artifact_id="a1", workspace_root="/workspace",
        )
        assert result.allowed is False

    def test_no_patch_in_output_rejected(self):
        result = self.adapter.parse_patch_output(
            "Task done!", task_id="t1", artifact_id="a1",
        )
        assert result.allowed is False

    def test_policy_check_requires_patch_propose(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False


# ── EW-T047: MCPAdapter ───────────────────────────────────────────────────────

class TestMCPAdapter:
    def setup_method(self):
        self.adapter = MCPAdapter()

    def test_filter_tools_by_policy(self):
        env = _env(tool_policy=ToolPolicy(allowed_tool_ids=["read_file", "memory_read"]))
        allowed, denied = self.adapter.filter_tools(
            ["read_file", "shell_exec", "memory_read"], env
        )
        assert "read_file" in allowed
        assert "memory_read" in allowed
        assert "shell_exec" in denied

    def test_empty_tool_allowlist_allows_all(self):
        env = _env(tool_policy=ToolPolicy())
        allowed, denied = self.adapter.filter_tools(["any_tool"], env)
        assert "any_tool" in allowed and denied == []

    def test_scoped_env_only_allows_listed_keys(self):
        env_in = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-secret", "HOME": "/home/user"}
        scoped = self.adapter.scoped_env(env_in)
        assert "PATH" in scoped
        assert "OPENAI_API_KEY" not in scoped

    def test_extra_allowed_keys_included(self):
        env_in = {"MY_SAFE_VAR": "value", "SECRET": "hidden"}
        scoped = self.adapter.scoped_env(env_in, extra_allowed_keys={"MY_SAFE_VAR"})
        assert "MY_SAFE_VAR" in scoped
        assert "SECRET" not in scoped

    def test_mcp_result_sanitized(self):
        result = self.adapter.sanitize_result(
            "token=sk-ant-api03-abcdefghij1234567890XYZ was used"
        )
        assert "sk-ant-" not in result

    def test_mcp_dict_result_sanitized(self):
        result = self.adapter.sanitize_result({"output": "key=sk-proj-abcdefghij1234567890XYZ"})
        assert "sk-proj-" not in result["output"]

    def test_policy_requires_mcp_call_capability(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False and reason == "missing_capability"

    def test_policy_requires_approval(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["mcp_call"]),
            approval_refs=[],
        )
        ok, reason = self.adapter.check_policy(env)
        assert ok is False and reason == "approval_missing"
