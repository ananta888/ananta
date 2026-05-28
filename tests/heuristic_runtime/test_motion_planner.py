"""Tests für Motion Planner (T05.03)."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.motion_planner import MotionPlanner, MotionPlan


def _action(kind="smooth_follow", target_cell=None, target_bbox=None, max_step=2, min_distance=0, confidence=0.8):
    a = {"kind": kind, "max_step": max_step, "min_distance": min_distance, "confidence": confidence}
    if target_cell is not None:
        a["target_cell"] = target_cell
    if target_bbox is not None:
        a["target_bbox"] = target_bbox
    return a


class TestMotionPlanner:
    def setup_method(self):
        self.planner = MotionPlanner()

    def test_no_action_returns_zero_motion(self):
        plan = self.planner.plan({"kind": "no_action"}, (10, 10))
        assert plan.dx == 0
        assert plan.dy == 0
        assert plan.strategy == "no_action"

    def test_horizontal_movement_right(self):
        plan = self.planner.plan(_action(target_cell={"x": 20, "y": 10}), (10, 10))
        assert plan.dx == 1
        assert plan.dy == 0

    def test_horizontal_movement_left(self):
        plan = self.planner.plan(_action(target_cell={"x": 0, "y": 10}), (10, 10))
        assert plan.dx == -1
        assert plan.dy == 0

    def test_vertical_movement_down(self):
        plan = self.planner.plan(_action(target_cell={"x": 10, "y": 20}), (10, 10))
        assert plan.dx == 0
        assert plan.dy == 1

    def test_vertical_movement_up(self):
        plan = self.planner.plan(_action(target_cell={"x": 10, "y": 0}), (10, 10))
        assert plan.dx == 0
        assert plan.dy == -1

    def test_diagonal_prefers_x_axis_when_equal(self):
        # raw_dx == raw_dy → prefer x axis
        plan = self.planner.plan(_action(target_cell={"x": 15, "y": 15}), (10, 10))
        # |raw_dx| = |raw_dy| = 5 → x axis preferred → dy = 0
        assert plan.dy == 0
        assert plan.dx != 0

    def test_diagonal_prefers_y_axis_when_larger(self):
        # raw_dy > raw_dx → prefer y axis
        plan = self.planner.plan(_action(target_cell={"x": 11, "y": 20}), (10, 10))
        # raw_dx=1, raw_dy=10 → y axis preferred → dx = 0
        assert plan.dx == 0
        assert plan.dy != 0

    def test_already_near_returns_zero(self):
        # min_distance=5, distance=3 → already near
        plan = self.planner.plan(_action(target_cell={"x": 12, "y": 10}, min_distance=5), (10, 10))
        assert plan.dx == 0
        assert plan.dy == 0
        assert plan.strategy == "already_near"

    def test_max_step_respected(self):
        plan = self.planner.plan(_action(target_cell={"x": 50, "y": 10}, max_step=1), (10, 10))
        assert abs(plan.dx) <= 1
        assert abs(plan.dy) <= 1

    def test_max_step_default_two(self):
        plan = self.planner.plan(_action(target_cell={"x": 50, "y": 10}), (10, 10))
        # max_step=2, but we only move 1 step along chosen axis
        assert abs(plan.dx) <= 2
        assert abs(plan.dy) <= 2

    def test_no_jump_outside_board_right(self):
        # Snake at x=119, board_w=120, target at x=150 → would go outside
        plan = self.planner.plan(_action(target_cell={"x": 150, "y": 10}), (119, 10), board_w=120)
        assert plan.dx == 0
        assert plan.clamped is True

    def test_no_jump_outside_board_left(self):
        # Snake at x=0, target at x=-10 → would go outside
        plan = self.planner.plan(_action(target_cell={"x": -10, "y": 10}), (0, 10), board_w=120)
        assert plan.dx == 0
        assert plan.clamped is True

    def test_no_jump_outside_board_top(self):
        plan = self.planner.plan(_action(target_cell={"x": 10, "y": -5}), (10, 0), board_h=32)
        assert plan.dy == 0
        assert plan.clamped is True

    def test_no_jump_outside_board_bottom(self):
        plan = self.planner.plan(_action(target_cell={"x": 10, "y": 50}), (10, 31), board_h=32)
        assert plan.dy == 0
        assert plan.clamped is True

    def test_bbox_target_computes_center(self):
        # bbox at x=10, y=10, w=4, h=4 → center (12, 12)
        action = _action(kind="smooth_follow", target_bbox={"x": 10, "y": 10, "w": 4, "h": 4})
        plan = self.planner.plan(action, (0, 0))
        # Target is (12, 12) from (0, 0) → dx=1 or dy=1 (equal raw, prefer x)
        assert plan.dx != 0 or plan.dy != 0

    def test_fast_target_strategy_name(self):
        plan = self.planner.plan({"kind": "fast_target", "target_cell": {"x": 20, "y": 10}}, (10, 10))
        assert plan.strategy == "fast_target"

    def test_smooth_follow_strategy_name(self):
        plan = self.planner.plan(_action(target_cell={"x": 20, "y": 10}), (10, 10))
        assert plan.strategy == "smooth_follow"

    def test_no_target_returns_straight(self):
        plan = self.planner.plan({"kind": "smooth_follow"}, (10, 10))
        assert plan.dx == 1
        assert plan.dy == 0

    def test_plan_is_deterministic(self):
        action = _action(target_cell={"x": 30, "y": 15})
        p1 = self.planner.plan(action, (10, 10))
        p2 = self.planner.plan(action, (10, 10))
        assert p1.dx == p2.dx
        assert p1.dy == p2.dy
