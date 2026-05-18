from scripts.goal_latency_diagnostics import (
    _summarize_llm_call_profile,
    _classify_idle_reason_from_history,
    _build_reason_breakdown,
    _REASON_CATEGORIES,
    PhaseDurations,
)


def test_summarize_llm_call_profile_separates_real_and_synthetic():
    summary = _summarize_llm_call_profile(
        [
            {
                "model": "qwen",
                "source": "model_invocation_service",
                "estimated": False,
                "success": True,
                "latency_ms": 1000,
                "prompt_tokens": 100,
                "completion_tokens": 20,
            },
            {
                "model": "qwen",
                "source": "orchestrator_synthetic",
                "estimated": True,
                "success": True,
                "latency_ms": None,
                "prompt_tokens": None,
                "completion_tokens": None,
            },
        ]
    )

    assert summary["calls_seen_total"] == 2
    assert summary["calls_seen_real"] == 1
    assert summary["calls_seen_synthetic"] == 1
    assert summary["latency_ms_mean_real"] == 1000
    assert summary["prompt_tokens_mean_real"] == 100
    assert summary["completion_tokens_mean_real"] == 20


def test_summarize_llm_call_profile_backward_compatible_missing_fields():
    summary = _summarize_llm_call_profile(
        [
            {
                "model": "legacy",
                "success": True,
                "latency_ms": 123,
                "prompt_tokens": 11,
                "completion_tokens": 5,
            }
        ]
    )
    assert summary["calls_seen_total"] == 1
    assert summary["calls_seen_real"] == 1
    assert summary["calls_seen_synthetic"] == 0
    assert summary["latency_ms_mean_real"] == 123


# ARD-004: reason breakdown
class TestClassifyIdleReason:
    def test_llm_wait_when_strategy_attempt_seen(self):
        history = [{"event_type": "autopilot_strategy_attempt"}]
        reason = _classify_idle_reason_from_history(history, phase_seconds=5.0)
        assert reason == "llm_wait"

    def test_provider_unavailable_when_circuit_open(self):
        history = [{"event_type": "circuit_open"}]
        reason = _classify_idle_reason_from_history(history, phase_seconds=30.0)
        assert reason == "provider_unavailable"

    def test_dependency_blocked_when_task_blocked(self):
        history = [{"event_type": "blocked_by_dependency"}]
        reason = _classify_idle_reason_from_history(history, phase_seconds=10.0)
        assert reason == "dependency_blocked"

    def test_worker_unavailable_when_event_seen(self):
        history = [{"event_type": "no_workers_available"}]
        reason = _classify_idle_reason_from_history(history, phase_seconds=5.0)
        assert reason == "worker_unavailable"

    def test_unknown_when_no_matching_event(self):
        history = [{"event_type": "task_created"}]
        reason = _classify_idle_reason_from_history(history, phase_seconds=3.0)
        assert reason == "unknown"

    def test_unknown_when_phase_is_none(self):
        reason = _classify_idle_reason_from_history([], phase_seconds=None)
        assert reason == "unknown"


class TestBuildReasonBreakdown:
    def test_all_categories_present_even_when_empty(self):
        breakdown = _build_reason_breakdown([], [])
        for cat in _REASON_CATEGORIES:
            assert cat in breakdown, f"category '{cat}' must always be present"

    def test_unknown_not_silently_omitted(self):
        phases = [PhaseDurations(queued_to_assigned=5.0, assigned_to_propose_done=None,
                                 propose_to_execute_done=None, execute_to_terminal=None, total=None)]
        histories = [[{"event_type": "task_created"}]]  # no classifiable event
        breakdown = _build_reason_breakdown(phases, histories)
        assert "unknown" in breakdown
        assert breakdown["unknown"]["task_count"] == 1

    def test_reason_time_accumulates(self):
        phases = [
            PhaseDurations(queued_to_assigned=10.0, assigned_to_propose_done=None,
                           propose_to_execute_done=None, execute_to_terminal=None, total=None),
            PhaseDurations(queued_to_assigned=5.0, assigned_to_propose_done=None,
                           propose_to_execute_done=None, execute_to_terminal=None, total=None),
        ]
        histories = [
            [{"event_type": "autopilot_strategy_attempt"}],
            [{"event_type": "autopilot_strategy_attempt"}],
        ]
        breakdown = _build_reason_breakdown(phases, histories)
        assert breakdown["llm_wait"]["total_seconds"] == 15.0
        assert breakdown["llm_wait"]["task_count"] == 2
