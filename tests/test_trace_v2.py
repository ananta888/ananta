"""Tests for worker/core/trace_v2.py (EW-T049, EW-T050)."""
import time
import pytest
from pydantic import ValidationError

from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.trace_v2 import (
    ExecutionOutcome,
    ModelCallTrace,
    TraceBundleV2,
    TraceEventV2,
)


def _env() -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id="t1", actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=["planning"]),
        context_envelope_ref="ctx:1", audit_correlation_id="audit:corr-001",
    )


# ── EW-T049: TraceBundleV2 ────────────────────────────────────────────────────

class TestTraceBundleV2:
    def setup_method(self):
        self.trace = TraceBundleV2(
            execution_id="exec-1", task_id="t1", goal_id="g1",
            actor_ref="hub:test", capability_hash="abc123",
            context_hash="ctx-hash", model_id="ollama/llama3",
            outcome=ExecutionOutcome.success,
        )

    def test_from_envelope(self):
        env = _env()
        trace = TraceBundleV2.from_envelope(env, model_id="ollama/llama3")
        assert trace.execution_id == "audit:corr-001"
        assert trace.task_id == "t1"
        assert trace.capability_hash == env.capability_grant.snapshot_hash

    def test_append_event(self):
        self.trace.append("preflight_allow", reason_code=None, capability="planning")
        assert len(self.trace.events) == 1
        assert self.trace.events[0].event_type == "preflight_allow"

    def test_finish_sets_outcome_and_timestamp(self):
        before = time.time()
        self.trace.finish(ExecutionOutcome.success)
        after = time.time()
        assert self.trace.outcome == ExecutionOutcome.success
        assert self.trace.finished_at is not None
        assert before <= self.trace.finished_at <= after

    def test_cancel_sets_cancelled_flag(self):
        self.trace.cancel()
        assert self.trace.cancelled is True
        assert self.trace.outcome == ExecutionOutcome.cancellation

    def test_timeout_sets_timed_out_flag(self):
        self.trace.timeout()
        assert self.trace.timed_out is True
        assert self.trace.outcome == ExecutionOutcome.timeout

    def test_produced_for_all_outcomes(self):
        for outcome in ExecutionOutcome:
            trace = TraceBundleV2(
                execution_id="x", task_id="t", goal_id=None,
                actor_ref="h", capability_hash="h1",
                context_hash="c1", model_id="m",
                outcome=outcome,
            )
            d = trace.as_dict()
            assert d["outcome"] == outcome.value

    def test_as_dict_no_secrets(self):
        d = self.trace.as_dict()
        d_str = str(d)
        assert "api_key" not in d_str.lower()
        assert "password" not in d_str.lower()

    def test_as_dict_has_required_fields(self):
        d = self.trace.as_dict()
        for field in ["execution_id", "task_id", "actor_ref", "capability_hash",
                      "outcome", "started_at", "events"]:
            assert field in d

    def test_record_model_call(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="ollama", model="llama3",
            local_or_cloud="local",
        )
        self.trace.record_model_call(call)
        d = self.trace.as_dict(include_model_calls=True)
        assert len(d["model_calls"]) == 1

    def test_skill_ids_tracked(self):
        self.trace.skill_ids_used.append("bugfix_plan")
        d = self.trace.as_dict()
        assert "bugfix_plan" in d["skill_ids_used"]

    def test_subworker_ids_tracked(self):
        self.trace.subworker_ids.append("child-t1")
        d = self.trace.as_dict()
        assert "child-t1" in d["subworker_ids"]


# ── EW-T050: ModelCallTrace ───────────────────────────────────────────────────

class TestModelCallTrace:
    def test_as_trace_event_no_raw_content(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="openai", model="gpt-4",
            local_or_cloud="cloud",
            prompt_token_estimate=500,
            completion_token_estimate=200,
            latency_ms=1200.0,
        )
        event = call.as_trace_event()
        assert "call_id" in event
        assert "prompt_token_estimate" in event
        assert "latency_ms" in event
        # Raw content must not appear
        assert "_raw_prompt" not in event
        assert "_raw_response" not in event

    def test_local_or_cloud_recorded(self):
        call_local = ModelCallTrace(
            call_id="c1", provider_id="ollama", model="llama3",
            local_or_cloud="local",
        )
        call_cloud = ModelCallTrace(
            call_id="c2", provider_id="openai", model="gpt-4",
            local_or_cloud="cloud",
        )
        assert call_local.as_trace_event()["local_or_cloud"] == "local"
        assert call_cloud.as_trace_event()["local_or_cloud"] == "cloud"

    def test_retry_count_recorded(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="ollama", model="m",
            local_or_cloud="local", retry_count=2,
        )
        assert call.as_trace_event()["retry_count"] == 2

    def test_fallback_recorded(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="lmstudio", model="m",
            local_or_cloud="local",
            fallback_used=True, fallback_provider="ollama",
        )
        event = call.as_trace_event()
        assert event["fallback_used"] is True
        assert event["fallback_provider"] == "ollama"

    def test_debug_event_requires_scope(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="ollama", model="m",
            local_or_cloud="local",
        )
        call._raw_prompt = "secret prompt sk-abc123"
        call._raw_response = "secret response"
        # No scope → same as safe trace event
        event = call.as_debug_event(scope="")
        assert "prompt_preview" not in event

    def test_debug_event_with_scope_redacts_secrets(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="ollama", model="m",
            local_or_cloud="local",
        )
        call._raw_prompt = "key=sk-proj-abcdefghij1234567890XYZ"
        event = call.as_debug_event(scope="debug:test")
        # Should be present but redacted
        assert "prompt_preview" in event
        assert "sk-proj-" not in event["prompt_preview"]

    def test_raw_prompt_logging_disabled_by_default(self):
        call = ModelCallTrace(
            call_id="c1", provider_id="ollama", model="m",
            local_or_cloud="local",
        )
        event = call.as_trace_event()
        # Raw prompt/response must never appear in standard trace event
        assert "raw_prompt" not in str(event)
        assert "raw_response" not in str(event)

    def test_all_outcomes_representable(self):
        for outcome in ["success", "timeout", "failure", "cancelled"]:
            call = ModelCallTrace(
                call_id="c", provider_id="ollama", model="m",
                local_or_cloud="local", outcome=outcome,
            )
            event = call.as_trace_event()
            assert event["outcome"] == outcome
