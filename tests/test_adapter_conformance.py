"""Adapter conformance test harness (EW-T059)."""

import time

import pytest

from worker.core.artifact_enforcer import ArtifactEnforcer, KNOWN_ARTIFACT_KINDS
from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    ToolPolicy,
)
from worker.core.external_adapters import HermesAdapter, MCPAdapter, OpenCodeAdapter
from worker.core.sanitizer import OutputSanitizer


def _env(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id="t1",
        actor_ref="hub:conform",
        capability_grant=CapabilityGrant(capabilities=["provider_call", "patch_propose", "mcp_call"]),
        context_envelope_ref="ctx:1",
        audit_correlation_id="audit:1",
        approval_refs=[
            ApprovalRef(ref_id="r1", operation="mcp_call", granted_at=time.time(), granted_by="hub"),
        ],
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


class TestHermesAdapterConformance:
    def setup_method(self):
        self.adapter = HermesAdapter()
        self.sanitizer = OutputSanitizer()
        self.enforcer = ArtifactEnforcer()

    def test_c1_missing_capability_rejected(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        ok, reason = self.adapter.check_policy(env)
        assert ok is False
        assert reason != ""

    def test_c2_policy_satisfied(self):
        ok, _ = self.adapter.check_policy(_env())
        assert ok is True

    def test_c3_secrets_sanitized_in_response(self):
        result = self.adapter.parse_response(
            "api_key=sk-ant-api03-abcdefghij1234567890XYZzzz was used", task_id="t1"
        )
        assert "sk-ant-api03-" not in result.sanitized_output

    def test_c4_artifact_kinds_known(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-old\n+new"
        result = self.adapter.parse_response(f"Fix:\n```diff\n{diff}\n```", task_id="t1")
        for artifact in result.artifacts:
            kind = artifact.get("kind", "")
            if kind:
                assert kind in KNOWN_ARTIFACT_KINDS

    def test_c5_empty_response_allowed_with_empty_artifacts(self):
        assert self.adapter.parse_response("", task_id="t1").allowed is True

    def test_sensitive_context_stripped(self):
        blocks = [
            ContextBlock("task", "b1", "hub", sensitivity=ContextSensitivity.secret, content="secret info"),
            ContextBlock("task", "b2", "hub", sensitivity=ContextSensitivity.public, content="public info"),
        ]
        allowed, redacted = self.adapter.prepare_context(blocks, cloud_allowed=False)
        assert "b1" in redacted
        assert all(block.origin_id != "b1" for block in allowed)

    def test_all_context_allowed_when_cloud_true(self):
        blocks = [
            ContextBlock("task", "b1", "hub", sensitivity=ContextSensitivity.secret, content="secret info"),
        ]
        allowed, redacted = self.adapter.prepare_context(blocks, cloud_allowed=True)
        assert len(allowed) == 1
        assert redacted == []


class TestOpenCodeAdapterConformance:
    def setup_method(self):
        self.adapter = OpenCodeAdapter()

    def test_c1_missing_capability_rejected(self):
        ok, _ = self.adapter.check_policy(_env(capability_grant=CapabilityGrant(capabilities=["planning"])))
        assert ok is False

    def test_c2_policy_satisfied(self):
        ok, _ = self.adapter.check_policy(_env(capability_grant=CapabilityGrant(capabilities=["patch_propose"])))
        assert ok is True

    def test_c6_files_outside_workspace_denied(self):
        _, denied = self.adapter.filter_allowed_files(
            ["/workspace/ok.py", "/etc/shadow"], read_paths=[], workspace_root="/workspace"
        )
        assert "/etc/shadow" in denied

    def test_c4_patch_artifact_kind_known(self):
        diff = "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n-old\n+new"
        result = self.adapter.parse_patch_output(
            diff, task_id="t1", artifact_id="a1", workspace_root="/workspace", write_paths=["/workspace"]
        )
        if result.allowed:
            for artifact in result.artifacts:
                kind = artifact.get("kind", "") if isinstance(artifact, dict) else artifact.as_dict().get("kind", "")
                if kind:
                    assert kind in KNOWN_ARTIFACT_KINDS

    def test_c5_no_patch_output_rejected(self):
        assert not self.adapter.parse_patch_output("All done!", task_id="t1", artifact_id="a1").allowed

    def test_traversal_in_diff_blocked(self):
        diff = "--- /etc/passwd\n+++ /etc/passwd\n@@ -1,1 +1,1 @@\n-root:x:0:0\n+hacker:x:0:0"
        assert not self.adapter.parse_patch_output(
            diff, task_id="t1", artifact_id="a1", workspace_root="/workspace"
        ).allowed

    def test_files_within_workspace_allowed(self):
        allowed, denied = self.adapter.filter_allowed_files(
            ["/workspace/src/a.py", "/workspace/tests/b.py"], read_paths=[], workspace_root="/workspace"
        )
        assert len(allowed) == 2
        assert denied == []


class TestMCPAdapterConformance:
    def setup_method(self):
        self.adapter = MCPAdapter()

    def test_c1_missing_capability_rejected(self):
        ok, reason = self.adapter.check_policy(_env(capability_grant=CapabilityGrant(capabilities=["planning"])))
        assert ok is False
        assert reason == "missing_capability"

    def test_c1b_no_approval_rejected(self):
        ok, reason = self.adapter.check_policy(
            _env(capability_grant=CapabilityGrant(capabilities=["mcp_call"]), approval_refs=[])
        )
        assert ok is False
        assert reason == "approval_missing"

    def test_c2_policy_satisfied(self):
        ok, _ = self.adapter.check_policy(_env())
        assert ok is True

    def test_c3_string_result_sanitized(self):
        assert "ghp_" not in self.adapter.sanitize_result("token=ghp_abcdefghijklmnopqrstuvwxyz12345 was used")

    def test_c3_dict_result_sanitized(self):
        result = self.adapter.sanitize_result({"output": "key=sk-proj-abcdefghijklmnopqrstuvwxyz12345"})
        assert "sk-proj-" not in result["output"]

    def test_tool_filter_respects_policy(self):
        allowed, denied = self.adapter.filter_tools(
            ["read_file", "shell_exec", "write_file"], _env(tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]))
        )
        assert "read_file" in allowed
        assert "shell_exec" in denied
        assert "write_file" in denied

    def test_env_allowlist_strips_secrets(self):
        scoped = self.adapter.scoped_env(
            {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-secret", "OPENAI_API_KEY": "sk-openai-secret", "HOME": "/home/user"}
        )
        assert "ANTHROPIC_API_KEY" not in scoped
        assert "OPENAI_API_KEY" not in scoped
        assert "PATH" in scoped


class TestCrossAdapterConformance:
    ADAPTERS = [("hermes", HermesAdapter()), ("opencode", OpenCodeAdapter()), ("mcp", MCPAdapter())]

    def test_all_adapters_have_check_policy(self):
        for name, adapter in self.ADAPTERS:
            assert hasattr(adapter, "check_policy"), f"{name} missing check_policy"

    def test_all_adapters_reject_missing_required_capability(self):
        env_no_caps = _env(capability_grant=CapabilityGrant(capabilities=[]), approval_refs=[])
        for name, adapter in self.ADAPTERS:
            ok, _ = adapter.check_policy(env_no_caps)
            assert ok is False, f"{name} allowed request with no capabilities"

    def test_all_adapters_satisfy_policy_with_correct_env(self):
        hermes, opencode, mcp = [adapter for _, adapter in self.ADAPTERS]
        assert hermes.check_policy(_env(capability_grant=CapabilityGrant(capabilities=["provider_call"])))[0] is True
        assert opencode.check_policy(_env(capability_grant=CapabilityGrant(capabilities=["patch_propose"])))[0] is True
        assert mcp.check_policy(_env())[0] is True


import inspect
from dataclasses import replace

from agent.services.scientific_skill_adapter_service import (
    ControlledExecutionSkillAdapter,
    DocumentationResearchSkillAdapter,
    ReadOnlyResearchSkillAdapter,
    ScientificSkillAdapterRequest,
    ScientificSkillAdapterService,
    ScientificSkillAdapterStatus,
)
from agent.services.scientific_skill_catalog_service import (
    ScientificSkillApprovalLevel,
    ScientificSkillCatalog,
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
    ScientificSkillNetworkProfile,
)
from agent.services.scientific_skill_risk_profile_service import ScientificSkillOperatingMode


class _Catalogs:
    def __init__(self, catalog: ScientificSkillCatalog | None) -> None:
        self.catalog = catalog

    def get(self, *, catalog_id: str, catalog_version: str) -> ScientificSkillCatalog | None:
        if self.catalog and (catalog_id, catalog_version) == (
            self.catalog.catalog_id,
            self.catalog.catalog_version,
        ):
            return self.catalog
        return None


class _ApprovalRequests:
    def __init__(self) -> None:
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return request


def _entry(mode: ScientificSkillOperatingMode) -> ScientificSkillCatalogEntry:
    return ScientificSkillCatalogEntry.create(
        skill_name="literature",
        upstream_path="skills/literature/SKILL.md",
        upstream_pin="0123456789abcdef0123456789abcdef01234567",
        skill_sha256="a" * 64,
        risk_profile_digest="b" * 64,
        status=ScientificSkillCatalogEntryStatus.APPROVED,
        allowed_mode=mode,
        context_budget_tokens=500,
        allowed_tools=("sandbox_task_request",)
        if mode is ScientificSkillOperatingMode.CONTROLLED_EXECUTION
        else (),
        data_classification="internal",
        network_profile=(
            ScientificSkillNetworkProfile.APPROVAL_REQUIRED
            if mode is ScientificSkillOperatingMode.CONTROLLED_EXECUTION
            else ScientificSkillNetworkProfile.DENIED
        ),
        allowed_network_targets=(),
        approval_level=(
            ScientificSkillApprovalLevel.TASK
            if mode is ScientificSkillOperatingMode.CONTROLLED_EXECUTION
            else ScientificSkillApprovalLevel.NONE
        ),
        approval_receipt_digest="c" * 64,
    )


def _catalog(entry: ScientificSkillCatalogEntry, *, enabled: bool = True) -> ScientificSkillCatalog:
    return ScientificSkillCatalog.create(
        catalog_id="scientific-default",
        catalog_version="v1",
        feature_enabled=enabled,
        entries=(entry,),
    )


def _request() -> ScientificSkillAdapterRequest:
    return ScientificSkillAdapterRequest(
        catalog_id="scientific-default", catalog_version="v1", skill_name="literature"
    )


def test_documentation_and_research_entries_project_bounded_source_context() -> None:
    for mode, adapter in (
        (ScientificSkillOperatingMode.DOCUMENTATION_ONLY, DocumentationResearchSkillAdapter()),
        (ScientificSkillOperatingMode.READ_ONLY_RESEARCH, ReadOnlyResearchSkillAdapter()),
    ):
        entry = _entry(mode)
        result = ScientificSkillAdapterService(_Catalogs(_catalog(entry)), (adapter,)).adapt(_request())
        assert result.status is ScientificSkillAdapterStatus.PROJECTED
        assert result.projection is not None
        assert result.projection.context_budget_tokens == 500
        assert result.projection.source_references == (
            "https://github.com/K-Dense-AI/scientific-agent-skills/blob/"
            "0123456789abcdef0123456789abcdef01234567/skills/literature/SKILL.md",
        )
        assert "Do not execute upstream files" in result.projection.instruction


def test_controlled_execution_only_submits_approval_required_native_task_request() -> None:
    requests = _ApprovalRequests()
    entry = _entry(ScientificSkillOperatingMode.CONTROLLED_EXECUTION)
    result = ScientificSkillAdapterService(
        _Catalogs(_catalog(entry)), (ControlledExecutionSkillAdapter(requests),)
    ).adapt(_request())
    assert result.status is ScientificSkillAdapterStatus.APPROVAL_REQUIRED
    assert result.approval_request is not None
    assert result.approval_request.execution_performed is False
    assert result.approval_request.status == "approval-required"
    assert requests.requests == [result.approval_request]


def test_disabled_catalog_missing_adapter_and_invalid_pin_degrade_explicitly() -> None:
    documentation = _entry(ScientificSkillOperatingMode.DOCUMENTATION_ONLY)
    disabled = ScientificSkillAdapterService(_Catalogs(_catalog(documentation, enabled=False)))
    assert disabled.adapt(_request()).degradation_code == "scientific_skill_adapter_feature_disabled"

    missing = ScientificSkillAdapterService(_Catalogs(_catalog(documentation)))
    assert missing.adapt(_request()).degradation_code == "scientific_skill_adapter_missing"

    invalid_entry = replace(documentation, upstream_pin="invalid pin")
    invalid = ScientificSkillAdapterService(
        _Catalogs(_catalog(invalid_entry)), (DocumentationResearchSkillAdapter(),)
    )
    assert invalid.adapt(_request()).degradation_code == "scientific_skill_adapter_pin_invalid"


def test_adapter_public_contracts_never_reference_upstream_manifest_types() -> None:
    import agent.services.scientific_skill_adapter_service as module

    exported = [getattr(module, name) for name in module.__all__]
    annotations = "\n".join(str(inspect.get_annotations(item, eval_str=False)) for item in exported if callable(item))
    source = inspect.getsource(module)
    assert "ScientificSkillManifest" not in annotations
    assert "scientific_skill_manifest_service import" not in source
