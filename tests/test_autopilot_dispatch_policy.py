from types import SimpleNamespace

from agent.routes.tasks.autopilot_dispatch_policy import resolve_effective_concurrency, resolve_target_worker_for_task


def test_effective_concurrency_fail_closed_when_missing_cap():
    assert resolve_effective_concurrency(requested_max_concurrency=8, security_policy={}) == 1


def test_effective_concurrency_requested_above_cap_is_capped():
    assert resolve_effective_concurrency(requested_max_concurrency=8, security_policy={"max_concurrency_cap": 3}) == 3


def test_effective_concurrency_worker_limited():
    assert resolve_effective_concurrency(
        requested_max_concurrency=8,
        security_policy={"max_concurrency_cap": 6},
        online_worker_capacity=2,
        runtime_capacity=6,
        ollama_capacity=6,
    ) == 2


def test_effective_concurrency_ollama_limited():
    assert resolve_effective_concurrency(
        requested_max_concurrency=8,
        security_policy={"max_concurrency_cap": 6},
        online_worker_capacity=6,
        runtime_capacity=6,
        ollama_capacity=2,
    ) == 2


def test_effective_concurrency_invalid_inputs_fail_closed():
    assert resolve_effective_concurrency(
        requested_max_concurrency="invalid",  # type: ignore[arg-type]
        security_policy={"max_concurrency_cap": "invalid"},
        online_worker_capacity=0,
        runtime_capacity=-1,
        ollama_capacity=None,
    ) == 1


def test_resolve_target_worker_filters_hub_self_when_disabled():
    task = SimpleNamespace(
        assigned_agent_url=None,
        _hub_can_be_worker=False,
        _local_worker_url="http://hub:5000",
    )
    workers = [SimpleNamespace(url="http://hub:5000", token="t")]
    target, cursor, was_assigned, reason = resolve_target_worker_for_task(task=task, workers=workers, worker_cursor=0)
    assert target is None
    assert cursor == 0
    assert was_assigned is False
    assert reason == "hub_self_worker_filtered"


def test_resolve_target_worker_assigned_offline_is_not_round_robin_fallback():
    task = SimpleNamespace(
        assigned_agent_url="http://worker-missing:5000",
        _hub_can_be_worker=False,
        _local_worker_url="http://hub:5000",
    )
    workers = [SimpleNamespace(url="http://worker-a:5000", token="ta")]
    target, cursor, was_assigned, reason = resolve_target_worker_for_task(task=task, workers=workers, worker_cursor=0)
    assert target is None
    assert cursor == 0
    assert was_assigned is False
    assert reason == "assigned_worker_offline"


def test_resolve_target_worker_assigned_hub_is_blocked_when_forbidden():
    task = SimpleNamespace(
        assigned_agent_url="http://hub:5000",
        _hub_can_be_worker=False,
        _local_worker_url="http://hub:5000",
    )
    workers = [SimpleNamespace(url="http://hub:5000", token="t")]
    target, cursor, was_assigned, reason = resolve_target_worker_for_task(task=task, workers=workers, worker_cursor=0)
    assert target is None
    assert cursor == 0
    assert was_assigned is False
    assert reason == "assigned_worker_is_hub_forbidden"
