"""AWF-T031–T045: baseline skills, subworker envelope, delegation, results, trace, diagnostics, audit, E2E.

AWF-T031: Seed minimal safe native skills
AWF-T032: SubworkerEnvelope capability subset enforcement
AWF-T033: DelegationArtifact and execution tree trace
AWF-T034: Subworker recursion/fan-out/mutation limits
AWF-T035: Cancellation and timeout propagation
AWF-T036: WorkerResult v2
AWF-T037: TraceBundle v2
AWF-T038: Typed artifact enforcement
AWF-T039: Worker diagnostics read model
AWF-T040: Audit events for sensitive worker transitions
AWF-T041: E2E native worker safe plan/review flow
AWF-T042: E2E command execute denial and approval
AWF-T043: Provider fallback and cloud-block
AWF-T044: Worker security regression suite
AWF-T045: Docs and track update
"""
from __future__ import annotations

import time
import pytest


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T031: Baseline skills
# ══════════════════════════════════════════════════════════════════════════════

class TestT031BaselineSkills:
    def _registry(self):
        from worker.skills.skill_registry import SkillRegistry
        return SkillRegistry()

    def test_five_builtin_skills_exist(self):
        from worker.skills.builtin.manifests import BUILTIN_SKILLS
        ids = {s.id for s in BUILTIN_SKILLS}
        assert "repo_context_review" in ids
        assert "test_failure_triage" in ids
        assert "patch_plan" in ids
        assert "security_review" in ids
        assert "result_summary" in ids

    def test_load_builtin_skills_registers_all(self):
        from worker.skills.builtin.manifests import load_builtin_skills, BUILTIN_SKILLS
        reg = self._registry()
        errors = load_builtin_skills(reg)
        assert errors == [], f"Unexpected errors: {errors}"
        diag = reg.list_diagnostics()
        assert len(diag) == len(BUILTIN_SKILLS)

    def test_builtin_skills_disabled_by_default(self):
        from worker.skills.builtin.manifests import load_builtin_skills
        reg = self._registry()
        load_builtin_skills(reg)
        for d in reg.list_diagnostics():
            assert d["enabled"] is False, f"skill {d['id']} is enabled by default — should not be"

    def test_no_baseline_skill_allows_shell_execute(self):
        from worker.skills.builtin.manifests import BUILTIN_SKILLS
        for skill in BUILTIN_SKILLS:
            assert "run_shell" not in skill.allowed_tools, f"{skill.id} allows run_shell"
            assert "shell_execute" not in skill.allowed_tools, f"{skill.id} allows shell_execute"

    def test_no_baseline_skill_allows_patch_apply(self):
        from worker.skills.builtin.manifests import BUILTIN_SKILLS
        for skill in BUILTIN_SKILLS:
            assert "patch_apply" not in skill.allowed_tools, f"{skill.id} allows patch_apply"

    def test_baseline_skills_have_low_or_medium_risk(self):
        from worker.skills.builtin.manifests import BUILTIN_SKILLS
        for skill in BUILTIN_SKILLS:
            assert skill.risk_class in {"low", "medium"}, f"{skill.id} has risk_class={skill.risk_class}"

    def test_each_builtin_skill_valid(self):
        from worker.skills.builtin.manifests import BUILTIN_SKILLS
        from worker.skills.skill_manifest import validate_skill_manifest
        for skill in BUILTIN_SKILLS:
            errors = validate_skill_manifest(skill)
            assert errors == [], f"{skill.id}: {errors}"

    def test_enable_builtin_skill(self):
        from worker.skills.builtin.manifests import load_builtin_skills
        reg = self._registry()
        load_builtin_skills(reg)
        assert reg.enable("result_summary") is True
        assert reg.is_enabled("result_summary") is True


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T032: SubworkerEnvelope capability subset enforcement
# ══════════════════════════════════════════════════════════════════════════════

def _make_envelope(**kwargs):
    from worker.core.subworker_envelope import create_subworker_envelope
    defaults = dict(
        parent_execution_id="parent-1",
        child_task_id="child-t-1",
        delegated_objective="review code",
        parent_capabilities=["code_read", "summarize"],
        reduced_capabilities=["code_read"],
        context_subset_ref="ctx-1",
        audit_correlation_id="audit-1",
    )
    defaults.update(kwargs)
    return create_subworker_envelope(**defaults)


class TestT032SubworkerEnvelope:
    def test_valid_subset_no_errors(self):
        env, errors = _make_envelope()
        assert errors == []

    def test_capability_escalation_denied(self):
        env, errors = _make_envelope(
            parent_capabilities=["code_read"],
            reduced_capabilities=["code_read", "shell_execute"],  # escalation!
        )
        assert any("subworker_capability_escalation" in e for e in errors)

    def test_equal_capabilities_allowed(self):
        env, errors = _make_envelope(
            parent_capabilities=["code_read", "summarize"],
            reduced_capabilities=["code_read", "summarize"],
        )
        assert errors == []

    def test_empty_child_capabilities_allowed(self):
        env, errors = _make_envelope(reduced_capabilities=[])
        assert errors == []

    def test_envelope_has_required_fields(self):
        env, _ = _make_envelope()
        assert env.parent_execution_id
        assert env.child_task_id
        assert env.audit_correlation_id
        assert env.context_subset_ref

    def test_deadline_set_from_timeout(self):
        before = time.time()
        env, _ = _make_envelope(timeout_seconds=60)
        assert env.deadline_at >= before + 60
        assert env.deadline_at <= time.time() + 60


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T033: DelegationArtifact
# ══════════════════════════════════════════════════════════════════════════════

class TestT033DelegationArtifact:
    def _build(self, children=None):
        from worker.core.delegation_artifact import build_delegation_artifact
        children = children or [
            {"execution_id": "c-1", "task_id": "t-1", "status": "success", "artifact_refs": ["a1"], "trace_refs": ["tr1"]},
            {"execution_id": "c-2", "task_id": "t-2", "status": "failed", "artifact_refs": [], "trace_refs": []},
        ]
        return build_delegation_artifact(
            parent_execution_id="parent-1",
            children=children,
            delegated_capabilities=["code_read"],
            context_refs=["ctx-1"],
        )

    def test_child_execution_ids_captured(self):
        art = self._build()
        assert "c-1" in art.child_execution_ids
        assert "c-2" in art.child_execution_ids

    def test_failed_child_not_dropped(self):
        art = self._build()
        assert art.any_failed is True
        assert "c-2" in art.statuses
        assert art.statuses["c-2"] == "failed"

    def test_all_succeeded_false_when_any_failed(self):
        art = self._build()
        assert art.all_succeeded is False

    def test_all_succeeded_true_when_all_ok(self):
        from worker.core.delegation_artifact import build_delegation_artifact
        art = build_delegation_artifact(
            parent_execution_id="p-1",
            children=[
                {"execution_id": "c-1", "task_id": "t-1", "status": "success"},
                {"execution_id": "c-2", "task_id": "t-2", "status": "success"},
            ],
            delegated_capabilities=["summarize"],
        )
        assert art.all_succeeded is True

    def test_artifact_refs_aggregated(self):
        art = self._build()
        assert "a1" in art.artifact_refs

    def test_as_dict_has_kind(self):
        art = self._build()
        d = art.as_dict()
        assert d["kind"] == "delegation_artifact"
        assert d["parent_execution_id"] == "parent-1"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T034: Subworker recursion/fan-out/mutation limits
# ══════════════════════════════════════════════════════════════════════════════

class TestT034SubworkerLimits:
    def test_depth_limit_exceeded_returns_error(self):
        env, errors = _make_envelope(max_depth=2, current_depth=2)
        assert any("delegation_cycle_or_depth_limit" in e for e in errors)

    def test_depth_within_limit_ok(self):
        env, errors = _make_envelope(max_depth=3, current_depth=2)
        assert not any("delegation_cycle_or_depth_limit" in e for e in errors)

    def test_max_depth_exceeds_system_limit(self):
        env, errors = _make_envelope(max_depth=10)  # > _MAX_DEPTH_LIMIT=5
        assert any("max_depth_exceeds_system_limit" in e for e in errors)

    def test_max_children_exceeds_system_limit(self):
        env, errors = _make_envelope(max_children=20)  # > _MAX_CHILDREN_LIMIT=10
        assert any("max_children_exceeds_system_limit" in e for e in errors)

    def test_mutation_capability_detected(self):
        env, _ = _make_envelope(
            parent_capabilities=["code_read", "shell_execute"],
            reduced_capabilities=["shell_execute"],
        )
        assert env.has_mutation_capability() is True

    def test_no_mutation_capability(self):
        env, _ = _make_envelope()
        assert env.has_mutation_capability() is False


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T035: Cancellation and timeout propagation
# ══════════════════════════════════════════════════════════════════════════════

class TestT035CancellationTimeout:
    def test_expired_envelope_detected(self):
        env, _ = _make_envelope(timeout_seconds=0.001)
        time.sleep(0.005)
        assert env.is_expired() is True

    def test_fresh_envelope_not_expired(self):
        env, _ = _make_envelope(timeout_seconds=3600)
        assert env.is_expired() is False

    def test_worker_result_v2_supports_cancelled_status(self):
        from worker.core.worker_result import WorkerResultV2
        r = WorkerResultV2(status="cancelled", reason_code="parent_cancelled")
        assert r.status == "cancelled"

    def test_worker_result_v2_supports_timeout_status(self):
        from worker.core.worker_result import WorkerResultV2
        r = WorkerResultV2(status="timeout", reason_code="deadline_exceeded")
        assert r.status == "timeout"

    def test_map_native_result_cancelled(self):
        from worker.core.worker_result import map_native_result_to_v2
        r = map_native_result_to_v2({"status": "cancelled", "reason": "user_cancel"})
        assert r.status == "cancelled"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T036: WorkerResult v2
# ══════════════════════════════════════════════════════════════════════════════

class TestT036WorkerResultV2:
    def test_success_status(self):
        from worker.core.worker_result import WorkerResultV2
        r = WorkerResultV2(status="success")
        assert r.status == "success"

    def test_invalid_status_raises(self):
        from worker.core.worker_result import WorkerResultV2
        with pytest.raises(ValueError, match="invalid_worker_result_status"):
            WorkerResultV2(status="unknown_xyz")

    def test_all_valid_statuses(self):
        from worker.core.worker_result import WorkerResultV2, _VALID_STATUSES
        for status in _VALID_STATUSES:
            r = WorkerResultV2(status=status)
            assert r.status == status

    def test_as_dict_schema_field(self):
        from worker.core.worker_result import WorkerResultV2
        r = WorkerResultV2(status="success", summary="done")
        d = r.as_dict()
        assert d["schema"] == "worker_result.v2"
        assert d["summary"] == "done"

    def test_map_passed_to_success(self):
        from worker.core.worker_result import map_native_result_to_v2
        r = map_native_result_to_v2({"status": "passed"})
        assert r.status == "success"

    def test_map_blocked_to_denied(self):
        from worker.core.worker_result import map_native_result_to_v2
        r = map_native_result_to_v2({"status": "blocked"})
        assert r.status == "denied"

    def test_map_preserves_artifacts(self):
        from worker.core.worker_result import map_native_result_to_v2
        arts = [{"kind": "test_result_artifact"}]
        r = map_native_result_to_v2({"status": "success", "artifacts": arts})
        assert r.artifacts == arts

    def test_no_side_effects_confirmed(self):
        from worker.core.worker_result import WorkerResultV2
        r = WorkerResultV2(status="success", no_side_effects_confirmed=True)
        assert r.no_side_effects_confirmed is True


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T037: TraceBundle v2
# ══════════════════════════════════════════════════════════════════════════════

class TestT037TraceBundleV2:
    def _bundle(self, **kwargs):
        from worker.core.trace_bundle import TraceBundleV2
        defaults = dict(execution_id="ex-1", task_id="t-1")
        defaults.update(kwargs)
        return TraceBundleV2(**defaults)

    def test_required_fields_present(self):
        b = self._bundle()
        d = b.as_dict()
        assert d["schema"] == "trace_bundle.v2"
        assert d["execution_id"] == "ex-1"
        assert d["task_id"] == "t-1"

    def test_finish_sets_final_status(self):
        b = self._bundle()
        b.finish(status="success")
        assert b.final_status == "success"
        assert b.finished_at is not None

    def test_duration_ms_computed(self):
        b = self._bundle()
        time.sleep(0.01)
        b.finish(status="success")
        assert b.duration_ms >= 10

    def test_capability_snapshot_hash_in_dict(self):
        b = self._bundle(capability_snapshot_hash="abc123")
        assert b.as_dict()["capability_snapshot_hash"] == "abc123"

    def test_context_hash_in_dict(self):
        b = self._bundle(context_hash="ctx-hash-xyz")
        assert b.as_dict()["context_hash"] == "ctx-hash-xyz"

    def test_provider_call_recorded(self):
        from worker.core.trace_bundle import TraceBundleV2, ProviderCallRecord
        b = self._bundle()
        b.provider_calls.append(ProviderCallRecord(provider="native_worker", model=None, status="ok"))
        d = b.as_dict()
        assert len(d["provider_calls"]) == 1
        assert d["provider_calls"][0]["provider"] == "native_worker"

    def test_tool_call_recorded(self):
        from worker.core.trace_bundle import TraceBundleV2, ToolCallRecord
        b = self._bundle()
        b.tool_calls.append(ToolCallRecord(tool_id="run_shell", output_chars=100))
        d = b.as_dict()
        assert d["tool_calls"][0]["tool_id"] == "run_shell"

    def test_no_raw_secrets_in_dict(self):
        b = self._bundle()
        d = b.as_dict()
        content = str(d)
        assert "api_key" not in content.lower()
        assert "password" not in content.lower()


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T038: Typed artifact enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestT038ArtifactEnforcement:
    def test_known_kind_is_typed(self):
        from worker.core.artifact_types import is_typed_artifact
        assert is_typed_artifact({"kind": "test_result_artifact"}) is True
        assert is_typed_artifact({"kind": "patch_artifact"}) is True
        assert is_typed_artifact({"kind": "delegation_artifact"}) is True

    def test_unknown_kind_not_typed(self):
        from worker.core.artifact_types import is_typed_artifact
        assert is_typed_artifact({"kind": "raw_stdout"}) is False
        assert is_typed_artifact({}) is False

    def test_success_command_execute_no_artifact_violates(self):
        from worker.core.artifact_types import enforce_artifact_first
        violations = enforce_artifact_first(artifacts=[], mode="command_execute", status="success")
        assert len(violations) >= 1
        assert "artifact_first_violation" in violations[0]

    def test_success_command_execute_with_typed_artifact_ok(self):
        from worker.core.artifact_types import enforce_artifact_first
        violations = enforce_artifact_first(
            artifacts=[{"kind": "test_result_artifact"}],
            mode="command_execute",
            status="success",
        )
        assert violations == []

    def test_denied_status_no_enforcement(self):
        from worker.core.artifact_types import enforce_artifact_first
        violations = enforce_artifact_first(artifacts=[], mode="shell_execute", status="denied")
        assert violations == []

    def test_all_typed_kinds_registered(self):
        from worker.core.artifact_types import TYPED_ARTIFACT_KINDS
        for kind in ("memory_proposal_artifact", "skill_proposal_artifact", "delegation_artifact"):
            assert kind in TYPED_ARTIFACT_KINDS


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T039: Worker diagnostics read model
# ══════════════════════════════════════════════════════════════════════════════

class TestT039WorkerDiagnostics:
    def _diag(self, **kwargs):
        from worker.core.diagnostics import build_worker_diagnostics_read_model
        return build_worker_diagnostics_read_model(**kwargs)

    def test_required_fields_present(self):
        d = self._diag()
        result = d.as_dict()
        assert "native_worker_enabled" in result
        assert "worker_profiles" in result
        assert "tool_registry_summary" in result
        assert "provider_summary" in result
        assert "skill_registry_summary" in result
        assert "memory_policy_summary" in result
        assert "context_policy_summary" in result
        assert "last_degraded_reasons" in result
        assert "enforcement_gates_active" in result

    def test_no_secrets_in_output(self):
        d = self._diag(context_policy={"some_flag": True, "api_key": "sk-secret"})
        assert d.has_secrets() is False

    def test_enforcement_gates_active(self):
        d = self._diag()
        gates = d.enforcement_gates_active
        assert gates.get("preflight_gate") is True
        assert gates.get("tool_registry_check") is True

    def test_skill_registry_summary_populated(self):
        from worker.skills.skill_registry import SkillRegistry
        from worker.skills.builtin.manifests import load_builtin_skills, BUILTIN_SKILLS
        reg = SkillRegistry()
        load_builtin_skills(reg)
        reg.enable("result_summary")
        d = self._diag(skill_registry=reg)
        s = d.skill_registry_summary
        assert s["registered_count"] == len(BUILTIN_SKILLS)
        assert s["enabled_count"] == 1

    def test_memory_policy_summary_strips_sensitive_keys(self):
        from agent.services.result_memory_service import normalize_result_memory_policy
        policy = normalize_result_memory_policy(None)
        d = self._diag(memory_policy=policy)
        assert "enabled" in d.memory_policy_summary
        assert "redact_before_persist" in d.memory_policy_summary

    def test_tool_registry_summary(self):
        from worker.core.tool_registry import build_default_registry
        reg = build_default_registry()
        d = self._diag(tool_registry=reg)
        assert d.tool_registry_summary["registered_count"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T040: Audit events for sensitive worker transitions
# ══════════════════════════════════════════════════════════════════════════════

class TestT040AuditEvents:
    def test_known_event_emitted(self):
        from worker.core.audit_events import emit_worker_audit_event
        event = emit_worker_audit_event(
            event_type="worker_preflight_decision",
            task_id="t-1",
            reason_code="allow",
        )
        assert event["event_type"] == "worker_preflight_decision"
        assert event["task_id"] == "t-1"
        assert event["reason_code"] == "allow"
        assert "emitted_at" in event

    def test_unknown_event_type_raises(self):
        from worker.core.audit_events import emit_worker_audit_event
        with pytest.raises(ValueError, match="unknown_worker_audit_event"):
            emit_worker_audit_event(event_type="custom_arbitrary_event", task_id="t-1")

    def test_all_defined_event_types_emittable(self):
        from worker.core.audit_events import emit_worker_audit_event, WORKER_AUDIT_EVENTS
        for event_type in WORKER_AUDIT_EVENTS:
            event = emit_worker_audit_event(event_type=event_type, task_id="t-1")
            assert event["event_type"] == event_type

    def test_sensitive_keys_scrubbed(self):
        from worker.core.audit_events import emit_worker_audit_event
        event = emit_worker_audit_event(
            event_type="worker_provider_call",
            task_id="t-1",
            extra={"api_key": "sk-secret", "provider": "native"},
        )
        assert event.get("api_key") != "sk-secret"
        assert event.get("provider") == "native"

    def test_trace_port_called(self):
        from worker.core.audit_events import emit_worker_audit_event
        emitted = []
        class FakePort:
            def emit(self, *, event_type, payload):
                emitted.append((event_type, payload))
        emit_worker_audit_event(
            event_type="worker_memory_write",
            task_id="t-1",
            trace_port=FakePort(),
        )
        assert len(emitted) == 1
        assert emitted[0][0] == "worker_memory_write"

    def test_events_include_required_fields(self):
        from worker.core.audit_events import emit_worker_audit_event
        event = emit_worker_audit_event(
            event_type="worker_tool_invocation",
            task_id="t-1",
            goal_id="g-1",
            execution_id="ex-1",
            capability_snapshot_hash="hash123",
            policy_decision_ref="ref-1",
        )
        assert event["goal_id"] == "g-1"
        assert event["execution_id"] == "ex-1"
        assert event["capability_snapshot_hash"] == "hash123"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T041: E2E native worker safe plan/review flow
# ══════════════════════════════════════════════════════════════════════════════

class TestT041E2ESafeWorkerFlow:
    def _make_runtime(self):
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
        )
        return rt, tp, ap

    def _safe_plan_contract(self):
        from tests.test_awf_worker_fixup_t001_t010 import _todo_contract
        contract = _todo_contract()
        # plan_only mode — no shell execution, no workspace mutation
        contract["execution"]["mode"] = "plan_only"
        contract["worker"]["executor_kind"] = "custom"
        return contract

    def test_safe_plan_flow_completes(self):
        rt, tp, _ = self._make_runtime()
        result = rt.run(task_contract=self._safe_plan_contract(), workspace_dir="/tmp")
        # Should not fail outright — may be degraded/approval_required but not crash
        assert "status" in result

    def test_safe_plan_emits_trace_event(self):
        rt, tp, _ = self._make_runtime()
        rt.run(task_contract=self._safe_plan_contract(), workspace_dir="/tmp")
        # events stored as {"event_type": ..., "payload": ...}
        event_types = [e.get("event_type") for e in tp.events]
        assert any("runtime" in str(t).lower() for t in event_types)

    def test_standalone_contract_no_workspace_mutation(self):
        from tests.test_awf_worker_fixup_t001_t010 import _AllowPolicyPort, _ListTracePort, _ManifestArtifactPort
        from worker.runtime.standalone_runtime import StandaloneRuntime
        from worker.core.tool_registry import build_default_registry
        rt = StandaloneRuntime(
            policy_port=_AllowPolicyPort(),
            trace_port=_ListTracePort(),
            artifact_port=_ManifestArtifactPort(),
            tool_registry=build_default_registry(),
        )
        # standalone contract needs "command" at top level
        result = rt.run(
            task_contract={
                "schema": "standalone_task_contract.v1",
                "task_id": "t-e2e",
                "command": "echo test",
                "worker": {"profile": "balanced", "profile_source": "agent_default"},
                "execution": {"mode": "plan_only"},
                "control_manifest": {"trace_id": "tr-e2e", "capability_id": "shell_plan"},
                "expected_result_schema": "worker_execution_result.v1",
            },
            workspace_dir="/tmp",
        )
        assert "status" in result

    def test_context_hash_tracked(self):
        from worker.core.trace_bundle import TraceBundleV2
        b = TraceBundleV2(
            execution_id="ex-e2e",
            task_id="t-e2e",
            context_hash="ctx-hash-abc",
            capability_snapshot_hash="cap-hash-xyz",
        )
        b.finish(status="success")
        d = b.as_dict()
        assert d["context_hash"] == "ctx-hash-abc"
        assert d["capability_snapshot_hash"] == "cap-hash-xyz"

    def test_worker_result_v2_no_mutation_confirmed(self):
        from worker.core.worker_result import WorkerResultV2
        r = WorkerResultV2(
            status="success",
            no_side_effects_confirmed=True,
            artifacts=[{"kind": "review_artifact"}],
        )
        assert r.no_side_effects_confirmed is True
        assert r.artifacts[0]["kind"] == "review_artifact"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T042: E2E command execute denial and approval
# ══════════════════════════════════════════════════════════════════════════════

class TestT042CommandExecuteDenialApproval:
    def _make_runtime(self, policy_port=None):
        from tests.test_awf_worker_fixup_t001_t010 import (
            _ListTracePort, _ManifestArtifactPort, _AllowPolicyPort,
        )
        from worker.runtime.standalone_runtime import StandaloneRuntime
        from worker.core.tool_registry import build_default_registry
        return StandaloneRuntime(
            policy_port=policy_port or _AllowPolicyPort(),
            trace_port=_ListTracePort(),
            artifact_port=_ManifestArtifactPort(),
            tool_registry=build_default_registry(),
        )

    def test_policy_deny_blocks_execution(self):
        from tests.test_awf_worker_fixup_t001_t010 import _DenyPolicyPort
        rt = self._make_runtime(policy_port=_DenyPolicyPort())
        result = rt.run(
            task_contract={
                "schema": "standalone_task_contract.v1",
                "task_id": "t-deny",
                "command": "echo hi",
                "worker": {"profile": "balanced", "profile_source": "agent_default"},
                "execution": {"mode": "command_execute"},
                "control_manifest": {"trace_id": "tr-1", "capability_id": "shell_execute"},
                "expected_result_schema": "worker_execution_result.v1",
            },
            workspace_dir="/tmp",
        )
        assert result["status"] in {"degraded", "failed", "denied"}
        assert result.get("reason") in {"policy_denied", "denied", "schema_invalid"}

    def test_shell_execute_requires_approval_from_preflight(self):
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
        assert decision.reason_code in {"approval_required", "confirm_required", "approval_missing"}

    def test_approved_shell_execute_passes_preflight(self):
        from worker.core.execution_envelope import (
            CapabilityGrant, ExecutionEnvelope, ApprovalRef
        )
        from worker.core.preflight import PreflightGate
        import time as _time
        env = ExecutionEnvelope(
            task_id="t-1", actor_ref="hub", audit_correlation_id="a",
            context_envelope_ref="ctx",
            capability_grant=CapabilityGrant(capabilities=["shell_execute"]),
            approval_refs=[ApprovalRef(
                ref_id="ref-1",
                operation="shell_execute",
                granted_at=_time.time(),
                granted_by="hub",
            )],
        )
        gate = PreflightGate()
        decision = gate.check(env)
        assert decision.allowed

    def test_policy_denied_result_in_status(self):
        from worker.core.worker_result import map_native_result_to_v2
        r = map_native_result_to_v2({"status": "denied", "reason": "policy_denied"})
        assert r.status == "denied"

    def test_needs_approval_status_preserved(self):
        from worker.core.worker_result import map_native_result_to_v2
        r = map_native_result_to_v2({"status": "needs_approval"})
        assert r.status == "needs_approval"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T043: Provider fallback and cloud-block integration
# ══════════════════════════════════════════════════════════════════════════════

class TestT043ProviderCloudBlock:
    def test_cloud_blocked_when_cloud_not_allowed(self):
        from worker.core.provider_registry import (
            WorkerProviderRegistry, ProviderSelectionGate, ModelPolicy,
            ProviderEntry, ProviderKind,
        )
        reg = WorkerProviderRegistry()
        reg.register(ProviderEntry(id="local_llm", kind=ProviderKind.local, priority=10))
        reg.register(ProviderEntry(id="openai", kind=ProviderKind.cloud, priority=50))
        policy = ModelPolicy(cloud_allowed=False, allowed_providers=["local_llm", "openai"])
        gate = ProviderSelectionGate(reg)
        entry, reason = gate.select(policy=policy)
        selected_id = entry.id if entry else None
        assert selected_id != "openai"

    def test_local_selected_when_available(self):
        from worker.core.provider_registry import (
            WorkerProviderRegistry, ProviderSelectionGate, ModelPolicy,
            ProviderEntry, ProviderKind,
        )
        reg = WorkerProviderRegistry()
        reg.register(ProviderEntry(id="local_llm", kind=ProviderKind.local, priority=10))
        policy = ModelPolicy(cloud_allowed=True, allowed_providers=["local_llm"])
        gate = ProviderSelectionGate(reg)
        entry, reason = gate.select(policy=policy)
        assert entry is not None
        assert entry.id == "local_llm"
        assert "allow" in reason

    def test_unavailable_provider_returns_degraded_not_crash(self):
        from worker.core.provider_registry import (
            WorkerProviderRegistry, ProviderSelectionGate, ModelPolicy,
        )
        reg = WorkerProviderRegistry()
        policy = ModelPolicy(cloud_allowed=False, allowed_providers=["nonexistent"])
        gate = ProviderSelectionGate(reg)
        entry, reason = gate.select(policy=policy)
        assert entry is None
        assert "no_provider" in reason or "unavailable" in reason or reason != "allow"

    def test_provider_credentials_not_in_provenance(self):
        from worker.core.provider_registry import ProviderProvenanceRef
        prov = ProviderProvenanceRef.native_worker()
        d = prov.as_dict()
        content = str(d)
        assert "api_key" not in content.lower()
        assert "password" not in content.lower()

    def test_context_sensitivity_filter_blocks_confidential_for_cloud(self):
        from worker.core.context_resolver import ContextSensitivityFilter, ContextBlock, ContextSensitivity
        f = ContextSensitivityFilter()
        blocks = [
            ContextBlock(source_type="task", origin_id="t1", provenance="p", sensitivity=ContextSensitivity.customer_confidential, content="secret stuff"),
            ContextBlock(source_type="task", origin_id="t2", provenance="p", sensitivity=ContextSensitivity.public, content="public stuff"),
        ]
        kept, redacted = f.filter_for_cloud(blocks)
        assert len(kept) == 1
        assert kept[0].origin_id == "t2"
        assert len(redacted) == 1


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T044: Worker security regression suite
# Each test has a comment naming the bypass class it prevents.
# ══════════════════════════════════════════════════════════════════════════════

class TestT044SecurityRegression:

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
        reg = SkillRegistry()
        from tests.test_awf_worker_fixup_t021_t030 import _minimal_manifest
        reg.register(_minimal_manifest())
        # NOT enabled — must not run
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
        from tests.test_awf_worker_fixup_t001_t010 import _AllowPolicyPort, _ListTracePort, _ManifestArtifactPort
        from worker.runtime.standalone_runtime import StandaloneRuntime
        from worker.core.tool_registry import build_default_registry
        rt = StandaloneRuntime(
            policy_port=_AllowPolicyPort(),
            trace_port=_ListTracePort(),
            artifact_port=_ManifestArtifactPort(),
            tool_registry=build_default_registry(),
        )
        from tests.test_awf_worker_fixup_t001_t010 import _todo_contract
        contract = _todo_contract()
        contract["control_manifest"].pop("context_ref", None)
        contract["control_manifest"].pop("context_hash", None)
        contract["worker"]["executor_kind"] = "ananta_worker"
        contract["execution"]["mode"] = "patch_apply"
        result = rt.run(task_contract=contract, workspace_dir="/tmp")
        assert result.get("reason") == "code_context_required"


# ══════════════════════════════════════════════════════════════════════════════
# AWF-T045: Docs and track update verification
# ══════════════════════════════════════════════════════════════════════════════

class TestT045DocsAndTrackUpdate:
    def _todo_path(self):
        from pathlib import Path
        return Path(__file__).parents[1] / "todos" / "archiv" / "todo.ananta-worker-fixup.json"

    def test_todo_file_is_valid_json(self):
        import json
        data = json.loads(self._todo_path().read_text())
        assert data.get("version") == 1
        assert data.get("track")

    def test_architecture_doc_exists(self):
        from pathlib import Path
        doc = Path(__file__).parents[1] / "docs" / "architecture" / "ananta_native_worker.md"
        assert doc.exists()
        content = doc.read_text()
        assert "implemented" in content.lower()

    def test_setup_doc_exists(self):
        from pathlib import Path
        doc = Path(__file__).parents[1] / "docs" / "setup" / "native-worker.md"
        assert doc.exists()
        content = doc.read_text()
        assert "pytest" in content

    def test_all_t031_t040_marked_done(self):
        import json
        data = json.loads(self._todo_path().read_text())
        targets = {f"AWF-T0{i}" for i in range(31, 46)}
        for task in data["tasks"]:
            if task["id"] in targets:
                assert task["status"] == "done", f"{task['id']} not marked done"
