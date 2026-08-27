from __future__ import annotations

from agent.services.local_model_runtime_invocation_observer import (
    LocalRuntimeInvocationObserver,
)
from ananta_contracts.local_model_runtime import (
    LocalRuntimeResourceUsage,
    LocalRuntimeSnapshot,
    LocalRuntimeStatus,
    RuntimeHealth,
    RuntimeReadiness,
)


def _snapshot() -> LocalRuntimeSnapshot:
    runtimes = tuple(
        LocalRuntimeStatus(
            snapshot_revision=1,
            runtime_id=runtime_id,
            provider_id={"kat": "openai_compatible", "lfm": "llamacpp", "needle": "needle_sidecar"}[runtime_id],
            model_id={"kat": "kat-coder-v2.5-dev", "lfm": "lfm2.5-2.6b-agentic-q8_0", "needle": "needle-2-45m"}[
                runtime_id
            ],
            execution_device="cpu" if runtime_id == "needle" else "cuda",
            health=RuntimeHealth.HEALTHY,
            readiness=RuntimeReadiness.READY,
            reason_code="probe_ready",
            effective_context=256 if runtime_id == "needle" else 32768,
            context_capacity=256 if runtime_id == "needle" else 65536,
            resources=LocalRuntimeResourceUsage(),
            timeout_supported=True,
            cancellation_supported=runtime_id != "needle",
            candidate_only=runtime_id == "needle",
        )
        for runtime_id in ("kat", "lfm", "needle")
    )
    return LocalRuntimeSnapshot(
        revision=1,
        generated_at="2026-08-27T00:00:00Z",
        total_vram_bytes=10_000,
        free_vram_bytes=4_000,
        available_ram_bytes=20_000,
        reserve_vram_bytes=1_000,
        runtimes=runtimes,
    )


def test_observer_records_only_closed_content_free_operational_facts() -> None:
    audits: list[tuple[str, dict]] = []
    observer = LocalRuntimeInvocationObserver(
        snapshot=_snapshot,
        audit_sink=lambda action, facts: audits.append((action, dict(facts))),
        capacity=2,
    )

    item = observer.observe(
        profile_id="local_lfm25_agentic_fast",
        provider_id="llamacpp",
        model_id="lfm2.5-2.6b-agentic-q8_0",
        success=True,
        reason_code="invocation_completed",
        call_profile={
            "latency_ms": 17,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt": "must-not-be-copied",
        },
        fallback_index=1,
        context_capacity=131072,
        confidence_available=False,
        goal_id="goal-1",
        task_id="task-1",
    )

    assert item.runtime_id == "lfm"
    assert item.total_tokens == 15
    assert item.readiness is RuntimeReadiness.READY
    assert observer.read(limit=1) == (item,)
    assert audits[0][0] == "local_model_runtime_invocation"
    assert "prompt" not in audits[0][1]


def test_observer_rejects_non_local_profiles() -> None:
    observer = LocalRuntimeInvocationObserver(snapshot=_snapshot, audit_sink=lambda *_: None)

    try:
        observer.observe(
            profile_id="cloud_profile",
            provider_id="cloud",
            model_id="model",
            success=False,
            reason_code="blocked",
            call_profile=None,
            fallback_index=0,
            context_capacity=1,
            confidence_available=False,
            goal_id=None,
            task_id=None,
        )
    except ValueError as exc:
        assert str(exc) == "local_runtime_invocation_profile_unsupported"
    else:
        raise AssertionError("unsupported profile must fail closed")


def test_observer_records_needle_candidate_attempt_without_content() -> None:
    audits: list[dict] = []
    observer = LocalRuntimeInvocationObserver(
        snapshot=_snapshot,
        audit_sink=lambda _action, facts: audits.append(dict(facts)),
    )

    observer.emit(
        {
            "schema": "ananta.tiny_tool_router_event.v1",
            "status": "candidate",
            "reason_code": "candidate_validated",
            "prompt_chars": 42,
            "confidence": 0.91,
            "tool_name": "private.tool.name",
            "attempts": [
                {
                    "profile_id": "needle-2-45m",
                    "tier": "tiny",
                    "status": "candidate",
                    "reason_code": "candidate_validated",
                    "latency_ms": 7.5,
                    "selected_tool_count": 2,
                }
            ],
        }
    )

    item = observer.read(limit=1)[0]
    assert item.runtime_id == "needle"
    assert item.candidate_only is True
    assert item.candidate_confidence == 0.91
    assert item.prompt_chars == 42
    assert item.total_tokens == 0
    assert "tool_name" not in audits[0]
