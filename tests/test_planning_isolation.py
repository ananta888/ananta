"""Tests for planning isolation: lease/heartbeat, circuit breaker, reason codes (PRI-014)."""
from __future__ import annotations

import threading
import time
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# PRI-004: Planning Lease / Heartbeat
# ---------------------------------------------------------------------------

class TestPlanningLease:
    def _read_lease(self, engine, goal_id: str):
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text("SELECT planning_lease_expires_at FROM goals WHERE id = :gid"), {"gid": goal_id}).fetchone()
            return row[0] if row else None

    def test_set_planning_lease_writes_expires_at(self, app):
        from agent.routes.tasks.goals import _set_planning_lease
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        from agent.database import engine

        with app.app_context():
            goal = goal_repo.save(GoalDB(goal="lease test", status="planning_running", summary="t"))
            _set_planning_lease(goal.id, ttl_s=120)
            expires = self._read_lease(engine, goal.id)
            assert expires is not None
            assert expires > time.time()

    def test_set_planning_lease_skips_non_planning_running(self, app):
        from agent.routes.tasks.goals import _set_planning_lease
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        from agent.database import engine

        with app.app_context():
            goal = goal_repo.save(GoalDB(goal="lease skip test", status="planned", summary="t"))
            _set_planning_lease(goal.id, ttl_s=120)
            expires = self._read_lease(engine, goal.id)
            assert expires is None

    def test_clear_planning_lease_removes_expires_at(self, app):
        from agent.routes.tasks.goals import _set_planning_lease, _clear_planning_lease
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        from agent.database import engine

        with app.app_context():
            goal = goal_repo.save(GoalDB(goal="lease clear test", status="planning_running", summary="t"))
            _set_planning_lease(goal.id, ttl_s=60)
            _clear_planning_lease(goal.id)
            expires = self._read_lease(engine, goal.id)
            assert expires is None

    def test_heartbeat_thread_renews_lease(self, app):
        from agent.routes.tasks.goals import _start_planning_heartbeat, _set_planning_lease
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        from agent.database import engine

        with app.app_context():
            goal = goal_repo.save(GoalDB(goal="heartbeat test", status="planning_running", summary="t"))
            _set_planning_lease(goal.id, ttl_s=5)
            first_expires = self._read_lease(engine, goal.id)
            stop = threading.Event()
            _start_planning_heartbeat(goal_id=goal.id, stop_event=stop, interval_s=1)
            time.sleep(2.5)
            stop.set()
            renewed = self._read_lease(engine, goal.id)
            assert renewed is not None
            assert renewed >= first_expires

    def test_heartbeat_stops_when_event_set(self, app):
        from agent.routes.tasks.goals import _start_planning_heartbeat
        from agent.db_models import GoalDB
        from agent.repository import goal_repo

        with app.app_context():
            goal = goal_repo.save(GoalDB(goal="heartbeat stop test", status="planning_running", summary="t"))
            stop = threading.Event()
            thread = _start_planning_heartbeat(goal_id=goal.id, stop_event=stop, interval_s=1)
            stop.set()
            thread.join(timeout=3.0)
            assert not thread.is_alive()


# ---------------------------------------------------------------------------
# PRI-006: Preflight stale-recovery
# ---------------------------------------------------------------------------

class TestPreflightStaleRecovery:
    def test_cancel_stale_planning_goals_marks_expired_lease_as_failed(self, app):
        from agent.routes.tasks.goals import _cancel_stale_planning_goals
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        from sqlalchemy import text
        from agent.database import engine

        with app.app_context():
            stale = goal_repo.save(GoalDB(goal="stale planning", status="planning_running", summary="t"))
            fresh = goal_repo.save(GoalDB(goal="fresh planning", status="planning_running", summary="t"))
            no_lease = goal_repo.save(GoalDB(goal="no lease planning", status="planning_running", summary="t"))

            # Set lease values directly via SQL to avoid session conflicts.
            with engine.connect() as conn:
                conn.execute(text("UPDATE goals SET planning_lease_expires_at = :exp WHERE id = :gid"),
                             {"exp": time.time() - 10, "gid": stale.id})
                conn.execute(text("UPDATE goals SET planning_lease_expires_at = :exp WHERE id = :gid"),
                             {"exp": time.time() + 300, "gid": fresh.id})
                conn.commit()

            cancelled = _cancel_stale_planning_goals()
            assert cancelled == 1

            assert goal_repo.get_by_id(stale.id).status == "failed"
            assert goal_repo.get_by_id(fresh.id).status == "planning_running"
            assert goal_repo.get_by_id(no_lease.id).status == "planning_running"


# ---------------------------------------------------------------------------
# PRI-009: Circuit Breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def setup_method(self):
        from agent.llm_integration import CIRCUIT_BREAKER
        from collections import defaultdict
        CIRCUIT_BREAKER["failures"] = defaultdict(int)
        CIRCUIT_BREAKER["last_failure"] = defaultdict(float)
        CIRCUIT_BREAKER["open"] = defaultdict(bool)

    def test_circuit_starts_closed(self):
        from agent.llm_integration import _check_circuit_breaker
        assert _check_circuit_breaker("lmstudio") is True

    def test_circuit_opens_after_threshold_failures(self):
        from agent.llm_integration import _report_llm_failure, _check_circuit_breaker, _CB_DEFAULT_THRESHOLD
        for _ in range(_CB_DEFAULT_THRESHOLD):
            _report_llm_failure("lmstudio")
        assert _check_circuit_breaker("lmstudio") is False

    def test_circuit_transitions_to_half_open_after_recovery_time(self):
        from agent.llm_integration import _report_llm_failure, _check_circuit_breaker, CIRCUIT_BREAKER, _CB_DEFAULT_THRESHOLD
        for _ in range(_CB_DEFAULT_THRESHOLD):
            _report_llm_failure("lmstudio")
        # Simulate time passage beyond recovery window.
        CIRCUIT_BREAKER["last_failure"]["lmstudio"] = time.time() - 9999
        assert _check_circuit_breaker("lmstudio") is True
        assert CIRCUIT_BREAKER["open"]["lmstudio"] is False

    def test_report_success_resets_failures(self):
        from agent.llm_integration import _report_llm_failure, _report_llm_success, _check_circuit_breaker, _CB_DEFAULT_THRESHOLD
        for _ in range(_CB_DEFAULT_THRESHOLD - 1):
            _report_llm_failure("lmstudio")
        _report_llm_success("lmstudio")
        assert _check_circuit_breaker("lmstudio") is True

    def test_get_circuit_breaker_state_returns_dict(self):
        from agent.llm_integration import get_circuit_breaker_state
        state = get_circuit_breaker_state("lmstudio")
        assert state["provider"] == "lmstudio"
        assert state["state"] in ("open", "closed")
        assert isinstance(state["threshold"], int)

    def test_circuit_breaker_uses_config_threshold(self, app):
        """Circuit should open at config threshold, not hardcoded default."""
        from agent.llm_integration import _report_llm_failure, _check_circuit_breaker, CIRCUIT_BREAKER

        with app.app_context():
            # Patch AGENT_CONFIG to use low threshold.
            with mock.patch.dict(
                app.config,
                {"AGENT_CONFIG": {"llm_config": {"circuit_breaker_threshold": 2, "circuit_breaker_open_seconds": 30}}},
            ):
                CIRCUIT_BREAKER["failures"]["test_provider"] = 0
                CIRCUIT_BREAKER["open"]["test_provider"] = False
                _report_llm_failure("test_provider")
                assert _check_circuit_breaker("test_provider") is True  # 1 failure, threshold=2
                _report_llm_failure("test_provider")
                assert _check_circuit_breaker("test_provider") is False  # 2 failures → open


# ---------------------------------------------------------------------------
# PRI-005: Separate queue_wait_timeout vs execute_timeout
# ---------------------------------------------------------------------------

class TestPlanningTimeoutSeparation:
    def test_normalize_slot_capacity_clamps_to_range(self):
        from agent.routes.tasks.goals import _normalize_planning_slot_capacity
        assert _normalize_planning_slot_capacity(0) == 1
        assert _normalize_planning_slot_capacity(1) == 1
        assert _normalize_planning_slot_capacity(4) == 4
        assert _normalize_planning_slot_capacity(100) == 32
        assert _normalize_planning_slot_capacity(None) == 1
        assert _normalize_planning_slot_capacity("garbage") == 1

    def test_acquire_and_release_slot(self):
        from agent.routes.tasks.goals import _acquire_planning_slot, _release_planning_slot, _PLANNING_SLOTS_LOCK, _PLANNING_SLOTS
        import agent.routes.tasks.goals as goals_mod
        # Reset to known state.
        with _PLANNING_SLOTS_LOCK:
            goals_mod._PLANNING_SLOTS = None
            goals_mod._PLANNING_SLOTS_CAPACITY = 0

        acquired, capacity = _acquire_planning_slot(timeout_s=1, capacity=2)
        assert acquired is True
        assert capacity == 2
        _release_planning_slot()


# ---------------------------------------------------------------------------
# PRI-011: Reason codes completeness
# ---------------------------------------------------------------------------

class TestReasonCodes:
    def test_all_terminal_codes_are_strings(self):
        from agent.planning_reason_codes import TERMINAL_REASON_CODES
        for code in TERMINAL_REASON_CODES:
            assert isinstance(code, str) and code

    def test_key_codes_present(self):
        from agent.planning_reason_codes import (
            PLANNING_SLOT_TIMEOUT,
            PLANNING_BACKGROUND_TIMEOUT,
            PLANNING_DEADLINE_GUARD_TIMEOUT,
            PLANNING_STALE_RECOVERED,
            CIRCUIT_BREAKER_OPEN,
            TERMINAL_REASON_CODES,
        )
        for code in (
            PLANNING_SLOT_TIMEOUT,
            PLANNING_BACKGROUND_TIMEOUT,
            PLANNING_DEADLINE_GUARD_TIMEOUT,
            PLANNING_STALE_RECOVERED,
            CIRCUIT_BREAKER_OPEN,
        ):
            assert code in TERMINAL_REASON_CODES


# ---------------------------------------------------------------------------
# PRI-010: Rate-Limit / Backpressure
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PRI-012: Provider error rate tracking
# ---------------------------------------------------------------------------

class TestProviderErrorRate:
    def setup_method(self):
        from agent.llm_integration import _ERR_SUCCESS_WINDOW, _ERR_FAILURE_WINDOW
        _ERR_SUCCESS_WINDOW.clear()
        _ERR_FAILURE_WINDOW.clear()

    def test_error_rate_zero_when_no_calls(self):
        from agent.llm_integration import get_provider_error_rate
        state = get_provider_error_rate("lmstudio")
        assert state["error_rate"] == 0.0
        assert state["total"] == 0

    def test_error_rate_one_when_all_failures(self):
        from agent.llm_integration import _report_llm_failure, get_provider_error_rate
        _report_llm_failure("lmstudio")
        _report_llm_failure("lmstudio")
        state = get_provider_error_rate("lmstudio")
        assert state["error_rate"] == 1.0
        assert state["failures"] == 2
        assert state["successes"] == 0

    def test_error_rate_mixed(self):
        from agent.llm_integration import _report_llm_failure, _report_llm_success, get_provider_error_rate
        _report_llm_success("lmstudio")
        _report_llm_success("lmstudio")
        _report_llm_failure("lmstudio")
        state = get_provider_error_rate("lmstudio")
        assert state["total"] == 3
        assert abs(state["error_rate"] - 0.333) < 0.01

    def test_error_rate_evicts_old_entries(self):
        from agent.llm_integration import _report_llm_failure, get_provider_error_rate, _ERR_FAILURE_WINDOW
        # Inject old timestamp directly.
        _ERR_FAILURE_WINDOW["lmstudio"].append(time.time() - 120)
        state = get_provider_error_rate("lmstudio", window_s=60.0)
        assert state["failures"] == 0  # old entry evicted

    def test_error_rate_independent_per_provider(self):
        from agent.llm_integration import _report_llm_failure, _report_llm_success, get_provider_error_rate
        _report_llm_failure("lmstudio")
        _report_llm_failure("lmstudio")
        _report_llm_success("ollama")
        assert get_provider_error_rate("lmstudio")["error_rate"] == 1.0
        assert get_provider_error_rate("ollama")["error_rate"] == 0.0

    def test_planning_health_includes_error_rate(self, app, client, admin_auth_header):
        """PRI-012: /goals/planning/health must include provider_error_rate and by_profile."""
        res = client.get("/goals/planning/health", headers=admin_auth_header)
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert "provider_error_rate" in data
        er = data["provider_error_rate"]
        assert "error_rate" in er
        assert "total" in er
        assert "by_profile" in data


class TestRateLimit:
    def setup_method(self):
        from agent.llm_integration import _RATE_LIMIT_WINDOW
        _RATE_LIMIT_WINDOW.clear()

    def test_rate_limit_disabled_by_default(self):
        from agent.llm_integration import _check_rate_limit
        # No Flask context → _rl_config returns 0 → always allowed.
        for _ in range(100):
            assert _check_rate_limit("lmstudio") is True

    def test_rate_limit_blocks_over_budget(self, app):
        from agent.llm_integration import _check_rate_limit, _RATE_LIMIT_WINDOW
        import collections
        with app.app_context():
            with mock.patch.dict(
                app.config,
                {"AGENT_CONFIG": {"llm_config": {"rate_limit_rpm": 3}}},
            ):
                _RATE_LIMIT_WINDOW.clear()
                assert _check_rate_limit("lmstudio") is True
                assert _check_rate_limit("lmstudio") is True
                assert _check_rate_limit("lmstudio") is True
                assert _check_rate_limit("lmstudio") is False  # 4th request → blocked

    def test_rate_limit_window_expires_old_entries(self, app):
        from agent.llm_integration import _check_rate_limit, _RATE_LIMIT_WINDOW
        import collections
        with app.app_context():
            with mock.patch.dict(
                app.config,
                {"AGENT_CONFIG": {"llm_config": {"rate_limit_rpm": 2}}},
            ):
                _RATE_LIMIT_WINDOW.clear()
                # Inject two old timestamps (>60s ago) directly.
                old_ts = time.time() - 61
                _RATE_LIMIT_WINDOW["lmstudio"].extend([old_ts, old_ts])
                # Should be allowed because old entries are evicted.
                assert _check_rate_limit("lmstudio") is True

    def test_get_rate_limit_state_structure(self):
        from agent.llm_integration import get_rate_limit_state
        state = get_rate_limit_state("lmstudio")
        assert state["provider"] == "lmstudio"
        assert isinstance(state["requests_in_last_60s"], int)
        assert isinstance(state["enabled"], bool)

    def test_rate_limit_per_provider_independent(self, app):
        from agent.llm_integration import _check_rate_limit, _RATE_LIMIT_WINDOW
        with app.app_context():
            with mock.patch.dict(
                app.config,
                {"AGENT_CONFIG": {"llm_config": {"rate_limit_rpm": 1}}},
            ):
                _RATE_LIMIT_WINDOW.clear()
                assert _check_rate_limit("lmstudio") is True
                assert _check_rate_limit("lmstudio") is False  # blocked
                assert _check_rate_limit("ollama") is True    # independent budget


# ---------------------------------------------------------------------------
# PRI-015: Parallel goals — slot queuing integration
# ---------------------------------------------------------------------------

class TestParallelGoalsSlotQueuing:
    """PRI-015: Verify that parallel goals queue correctly and don't block indefinitely."""

    def test_two_parallel_goals_respect_capacity_one(self):
        """With capacity=1, second acquire times out cleanly — first is not starved."""
        from agent.routes.tasks.goals import _acquire_planning_slot, _release_planning_slot, _PLANNING_SLOTS_LOCK
        import agent.routes.tasks.goals as goals_mod

        with _PLANNING_SLOTS_LOCK:
            goals_mod._PLANNING_SLOTS = None
            goals_mod._PLANNING_SLOTS_CAPACITY = 0

        # First goal acquires the only slot.
        acquired1, cap1 = _acquire_planning_slot(timeout_s=1, capacity=1)
        assert acquired1 is True
        assert cap1 == 1

        # Second goal times out immediately (capacity=1, slot taken).
        acquired2, _ = _acquire_planning_slot(timeout_s=0, capacity=1)
        assert acquired2 is False

        # Release first — next acquire should succeed.
        _release_planning_slot()
        acquired3, _ = _acquire_planning_slot(timeout_s=1, capacity=1)
        assert acquired3 is True
        _release_planning_slot()

    def test_purge_after_cancel_leaves_no_orphan_tasks(self, app):
        """PRI-015: After purge, all tasks for that goal must be gone."""
        from agent.db_models import GoalDB, TaskDB
        from agent.repository import goal_repo, task_repo
        from agent.services.goal_purge_service import GoalPurgeService
        from types import SimpleNamespace

        with app.app_context():
            goal = goal_repo.save(GoalDB(goal="parallel orphan test", status="running", summary="t"))
            t1 = task_repo.save(TaskDB(id="orphan-t1", title="a", status="running", goal_id=goal.id, goal_trace_id=goal.trace_id))
            t2 = task_repo.save(TaskDB(id="orphan-t2", title="b", status="todo", goal_id=goal.id, goal_trace_id=goal.trace_id))

            svc = GoalPurgeService()
            # Patch dependencies.
            import agent.services.goal_purge_service as gps_mod
            original_pts = gps_mod.get_prompt_trace_service
            original_tas = gps_mod.get_task_admin_service
            gps_mod.get_prompt_trace_service = lambda: SimpleNamespace(delete_by_goal_id=lambda _: 0)
            gps_mod.get_task_admin_service = lambda: SimpleNamespace(intervene_task=lambda **_kw: (True, "ok", {}))
            try:
                result = svc.purge_goal(goal.id)
            finally:
                gps_mod.get_prompt_trace_service = original_pts
                gps_mod.get_task_admin_service = original_tas

            assert result is not None
            assert task_repo.get_by_id("orphan-t1") is None
            assert task_repo.get_by_id("orphan-t2") is None
            assert goal_repo.get_by_id(goal.id) is None

    def test_worker_cancel_retry_on_connection_failure(self):
        """PRI-008: RequestCancellationService retries failed worker POSTs on connection error."""
        from agent.services.request_cancellation_service import RequestCancellationService
        svc = RequestCancellationService()
        svc._retry_attempts = 2
        svc._retry_delay_s = 0.0

        call_count = [0]

        def _failing_post(*args, **kwargs):
            call_count[0] += 1
            raise ConnectionError("refused")

        with mock.patch("requests.post", side_effect=_failing_post):
            result = svc._post("http://worker:9000", "/cancel", token=None)

        assert result["ok"] is False
        assert call_count[0] == 2
        assert result["attempts"] == 2

    def test_worker_cancel_retry_on_5xx(self):
        """PRI-008: 5xx responses are retried; 4xx are not."""
        from agent.services.request_cancellation_service import RequestCancellationService
        svc = RequestCancellationService()
        svc._retry_attempts = 2
        svc._retry_delay_s = 0.0

        call_count = [0]

        def _5xx_response(*args, **kwargs):
            call_count[0] += 1
            r = mock.MagicMock()
            r.status_code = 503
            r.json.return_value = {"error": "overloaded"}
            r.text = "overloaded"
            return r

        with mock.patch("requests.post", side_effect=_5xx_response):
            result = svc._post("http://worker:9000", "/cancel", token=None)

        assert result["ok"] is False
        assert result["status_code"] == 503
        assert call_count[0] == 2  # retried once
        assert result["attempts"] == 2

    def test_worker_cancel_no_retry_on_4xx(self):
        """PRI-008: 4xx responses are NOT retried (client errors)."""
        from agent.services.request_cancellation_service import RequestCancellationService
        svc = RequestCancellationService()
        svc._retry_attempts = 3
        svc._retry_delay_s = 0.0

        call_count = [0]

        def _4xx_response(*args, **kwargs):
            call_count[0] += 1
            r = mock.MagicMock()
            r.status_code = 404
            r.json.return_value = {"error": "not found"}
            r.text = "not found"
            return r

        with mock.patch("requests.post", side_effect=_4xx_response):
            result = svc._post("http://worker:9000", "/cancel", token=None)

        assert result["ok"] is False
        assert result["status_code"] == 404
        assert call_count[0] == 1  # only one attempt for 4xx
        assert result["attempts"] == 1

    def test_worker_cancel_succeeds_on_second_attempt(self):
        """PRI-008: If first attempt fails but second succeeds, result is ok=True."""
        from agent.services.request_cancellation_service import RequestCancellationService
        svc = RequestCancellationService()
        svc._retry_attempts = 2
        svc._retry_delay_s = 0.0

        attempt = [0]

        def _flaky(*args, **kwargs):
            attempt[0] += 1
            if attempt[0] == 1:
                raise ConnectionError("transient")
            r = mock.MagicMock()
            r.status_code = 200
            r.json.return_value = {"ok": True}
            r.text = ""
            return r

        with mock.patch("requests.post", side_effect=_flaky):
            result = svc._post("http://worker:9000", "/cancel", token=None)

        assert result["ok"] is True
        assert result["attempts"] == 2


# ---------------------------------------------------------------------------
# PRI-016: Live smoke (feature-flagged, skipped unless ANANTA_LIVE_LMSTUDIO_SMOKE=1)
# ---------------------------------------------------------------------------

import os as _os

@pytest.mark.skipif(
    not _os.getenv("ANANTA_LIVE_LMSTUDIO_SMOKE"),
    reason="Set ANANTA_LIVE_LMSTUDIO_SMOKE=1 to run live LMStudio smoke tests.",
)
class TestLiveSmokeLMStudio:
    """PRI-016: Optional live smoke against a real LMStudio instance.

    Requires ANANTA_LIVE_LMSTUDIO_SMOKE=1 and a running Hub+LMStudio.
    """

    def test_planning_health_endpoint_responds(self):
        import requests
        base = _os.getenv("ANANTA_BASE_URL", "http://localhost:5000")
        try:
            r = requests.get(f"{base}/goals/planning/health", timeout=5)
        except Exception as exc:
            pytest.skip(f"Hub not reachable: {exc}")
        assert r.status_code in (200, 403)  # 403 = running but no admin creds

    def test_circuit_breaker_not_open_at_start(self):
        """Circuit breaker must start closed on a fresh hub."""
        from agent.llm_integration import get_circuit_breaker_state, CIRCUIT_BREAKER
        from collections import defaultdict
        CIRCUIT_BREAKER["failures"] = defaultdict(int)
        CIRCUIT_BREAKER["open"] = defaultdict(bool)
        state = get_circuit_breaker_state("lmstudio")
        assert state["state"] == "closed"

    def test_rate_limit_state_observable(self):
        """Rate-limit state is observable without error."""
        from agent.llm_integration import get_rate_limit_state
        state = get_rate_limit_state("lmstudio")
        assert "requests_in_last_60s" in state
        assert "limit_rpm" in state
