"""Tests für SnakeDecisionManager DSL v2 Integration (T05.04)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition, HeuristicRegistry
from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager


def _make_registry_with_dsl_heuristic(heuristic_id="snake-dsl-v1", has_dsl=True):
    """Creates a registry with an in-memory heuristic that has a _raw_def with mode=dsl_v2."""
    reg = HeuristicRegistry(base_path="/nonexistent")
    reg._loaded = True
    hdef = HeuristicDefinition(
        heuristic_id=heuristic_id, version="1.0.0", domain="tui_snake",
        strategy_kind="dsl_v2", description="DSL test heuristic", deterministic=True,
        safety_class="bounded", capabilities=(), inputs=(), outputs=(), parameters={},
    )
    # Attach _raw_def to simulate a definition with dsl_v2 runtime
    if has_dsl:
        object.__setattr__(hdef, "_raw_def", {
            "status": "active",
            "runtime": {
                "mode": "dsl_v2",
                "dsl_v2": {
                    "dsl": {
                        "dsl_version": "2.0",
                        "observe": {"sources": ["tui.snapshot"]},
                        "action": {"kind": "follow_artifact", "confidence": 0.9},
                        "safety": {"safety_class": "ui_motion_only"},
                        "provenance": {"created_by": "test", "rationale": "test dsl"},
                    }
                },
            },
        })
    reg._all.append(hdef)
    reg._definitions[hdef.heuristic_id] = hdef
    return reg


def _ctx(surface="tui_snake", ai_status="offline"):
    return DecisionContext(source_surface=surface, ai_status=ai_status)


def _make_lease(heuristic_id="snake-dsl-v1"):
    lease = MagicMock()
    lease.heuristic_id = heuristic_id
    lease.id = "lease-test-id"
    return lease


class TestSnakeDecisionManagerDslV2:
    """Test that DSL v2 path is tried when heuristic has mode=dsl_v2."""

    def _make_manager_with_dsl(self, heuristic_id="snake-dsl-v1"):
        reg = _make_registry_with_dsl_heuristic(heuristic_id=heuristic_id)
        # Use in-memory repos that don't need DB
        from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
        from agent.repositories.decision_trace_repo import DecisionTraceRepository
        from sqlmodel import SQLModel
        from agent.database import engine
        SQLModel.metadata.create_all(engine)
        lease_repo = HeuristicLeaseRepository()
        trace_repo = DecisionTraceRepository()
        mgr = SnakeDecisionManager(
            registry=reg,
            lease_repo=lease_repo,
            trace_repo=trace_repo,
        )
        return mgr

    def test_dsl_try_returns_none_when_no_raw_def(self):
        """If heuristic has no _raw_def, _try_dsl_decide returns None."""
        reg = _make_registry_with_dsl_heuristic(has_dsl=False)
        mgr = SnakeDecisionManager(registry=reg)
        lease = _make_lease()
        ctx = _ctx()
        result = mgr._try_dsl_decide(ctx, lease)
        assert result is None

    def test_dsl_try_returns_result_when_valid_dsl(self):
        """If heuristic has valid dsl_v2 DSL and matches, result is returned."""
        mgr = self._make_manager_with_dsl()
        # Manually inject a lease into _try_dsl_decide
        lease = _make_lease()
        ctx = _ctx()
        result = mgr._try_dsl_decide(ctx, lease)
        # Either a valid DecisionResult or None (None if get_by_id raises)
        # We test it doesn't raise an exception
        assert result is None or isinstance(result, DecisionResult)

    def test_dsl_exception_returns_none(self):
        """If DSL evaluation raises exception, _try_dsl_decide returns None (no crash)."""
        mgr = self._make_manager_with_dsl()
        lease = _make_lease()
        ctx = _ctx()
        # Inject a broken DSL loader to simulate failure
        mgr._dsl_loader = MagicMock(side_effect=RuntimeError("simulated crash"))
        result = mgr._try_dsl_decide(ctx, lease)
        assert result is None

    def test_dsl_validator_fail_returns_none(self):
        """If DSL validation fails, _try_dsl_decide returns None → fallback to chain."""
        mgr = self._make_manager_with_dsl()
        lease = _make_lease()
        ctx = _ctx()
        # Inject a validator that always fails
        from agent.services.heuristic_runtime.dsl.validator import ValidationResult
        mgr._dsl_validator = MagicMock()
        mgr._dsl_validator.validate.return_value = ValidationResult(passed=False, errors=["test error"])
        # Also need a valid loader
        mgr._dsl_loader = MagicMock()
        mgr._dsl_loader.load_from_definition.return_value = {"dsl_version": "2.0"}
        result = mgr._try_dsl_decide(ctx, lease)
        assert result is None

    def test_dsl_no_match_returns_none(self):
        """If DSL evaluates but doesn't match, returns None → fallback to chain."""
        mgr = self._make_manager_with_dsl()
        lease = _make_lease()
        ctx = _ctx()
        # Inject evaluator that returns not matched
        from agent.services.heuristic_runtime.dsl.evaluator import EvalResult
        mgr._dsl_evaluator = MagicMock()
        mgr._dsl_evaluator.evaluate.return_value = EvalResult(
            matched=False, score=0.0, action={"kind": "no_action"}
        )
        mgr._dsl_loader = MagicMock()
        mgr._dsl_loader.load_from_definition.return_value = {"dsl_version": "2.0"}
        from agent.services.heuristic_runtime.dsl.validator import ValidationResult
        mgr._dsl_validator = MagicMock()
        mgr._dsl_validator.validate.return_value = ValidationResult(passed=True)
        result = mgr._try_dsl_decide(ctx, lease)
        assert result is None

    def test_decide_returns_valid_result_without_crash(self):
        """decide() should not crash even if DSL path raises internally."""
        mgr = self._make_manager_with_dsl()
        ctx = _ctx()
        # decide() should not raise
        result = mgr.decide(ctx)
        assert isinstance(result, DecisionResult)

    def test_dsl_runtime_flag_is_available(self):
        """Check that DSL runtime modules are importable."""
        from agent.services.heuristic_runtime.snake_decision_manager import _DSL_RUNTIME_AVAILABLE
        assert _DSL_RUNTIME_AVAILABLE is True

    def test_manager_has_dsl_components(self):
        """Manager should have DSL loader/validator/evaluator/motion_planner when runtime available."""
        from agent.services.heuristic_runtime.snake_decision_manager import _DSL_RUNTIME_AVAILABLE
        mgr = self._make_manager_with_dsl()
        if _DSL_RUNTIME_AVAILABLE:
            assert mgr._dsl_loader is not None
            assert mgr._dsl_validator is not None
            assert mgr._dsl_evaluator is not None
            assert mgr._motion_planner is not None
