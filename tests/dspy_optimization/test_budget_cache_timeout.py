from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.dspy_program_artifact_store import DspyProgramArtifactStore
from ananta_contracts.dspy_optimization import OptimizationBudgets
from tests.dspy_optimization.helpers import price_profiles, program, spec
from worker.optimization.dspy.attempt_context import DspyCheckpointStore, current_attempt_context
from worker.optimization.dspy.cache import DspyRunMemoryCache
from worker.optimization.dspy.job_runner import DspyOptimizationJobRunner
from worker.optimization.dspy.lm_bridge import (
    AnantaBaseLmBridge,
    DspyBudgetExceeded,
    DspyBudgetLedger,
    DspyRetryableProviderError,
)


def _cache_binding(tenant: str = "tenant-1") -> dict[str, str]:
    return {
        "tenant_id": tenant,
        "dataset_digest": "a" * 64,
        "program_digest": "b" * 64,
        "model_digest": "c" * 64,
        "provider_digest": "d" * 64,
        "optimizer_digest": "e" * 64,
        "metric_digest": "f" * 64,
        "config_digest": "1" * 64,
        "request_digest": "2" * 64,
    }


def test_cache_keys_are_complete_and_cross_tenant_isolated() -> None:
    cache = DspyRunMemoryCache(max_entries=2)
    first = _cache_binding()
    assert cache.put(first, {"answer": 1}) == cache.key(first)
    assert cache.get(first) == {"answer": 1}
    assert cache.get(_cache_binding("tenant-2")) is None
    with pytest.raises(ValueError, match="binding_invalid"):
        cache.key({"tenant_id": "tenant-1"})


def test_role_and_concurrency_budgets_fail_closed() -> None:
    budgets = OptimizationBudgets(3, 100, 0, 30, 1, 10, 10_000, max_role_calls=1)
    ledger = DspyBudgetLedger(budgets)
    ledger.reserve("request-1", role="student")
    with pytest.raises(DspyBudgetExceeded, match="role_budget_exhausted"):
        ledger.reserve("request-2", role="student")
    with ledger.slot():
        with pytest.raises(DspyBudgetExceeded, match="concurrency_exhausted"):
            with ledger.slot():
                pass


class SlowEngine:
    def optimize(self, _spec, baseline, _records):
        return replace(baseline, program_id="candidate")


def test_worker_timeout_is_terminal_and_does_not_write_artifact(tmp_path) -> None:
    ticks = iter((0.0, 31.0))
    runner = DspyOptimizationJobRunner(
        SlowEngine(),
        DspyProgramArtifactStore(tmp_path / "artifacts"),
        authorization_verifier=lambda _job: True,
        clock=lambda: next(ticks),
    )
    result = runner.run(
        job={"tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        baseline=program(),
        records=[],
    )
    assert result["state"] == "failed"
    assert result["reason_code"] == "dspy_worker_timed_out"
    assert list((tmp_path / "artifacts").glob("tenant-1/run-1/*.json")) == []


class IsolatedEngine:
    def __init__(self) -> None:
        self.directories: list[str] = []
        self.checkpoints: list[dict | None] = []

    def optimize(self, _spec, baseline, _records):
        context = current_attempt_context()
        self.directories.append(context.temporary_directory)
        self.checkpoints.append(dict(context.checkpoint) if context.checkpoint else None)
        return replace(baseline, program_id="candidate")


def test_attempt_state_is_isolated_cleaned_and_checkpoint_bound(tmp_path) -> None:
    engine = IsolatedEngine()
    checkpoints = DspyCheckpointStore(tmp_path / "checkpoints")
    checkpoints.put(
        tenant_id="tenant-1",
        run_id="run-1",
        spec_digest=spec().digest,
        state={"phase": "optimized", "program_digest": "a" * 64},
    )
    runner = DspyOptimizationJobRunner(
        engine,
        DspyProgramArtifactStore(tmp_path / "artifacts"),
        authorization_verifier=lambda _job: True,
        workspace_root=tmp_path / "attempts",
        checkpoints=checkpoints,
    )
    result = runner.run(
        job={"tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        baseline=program(),
        records=[],
    )
    assert result["state"] == "completed"
    assert engine.checkpoints == [{"phase": "optimized", "program_digest": "a" * 64}]
    assert not Path(engine.directories[0]).exists()
    assert checkpoints.load(tenant_id="tenant-1", run_id="run-1", spec_digest=spec().digest) is None
    with pytest.raises(RuntimeError, match="context_unavailable"):
        current_attempt_context()


class RetryOncePort:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def complete(self, *, binding, role, messages, request_id):
        self.request_ids.append(request_id)
        if len(self.request_ids) == 1:
            raise DspyRetryableProviderError("temporary")
        return {
            "text": "ok",
            "finish_reason": "stop",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cache_hit": False},
            "cost_micros": 0,
        }


def test_retry_is_bounded_budgeted_and_keeps_idempotent_request_identity() -> None:
    port = RetryOncePort()
    events: list[dict] = []
    ticks = iter((10.0, 10.125))
    bridge = AnantaBaseLmBridge(
        port,
        bindings=spec().provider_bindings,
        ledger=DspyBudgetLedger(spec().budgets),
        run_id="run-1",
        attempt_id="attempt-1",
        audit_sink=lambda event: events.append(dict(event)),
        clock=lambda: next(ticks),
        price_profiles=price_profiles(),
    )
    result = bridge.complete(role="student", messages=({"role": "user", "content": "bounded"},), call_index=0)
    assert result["text"] == "ok"
    assert port.request_ids[0] == port.request_ids[1]
    assert events[0]["retry_count"] == 1
    assert events[0]["latency_ms"] == 125
    assert events[0]["rollout_id"] == "call-0"


def test_cancellation_stops_before_a_new_provider_call() -> None:
    port = RetryOncePort()
    bridge = AnantaBaseLmBridge(
        port,
        bindings=spec().provider_bindings,
        ledger=DspyBudgetLedger(spec().budgets),
        run_id="run-1",
        attempt_id="attempt-1",
        price_profiles=price_profiles(),
        cancelled=lambda: True,
    )
    with pytest.raises(DspyBudgetExceeded, match="call_cancelled"):
        bridge.complete(role="student", messages=({"role": "user", "content": "bounded"},), call_index=0)
    assert port.request_ids == []
