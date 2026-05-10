"""Tests for diagnostics.py (EW-T052, EW-T053)."""
import time
import pytest
from worker.core.diagnostics import (
    AuditEmitter,
    AuditEvent,
    AUDITABLE_EVENTS,
    WorkerDiagnostics,
    WorkerDiagnosticsBuilder,
    _redact_payload,
)


# ── EW-T052: WorkerDiagnostics ────────────────────────────────────────────────

class TestWorkerDiagnostics:
    def test_as_dict_contains_required_fields(self):
        d = WorkerDiagnostics(
            worker_id="w1", version="1.0.0", runtime_mode="local"
        ).as_dict()
        for key in ("worker_id", "version", "runtime_mode", "registered_tools",
                    "registered_providers", "enabled_skills", "active_capabilities",
                    "policy_summary", "generated_at"):
            assert key in d, f"missing key {key!r}"

    def test_tools_sorted_in_dict(self):
        d = WorkerDiagnostics(
            worker_id="w1", version="1.0", runtime_mode="local",
            registered_tools=["ztool", "atool", "mtool"],
        ).as_dict()
        assert d["registered_tools"] == ["atool", "mtool", "ztool"]

    def test_skills_sorted_in_dict(self):
        d = WorkerDiagnostics(
            worker_id="w1", version="1.0", runtime_mode="local",
            enabled_skills=["z_skill", "a_skill"],
        ).as_dict()
        assert d["enabled_skills"] == ["a_skill", "z_skill"]

    def test_no_secrets_in_dict(self):
        d = WorkerDiagnostics(
            worker_id="w1", version="1.0", runtime_mode="local"
        ).as_dict()
        for key in d:
            assert "secret" not in key.lower()
            assert "api_key" not in key.lower()
            assert "credential" not in key.lower()

    def test_generated_at_recent(self):
        before = time.time()
        d = WorkerDiagnostics(worker_id="w1", version="1.0", runtime_mode="ci").as_dict()
        after = time.time()
        assert before <= d["generated_at"] <= after

    def test_runtime_modes_allowed(self):
        for mode in ("local", "headless", "development", "ci"):
            diag = WorkerDiagnostics(worker_id="w1", version="1.0", runtime_mode=mode)
            assert diag.as_dict()["runtime_mode"] == mode


class TestWorkerDiagnosticsBuilder:
    def _builder(self):
        return WorkerDiagnosticsBuilder()

    def test_build_minimal(self):
        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local"
        )
        assert diag.worker_id == "w1"
        assert diag.registered_tools == []
        assert diag.enabled_skills == []

    def test_build_with_tool_registry(self):
        class FakeToolRegistry:
            def capability_catalog(self):
                return [{"id": "read_file"}, {"id": "write_file"}]

        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local",
            tool_registry=FakeToolRegistry(),
        )
        assert set(diag.registered_tools) == {"read_file", "write_file"}

    def test_build_with_failing_tool_registry(self):
        class BrokenRegistry:
            def capability_catalog(self):
                raise RuntimeError("broken")

        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local",
            tool_registry=BrokenRegistry(),
        )
        assert diag.registered_tools == []

    def test_build_with_skill_registry(self):
        class FakeManifest:
            id = "bugfix_plan"

        class FakeEntry:
            manifest = FakeManifest()

        class FakeSkillRegistry:
            def enabled_skills(self):
                return [FakeEntry()]

        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local",
            skill_registry=FakeSkillRegistry(),
        )
        assert "bugfix_plan" in diag.enabled_skills

    def test_build_with_envelope(self):
        from worker.core.execution_envelope import (
            CapabilityGrant, ExecutionEnvelope, ModelPolicy, ToolPolicy
        )
        env = ExecutionEnvelope(
            task_id="t1",
            actor_ref="hub",
            capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
            context_envelope_ref="ctx:1",
            audit_correlation_id="audit:1",
            model_policy=ModelPolicy(cloud_allowed=False, allowed_providers=["ollama"]),
            tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]),
        )
        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local",
            envelope=env,
        )
        assert set(diag.active_capabilities) == {"planning", "code_read"}
        assert diag.policy_summary["cloud_allowed"] is False
        assert "ollama" in diag.policy_summary["allowed_providers"]
        assert diag.policy_summary["tool_count"] == 1

    def test_build_with_failing_envelope(self):
        class BrokenEnvelope:
            @property
            def capability_grant(self):
                raise AttributeError("broken")

        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local",
            envelope=BrokenEnvelope(),
        )
        assert diag.active_capabilities == []
        assert diag.policy_summary == {}

    def test_build_with_provider_registry(self):
        class FakeProviderRegistry:
            def provider_info(self):
                return [{"provider_id": "ollama", "kind": "local"}]

        diag = self._builder().build(
            worker_id="w1", version="0.1", runtime_mode="local",
            provider_registry=FakeProviderRegistry(),
        )
        assert len(diag.registered_providers) == 1


# ── EW-T053: AuditEmitter ─────────────────────────────────────────────────────

class TestAuditEmitter:
    def setup_method(self):
        self.emitter = AuditEmitter()

    def test_emit_known_event_stored(self):
        event = self.emitter.emit(
            "preflight_allow",
            correlation_id="c1",
            reason_code=None,
            task_id="t1",
        )
        assert event.event_type == "preflight_allow"
        assert len(self.emitter.peek()) == 1

    def test_emit_unknown_event_flagged(self):
        event = self.emitter.emit(
            "totally_made_up_event",
            correlation_id="c1",
            reason_code="none",
            task_id="t1",
        )
        assert event.payload.get("_unknown_event") is True
        assert len(self.emitter.peek()) == 1

    def test_emit_preflight_allow(self):
        event = self.emitter.emit_preflight(
            "allow",
            correlation_id="c1",
            reason_code=None,
            task_id="t1",
        )
        assert event.event_type == "preflight_allow"
        assert event.payload["decision"] == "allow"

    def test_emit_preflight_denied(self):
        event = self.emitter.emit_preflight(
            "deny",
            correlation_id="c1",
            reason_code="MISSING_CAPABILITY",
            task_id="t1",
        )
        assert event.event_type == "preflight_denied"

    def test_emit_approval_required(self):
        event = self.emitter.emit_approval(
            "required",
            correlation_id="c1",
            operation="shell_execute",
            task_id="t1",
        )
        assert event.event_type == "approval_required"
        assert event.reason_code == "approval_missing"

    def test_emit_approval_consumed(self):
        event = self.emitter.emit_approval(
            "consumed",
            correlation_id="c1",
            operation="shell_execute",
            task_id="t1",
            ref_id="ref-001",
        )
        assert event.event_type == "approval_consumed"
        assert event.reason_code is None
        assert event.payload["ref_id"] == "ref-001"

    def test_flush_returns_events_and_clears(self):
        self.emitter.emit("preflight_allow", correlation_id="c1", reason_code=None, task_id="t1")
        self.emitter.emit("policy_denied", correlation_id="c2", reason_code="denied", task_id="t2")
        events = self.emitter.flush()
        assert len(events) == 2
        assert self.emitter.peek() == []

    def test_flush_returns_safe_dicts(self):
        self.emitter.emit(
            "provider_call",
            correlation_id="c1",
            reason_code=None,
            task_id="t1",
            api_key="sk-secret-key",
        )
        events = self.emitter.flush()
        assert events[0]["payload"]["api_key"] == "[REDACTED]"

    def test_peek_non_destructive(self):
        self.emitter.emit("preflight_allow", correlation_id="c1", reason_code=None, task_id="t1")
        first = self.emitter.peek()
        second = self.emitter.peek()
        assert len(first) == len(second) == 1

    def test_all_auditable_events_accepted(self):
        for event_type in AUDITABLE_EVENTS:
            e = self.emitter.emit(
                event_type, correlation_id="c", reason_code=None, task_id="t"
            )
            assert "_unknown_event" not in e.payload

    def test_correlation_id_always_present(self):
        event = self.emitter.emit(
            "shell_execute", correlation_id="corr-123", reason_code=None, task_id="t1"
        )
        d = event.as_dict()
        assert d["correlation_id"] == "corr-123"

    def test_event_timestamp_recent(self):
        before = time.time()
        event = self.emitter.emit("memory_write", correlation_id="c", reason_code=None, task_id="t")
        after = time.time()
        assert before <= event.ts <= after

    def test_multiple_emits_accumulate(self):
        for i in range(5):
            self.emitter.emit(
                "preflight_allow", correlation_id=f"c{i}", reason_code=None, task_id=f"t{i}"
            )
        assert len(self.emitter.peek()) == 5

    def test_actor_ref_stored_and_serialized(self):
        event = self.emitter.emit(
            "patch_apply",
            correlation_id="c1",
            reason_code=None,
            task_id="t1",
            actor_ref="hub:test",
        )
        assert event.as_dict()["actor_ref"] == "hub:test"


class TestRedactPayload:
    def test_api_key_redacted(self):
        result = _redact_payload({"api_key": "sk-secret"})
        assert result["api_key"] == "[REDACTED]"

    def test_token_redacted(self):
        result = _redact_payload({"token": "abc123"})
        assert result["token"] == "[REDACTED]"

    def test_password_redacted(self):
        result = _redact_payload({"password": "hunter2"})
        assert result["password"] == "[REDACTED]"

    def test_safe_keys_preserved(self):
        result = _redact_payload({"tool_id": "read_file", "task_id": "t1"})
        assert result["tool_id"] == "read_file"
        assert result["task_id"] == "t1"

    def test_mixed_payload(self):
        result = _redact_payload({"tool_id": "x", "secret": "oops", "status": "ok"})
        assert result["tool_id"] == "x"
        assert result["secret"] == "[REDACTED]"
        assert result["status"] == "ok"

    def test_case_insensitive_redaction(self):
        result = _redact_payload({"API_KEY": "val", "Token": "val2"})
        assert result["API_KEY"] == "[REDACTED]"
        assert result["Token"] == "[REDACTED]"
