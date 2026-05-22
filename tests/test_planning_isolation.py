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
