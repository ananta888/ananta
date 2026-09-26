from types import SimpleNamespace

from agent.routes.tasks.autopilot_dispatch_policy import (
    resolve_dispatch_hard_timeout,
    resolve_effective_concurrency,
    resolve_target_worker_for_task,
)
from tests.knowledge_index_execution_test_support import (
    build_execution_task,
)


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


def test_dispatch_timeout_uses_bound_knowledge_index_runtime_budget():
    task = SimpleNamespace(**build_execution_task(max_runtime_seconds=900))

    assert resolve_dispatch_hard_timeout(
        tasks=[task],
        security_policy={"propose_timeout": 120, "execute_timeout": 45},
    ) == 1080


def test_dispatch_timeout_does_not_truncate_long_bound_v2_budget():
    task = SimpleNamespace(
        **build_execution_task(max_runtime_seconds=7_200)
    )

    assert resolve_dispatch_hard_timeout(
        tasks=[task],
        security_policy={"propose_timeout": 120, "execute_timeout": 45},
    ) == 7_380


def test_dispatch_timeout_ignores_untrusted_job_shapes():
    task = SimpleNamespace(
        task_kind="codecompass_index_build",
        worker_execution_context={
            "knowledge_index_job": {
                "schema": "browser-invented-job",
                "resources": {"max_runtime_seconds": 900},
            },
        },
    )

    assert resolve_dispatch_hard_timeout(
        tasks=[task],
        security_policy={"propose_timeout": 120, "execute_timeout": 45},
    ) == 195


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


def test_bound_knowledge_index_routes_to_authorized_destination_worker():
    task = SimpleNamespace(
        assigned_agent_url=None,
        task_kind="codecompass_index_build",
        worker_execution_context={
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
            },
            "destination_selection": {"worker_id": "worker-b"},
        },
        _hub_can_be_worker=False,
        _local_worker_url="http://hub:5000",
    )
    workers = [
        SimpleNamespace(
            name="worker-a",
            url="http://worker-a:5000",
            token="ta",
        ),
        SimpleNamespace(
            name="worker-b",
            url="http://worker-b:5000",
            token="tb",
        ),
    ]

    target, cursor, was_assigned, reason = resolve_target_worker_for_task(
        task=task,
        workers=workers,
        worker_cursor=0,
    )

    assert target is workers[1]
    assert cursor == 0
    assert was_assigned is True
    assert reason == "destination_worker_binding"


def test_bound_knowledge_index_fails_closed_when_destination_worker_is_absent():
    task = SimpleNamespace(
        assigned_agent_url=None,
        task_kind="codecompass_index_build",
        worker_execution_context={
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
            },
            "destination_selection": {"worker_id": "worker-missing"},
        },
        _hub_can_be_worker=False,
        _local_worker_url="http://hub:5000",
    )
    workers = [
        SimpleNamespace(
            name="worker-a",
            url="http://worker-a:5000",
            token="ta",
        ),
    ]

    target, cursor, was_assigned, reason = resolve_target_worker_for_task(
        task=task,
        workers=workers,
        worker_cursor=3,
    )

    assert target is None
    assert cursor == 3
    assert was_assigned is False
    assert reason == "destination_worker_unavailable"


def test_layer_builds_go_only_to_workers_with_the_handler():
    hub = SimpleNamespace(url="http://localhost:5000", capabilities=["coding"])
    plain = SimpleNamespace(url="http://ai-agent-beta:5000", capabilities=["coding", "retrieval"])
    layered = SimpleNamespace(url="http://ai-agent-alpha:5000",
                              capabilities=["retrieval", "index_write", "codecompass_layer_build"])
    task = SimpleNamespace(task_kind="codecompass_layer_build", assigned_agent_url=None,
                           _hub_can_be_worker=True, _local_worker_url="http://localhost:5000")
    for cursor in range(4):
        target, _cursor, assigned, reason = resolve_target_worker_for_task(
            task=task, workers=[hub, plain, layered], worker_cursor=cursor)
        assert target is layered and assigned is True and reason is None
    missing = resolve_target_worker_for_task(task=task, workers=[hub, plain], worker_cursor=0)
    assert missing[0] is None and missing[3] == "required_capability_unavailable"


def test_other_task_kinds_keep_round_robin_over_all_workers():
    workers = [SimpleNamespace(url=f"http://{name}:5000", capabilities=[]) for name in ("a", "b")]
    task = SimpleNamespace(task_kind="coding", assigned_agent_url=None, _hub_can_be_worker=True, _local_worker_url="")
    picks = {resolve_target_worker_for_task(task=task, workers=workers, worker_cursor=c)[0].url for c in range(2)}
    assert picks == {"http://a:5000", "http://b:5000"}
