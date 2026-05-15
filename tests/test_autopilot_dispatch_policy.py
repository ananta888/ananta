from agent.routes.tasks.autopilot_dispatch_policy import resolve_effective_concurrency


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
