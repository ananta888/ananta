"""SEH-005: declarative, semantics-preserving action fusion."""

from __future__ import annotations

import pytest

from agent.services.harness_action_fusion import ActionFuser, FusedAction, ToolAction


def action(action_id, tool="repo.read_file_range", *, group="read_batch", idempotent=True, side_effect_free=True,
           cost_ms=10.0):
    return ToolAction(action_id=action_id, tool=tool, arguments=(("path", action_id),),
                      idempotent=idempotent, side_effect_free=side_effect_free, fusion_group=group, cost_ms=cost_ms)


def test_consecutive_safe_actions_in_the_same_group_are_fused():
    actions = [action("a"), action("b"), action("c")]
    plan = ActionFuser().plan(actions)
    assert len(plan.steps) == 1 and isinstance(plan.steps[0], FusedAction)
    assert [m.action_id for m in plan.steps[0].members] == ["a", "b", "c"]
    assert plan.steps[0].atomic and plan.steps[0].retry_policy == "whole_group"


def test_unsafe_or_other_group_actions_break_the_fusion_run():
    actions = [action("a"), action("b", group="other"), action("c", idempotent=False),
               action("d", side_effect_free=False), action("e"), action("f")]
    plan = ActionFuser().plan(actions)
    fused = [step for step in plan.steps if isinstance(step, FusedAction)]
    assert len(fused) == 1
    assert [m.action_id for m in fused[0].members] == ["e", "f"]
    singles = [step.action_id for step in plan.steps if isinstance(step, ToolAction)]
    assert singles == ["a", "b", "c", "d"]


def test_single_fusable_action_is_not_fused():
    plan = ActionFuser().plan([action("a")])
    assert isinstance(plan.steps[0], ToolAction)


def test_fused_plan_is_semantically_equivalent_to_the_baseline():
    actions = [action("a"), action("b"), action("c", group="other"), action("d"), action("e")]
    plan = ActionFuser().plan(actions)
    assert ActionFuser.is_semantically_equivalent(actions, plan)
    assert plan.flat_actions() == tuple(actions)


def test_measurement_reports_saved_calls():
    actions = [action("a"), action("b"), action("c"), action("d")]
    plan = ActionFuser().plan(actions)
    metrics = ActionFuser.measure(plan)
    assert metrics == {"unfused_calls": 4, "fused_calls": 1, "saved_calls": 3,
                       "unfused_cost_ms": 40.0, "fused_cost_ms": 40.0}


def test_invalid_action_is_rejected():
    with pytest.raises(ValueError, match="action_id_invalid"):
        ActionFuser().plan([ToolAction(action_id="", tool="x")])
    with pytest.raises(ValueError, match="action_tool_invalid"):
        ActionFuser().plan([ToolAction(action_id="a", tool="")])
    with pytest.raises(ValueError, match="action_fusion_group_invalid"):
        ActionFuser().plan([ToolAction(action_id="a", tool="t", fusion_group="")])
