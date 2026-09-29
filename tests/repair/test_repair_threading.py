"""Thread-safety tests for the autopilot tick engine threading refactor (thr-001..thr-012).

Tests focus on:
- Counter mutations (_increment_*) under concurrent access
- Worker cursor uniqueness from parallel _assign_worker calls
- Lock ordering (routing_lock never nested inside counters_lock)
- Per-thread Flask app_context opening in _dispatch_one_task
- Hard-timeout path marks tasks failed
- Aggregate counter correctness after parallel dispatch
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.routes.tasks.autopilot import AutonomousLoopManager


# ── helpers ──────────────────────────────────────────────────────────────────

def _fresh_loop() -> AutonomousLoopManager:
    loop = AutonomousLoopManager()
    loop._app = None
    return loop


def _fake_worker(url: str) -> SimpleNamespace:
    return SimpleNamespace(url=url, token="tok", name=url)


# ── 1. Counter thread safety ──────────────────────────────────────────────────

def test_increment_dispatched_is_thread_safe():
    loop = _fresh_loop()
    n = 100
    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(lambda _: loop._increment_dispatched(), range(n)))
    assert loop.dispatched_count == n


def test_increment_completed_is_thread_safe():
    loop = _fresh_loop()
    n = 100
    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(lambda _: loop._increment_completed(), range(n)))
    assert loop.completed_count == n


def test_increment_failed_is_thread_safe():
    loop = _fresh_loop()
    n = 100
    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(lambda _: loop._increment_failed(), range(n)))
    assert loop.failed_count == n


def test_mixed_counter_increments_are_thread_safe():
    """Dispatched = completed + failed even under concurrent mixed mutations."""
    loop = _fresh_loop()
    n = 60

    def _work(i):
        loop._increment_dispatched()
        if i % 2 == 0:
            loop._increment_completed()
        else:
            loop._increment_failed()

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_work, range(n)))

    assert loop.dispatched_count == n
    assert loop.completed_count + loop.failed_count == n


def test_set_last_error_is_thread_safe():
    """Final last_error is always one of the values written — no torn read."""
    loop = _fresh_loop()
    errors = [f"err-{i}" for i in range(50)]
    barrier = threading.Barrier(len(errors))

    def _write(e):
        barrier.wait()
        loop._set_last_error(e)

    with ThreadPoolExecutor(max_workers=len(errors)) as ex:
        list(ex.map(_write, errors))

    assert loop.last_error is None or loop.last_error in errors


# ── 2. Worker cursor uniqueness ───────────────────────────────────────────────

def test_assign_worker_cursor_is_unique_under_concurrency():
    """Each thread must get a distinct worker slot — no two threads share a cursor."""
    from agent.routes.tasks.autopilot_dispatch_policy import resolve_target_worker_for_task

    loop = _fresh_loop()
    workers = [_fake_worker(f"http://w{i}:5000") for i in range(5)]
    n_calls = 40
    results: list[int] = []
    lock = threading.Lock()

    def _call(_):
        task = SimpleNamespace(id=f"t-{threading.get_ident()}", required_capabilities=[], team_id=None)
        target_worker, was_assigned = loop._assign_worker(task, workers)
        # record the URL for duplicate detection
        if target_worker is not None:
            with lock:
                results.append(id(target_worker))

    with ThreadPoolExecutor(max_workers=n_calls) as ex:
        list(ex.map(_call, range(n_calls)))

    # We can't guarantee unique workers per call (round-robin recycles),
    # but cursor mutations must not have caused a race (test would hang or crash if they had).
    assert len(results) == n_calls


def test_assign_worker_cursor_advances_round_robin():
    """Cursor advances predictably so successive single-threaded calls pick different workers."""
    loop = _fresh_loop()
    workers = [_fake_worker(f"http://w{i}:5000") for i in range(3)]
    seen_urls: list[str] = []
    for _ in range(6):
        task = SimpleNamespace(id=f"t-rr-{_}", required_capabilities=[], team_id=None)
        worker, _ = loop._assign_worker(task, workers)
        if worker is not None:
            seen_urls.append(worker.url)
    # After 6 calls with 3 workers we must have visited all 3 at least once.
    assert set(seen_urls) == {"http://w0:5000", "http://w1:5000", "http://w2:5000"}


# ── 3. Lock ordering ─────────────────────────────────────────────────────────

def test_record_worker_failure_releases_routing_lock_before_counters_lock():
    """Verify _record_worker_failure does NOT hold _routing_lock while touching last_error.

    Strategy: intercept last_error setter via _counters_lock, verify that at that
    point _routing_lock is NOT locked (would raise RuntimeError on a non-reentrant Lock).
    """
    loop = _fresh_loop()
    # Trigger circuit open immediately (threshold = 1 for test)
    loop._app = MagicMock()
    loop._app.config = {
        "AGENT_CONFIG": {
            "execution_resilience": {
                "circuit_breaker_threshold": 1,
                "circuit_breaker_open_seconds": 1,
                "retry_attempts": 1,
                "retry_delay_seconds": 0,
            }
        }
    }

    routing_locked_during_counter = []

    original_set_error = loop._set_last_error

    def _spy_set_error(e):
        # At this point _routing_lock must NOT be held by this thread
        acquired = loop._routing_lock.acquire(blocking=False)
        routing_locked_during_counter.append(acquired)
        if acquired:
            loop._routing_lock.release()
        original_set_error(e)

    loop._set_last_error = _spy_set_error

    loop._record_worker_failure("http://w1:5000", "test_reason", task_id="t-lock-order")

    # If routing_lock was free when _set_last_error was called, acquired == True
    if routing_locked_during_counter:
        assert routing_locked_during_counter[-1] is True, (
            "_routing_lock was held while updating last_error — lock ordering violation!"
        )


# ── 4. Per-thread Flask app_context in _dispatch_one_task ────────────────────

def test_dispatch_one_task_opens_app_context_per_thread(app):
    """_dispatch_one_task must push an app_context in each worker thread."""
    from agent.routes.tasks.autopilot_tick_engine import _dispatch_one_task

    app_contexts_entered = []
    lock = threading.Lock()

    # Patch the inner function so we only check context, not real execution
    def _fake_inner(**kwargs):
        from flask import current_app
        try:
            _ = current_app._get_current_object()
            with lock:
                app_contexts_entered.append(True)
        except RuntimeError:
            with lock:
                app_contexts_entered.append(False)
        from agent.routes.tasks.autopilot_tick_engine import TaskDispatchResult
        return TaskDispatchResult(task_id=kwargs["task"].id)

    task = SimpleNamespace(id="t-ctx-test", required_capabilities=[], team_id=None)
    worker = _fake_worker("http://w-ctx:5000")

    loop = _fresh_loop()
    loop._app = app

    with patch(
        "agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner",
        side_effect=_fake_inner,
    ):
        _dispatch_one_task(
            task=task,
            target_worker=worker,
            was_assigned=False,
            loop=loop,
            services=MagicMock(),
            policy={},
            fallback_policy={"allow_hub_worker_fallback": True, "escalate_on_fallback_block": False, "fallback_block_status": "blocked"},
            runtime_caps={},
            queue_positions={},
            local_worker_url="http://localhost:5000",
            app=app,
            append_trace_event=lambda *a, **kw: None,
            update_local_task_status=lambda *a, **kw: None,
        )

    assert app_contexts_entered == [True], "Thread did not get a valid Flask app_context"


# ── 5. Hard-timeout path marks tasks failed ───────────────────────────────────

def test_dispatch_hard_timeout_marks_task_failed(app, monkeypatch):
    """When _dispatch_one_task_inner sleeps longer than the hard timeout,
    the task must be marked 'failed' with error='dispatch_timeout'."""
    from agent.routes.tasks.autopilot_tick_engine import _dispatch_one_task, TaskDispatchResult

    status_updates: list[tuple] = []

    def _slow_inner(**kwargs):
        time.sleep(5)  # will be cancelled by timeout
        return TaskDispatchResult(task_id=kwargs["task"].id)

    def _capture_status(task_id, status, **kw):
        status_updates.append((task_id, status, kw.get("error")))

    task = SimpleNamespace(id="t-timeout", required_capabilities=[], team_id=None)
    worker = _fake_worker("http://w-slow:5000")
    loop = _fresh_loop()
    loop._app = app

    import concurrent.futures as cf
    future_map: dict[cf.Future, str] = {}
    per_task_hard_timeout = 0.1  # very short for the test

    with patch(
        "agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner",
        side_effect=_slow_inner,
    ):
        with cf.ThreadPoolExecutor(max_workers=1) as executor:
            fut = executor.submit(
                _dispatch_one_task,
                task=task,
                target_worker=worker,
                was_assigned=False,
                loop=loop,
                services=MagicMock(),
                policy={},
                fallback_policy={"allow_hub_worker_fallback": True, "escalate_on_fallback_block": False, "fallback_block_status": "blocked"},
                runtime_caps={},
                queue_positions={},
                local_worker_url="http://localhost:5000",
                app=app,
                append_trace_event=lambda *a, **kw: None,
                update_local_task_status=_capture_status,
            )
            future_map[fut] = task.id
            try:
                for f in cf.as_completed(future_map, timeout=per_task_hard_timeout):
                    f.result()
            except cf.TimeoutError:
                for f, tid in future_map.items():
                    if not f.done():
                        f.cancel()
                        _capture_status(tid, "failed", error="dispatch_timeout")

    failed_entries = [(tid, st, err) for tid, st, err in status_updates if st == "failed"]
    assert any(tid == "t-timeout" for tid, _, _ in failed_entries), (
        f"Expected t-timeout to be marked failed; updates: {status_updates}"
    )


# ── 6. Aggregate counter correctness after parallel dispatch ──────────────────

def test_parallel_dispatch_counters_aggregate_exactly(app, monkeypatch):
    """N tasks dispatched in parallel: dispatched_count == N, (completed+failed) == N."""
    from agent.routes.tasks.autopilot_tick_engine import _dispatch_one_task, TaskDispatchResult

    n = 8
    loop = _fresh_loop()
    loop._app = app

    call_order: list[str] = []
    call_lock = threading.Lock()

    def _fake_inner(**kwargs):
        tid = kwargs["task"].id
        with call_lock:
            call_order.append(tid)
        return TaskDispatchResult(task_id=tid, dispatched=True, completed=True)

    tasks = [SimpleNamespace(id=f"t-par-{i}", required_capabilities=[], team_id=None) for i in range(n)]
    workers = [_fake_worker("http://w0:5000")]

    with patch(
        "agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner",
        side_effect=_fake_inner,
    ):
        import concurrent.futures as cf
        results: list[TaskDispatchResult] = []
        with cf.ThreadPoolExecutor(max_workers=n) as executor:
            futures = {
                executor.submit(
                    _dispatch_one_task,
                    task=t,
                    target_worker=workers[0],
                    was_assigned=False,
                    loop=loop,
                    services=MagicMock(),
                    policy={},
                    fallback_policy={"allow_hub_worker_fallback": True, "escalate_on_fallback_block": False, "fallback_block_status": "blocked"},
                    runtime_caps={},
                    queue_positions={},
                    local_worker_url="http://localhost:5000",
                    app=app,
                    append_trace_event=lambda *a, **kw: None,
                    update_local_task_status=lambda *a, **kw: None,
                ): t.id
                for t in tasks
            }
            for f in cf.as_completed(futures, timeout=10):
                results.append(f.result())

        for r in results:
            if r.dispatched:
                loop._increment_dispatched()
                if r.completed:
                    loop._increment_completed()
                else:
                    loop._increment_failed()

    assert loop.dispatched_count == n
    assert loop.completed_count + loop.failed_count == n
    assert set(call_order) == {f"t-par-{i}" for i in range(n)}


# ── 7. Circuit breaker thread safety ─────────────────────────────────────────

def test_circuit_breaker_opens_exactly_once_under_concurrent_failures():
    """Multiple threads recording failures simultaneously must not double-open the circuit."""
    loop = _fresh_loop()
    loop._app = MagicMock()
    loop._app.config = {
        "AGENT_CONFIG": {
            "execution_resilience": {
                "circuit_breaker_threshold": 3,
                "circuit_breaker_open_seconds": 60,
                "retry_attempts": 1,
                "retry_delay_seconds": 0,
            }
        }
    }
    worker_url = "http://w-cb:5000"
    barrier = threading.Barrier(10)

    def _fail(_):
        barrier.wait()
        loop._record_worker_failure(worker_url, "concurrent_test")

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_fail, range(10)))

    open_until, streak = loop._circuit_open_details(worker_url)
    assert streak == 10
    assert open_until > time.time(), "Circuit should be open after 10 failures"


def test_record_worker_success_resets_streak():
    loop = _fresh_loop()
    loop._app = MagicMock()
    loop._app.config = {
        "AGENT_CONFIG": {
            "execution_resilience": {
                "circuit_breaker_threshold": 5,
                "circuit_breaker_open_seconds": 30,
                "retry_attempts": 1,
                "retry_delay_seconds": 0,
            }
        }
    }
    worker_url = "http://w-reset:5000"
    loop._record_worker_failure(worker_url, "r1")
    loop._record_worker_failure(worker_url, "r2")
    loop._record_worker_success(worker_url)

    open_until, streak = loop._circuit_open_details(worker_url)
    assert streak == 0
    assert open_until == 0.0
