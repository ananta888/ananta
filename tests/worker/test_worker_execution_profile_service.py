from __future__ import annotations

from agent.services.worker_execution_profile_service import (
    normalize_worker_execution_profile,
    resolve_worker_execution_profile,
)


def test_normalize_worker_execution_profile_defaults_to_balanced() -> None:
    assert normalize_worker_execution_profile(None) == "balanced"
    assert normalize_worker_execution_profile("invalid") == "balanced"


def test_resolve_worker_execution_profile_prefers_task_context_over_agent_default() -> None:
    profile, source = resolve_worker_execution_profile(
        worker_execution_context={"worker_profile": "fast", "profile_source": "task_override"},
        agent_cfg={"worker_runtime": {"default_execution_profile": "safe"}},
    )
    assert profile == "fast"
    assert source == "task_override"
