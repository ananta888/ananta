from scripts.goal_latency_diagnostics import _summarize_llm_call_profile


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
