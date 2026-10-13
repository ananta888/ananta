"""Action Fusion: declarative, semantics-preserving fusion of safe tool calls (SEH-005).

Only consecutive actions that declare the **same** fusion group and are provably
idempotent and side-effect-free may be fused into one atomic operation. The fused
plan preserves the original action order (so it is semantically equivalent to the
unfused baseline), and a fused operation retries as a whole group — safe because
every member is idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ananta_contracts.harness_genome import content_digest


@dataclass(frozen=True, slots=True)
class ToolAction:
    action_id: str
    tool: str
    arguments: tuple[tuple[str, object], ...] = ()
    idempotent: bool = False
    side_effect_free: bool = False
    fusion_group: str | None = None
    cost_ms: float = 0.0

    def assert_valid(self) -> None:
        if not isinstance(self.action_id, str) or not self.action_id or len(self.action_id) > 200:
            raise ValueError("action_id_invalid")
        if not isinstance(self.tool, str) or not self.tool or len(self.tool) > 200:
            raise ValueError("action_tool_invalid")
        if self.fusion_group is not None and (not isinstance(self.fusion_group, str) or not self.fusion_group):
            raise ValueError("action_fusion_group_invalid")

    @property
    def fusable(self) -> bool:
        return self.fusion_group is not None and self.idempotent and self.side_effect_free

    def to_mapping(self) -> dict:
        return {"action_id": self.action_id, "tool": self.tool,
                "arguments": [[key, value] for key, value in self.arguments],
                "idempotent": self.idempotent, "side_effect_free": self.side_effect_free,
                "fusion_group": self.fusion_group, "cost_ms": self.cost_ms}


@dataclass(frozen=True, slots=True)
class FusedAction:
    fusion_id: str
    group: str
    members: tuple[ToolAction, ...]
    atomic: bool = True
    retry_policy: str = "whole_group"

    def to_mapping(self) -> dict:
        return {"fusion_id": self.fusion_id, "group": self.group,
                "members": [member.to_mapping() for member in self.members],
                "atomic": self.atomic, "retry_policy": self.retry_policy}


@dataclass(frozen=True, slots=True)
class FusionPlan:
    steps: tuple[ToolAction | FusedAction, ...]

    def flat_actions(self) -> tuple[ToolAction, ...]:
        flat: list[ToolAction] = []
        for step in self.steps:
            if isinstance(step, FusedAction):
                flat.extend(step.members)
            else:
                flat.append(step)
        return tuple(flat)

    def to_mapping(self) -> dict:
        return {"steps": [step.to_mapping() for step in self.steps]}


class ActionFuser:
    def plan(self, actions: Sequence[ToolAction]) -> FusionPlan:
        for action in actions:
            action.assert_valid()
        steps: list[ToolAction | FusedAction] = []
        index = 0
        total = len(actions)
        while index < total:
            action = actions[index]
            if not action.fusable:
                steps.append(action)
                index += 1
                continue
            group = action.fusion_group
            members = [action]
            cursor = index + 1
            while (cursor < total and actions[cursor].fusable and actions[cursor].fusion_group == group):
                members.append(actions[cursor])
                cursor += 1
            if len(members) == 1:
                steps.append(action)
            else:
                steps.append(FusedAction(fusion_id=content_digest([m.to_mapping() for m in members]), group=group,
                                         members=tuple(members)))
            index = cursor
        return FusionPlan(tuple(steps))

    @staticmethod
    def measure(plan: FusionPlan) -> dict:
        unfused_calls = len(plan.flat_actions())
        fused_calls = len(plan.steps)
        return {"unfused_calls": unfused_calls, "fused_calls": fused_calls,
                "saved_calls": unfused_calls - fused_calls,
                "unfused_cost_ms": sum(action.cost_ms for action in plan.flat_actions()),
                "fused_cost_ms": sum(action.cost_ms for action in plan.flat_actions())}

    @staticmethod
    def is_semantically_equivalent(original: Sequence[ToolAction], plan: FusionPlan) -> bool:
        return tuple(original) == plan.flat_actions()
