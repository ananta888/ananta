"""Context Assembly per Step (VPDF-003).

Derives the execution context for a single step from its I/O contract
and the artifacts produced by predecessor steps.

The assembled context is a dict that can be passed directly into
WorkerExecutionContextContract.context or used by a planner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.visual_process.models import VisualProcessGraph, VisualProcessStep


@dataclass
class StepContext:
    """Assembled execution context for one step."""
    step_id: str
    step_label: str
    step_kind: str
    role: str | None
    # Resolved artifact values: name → content/path/reference
    inputs: dict[str, Any] = field(default_factory=dict)
    # Names of expected output artifacts
    expected_outputs: list[str] = field(default_factory=list)
    # Allowed tool names derived from the skill profile (if set)
    allowed_tools: list[str] = field(default_factory=list)
    # Policy hints inherited from step definition
    policy_hints: list[str] = field(default_factory=list)
    # Predecessor step IDs (for audit / lineage)
    predecessor_step_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_label": self.step_label,
            "step_kind": self.step_kind,
            "role": self.role,
            "inputs": self.inputs,
            "expected_outputs": self.expected_outputs,
            "allowed_tools": self.allowed_tools,
            "policy_hints": self.policy_hints,
            "predecessor_step_ids": self.predecessor_step_ids,
        }


class StepContextAssembler:
    """Assembles execution context for a step from available runtime artifacts.

    Usage::

        assembler = StepContextAssembler(graph)
        ctx = assembler.assemble(step_id, runtime_artifacts={"file.txt": "/tmp/out.txt"})
    """

    def __init__(
        self,
        graph: VisualProcessGraph,
        skill_profiles: dict[str, Any] | None = None,
    ) -> None:
        self._graph = graph
        self._profiles = skill_profiles or {}

    def assemble(
        self,
        step_id: str,
        runtime_artifacts: dict[str, Any] | None = None,
    ) -> StepContext:
        """Build context for *step_id* using *runtime_artifacts* as the available pool."""
        step = self._graph.step_by_id(step_id)
        if step is None:
            raise ValueError(f"Step '{step_id}' not found in graph '{self._graph.id}'")

        pool = dict(runtime_artifacts or {})
        predecessors = self._predecessor_ids(step_id)

        # Resolve declared inputs from the artifact pool
        resolved_inputs: dict[str, Any] = {}
        for inp in step.io.inputs:
            if inp.name in pool:
                resolved_inputs[inp.name] = pool[inp.name]
            elif inp.required:
                resolved_inputs[inp.name] = None  # will be flagged by validator

        # Expected outputs
        expected_outputs = step.io.output_names()

        # Allowed tools from skill profile
        allowed_tools: list[str] = []
        if step.agent_skill_profile_id and step.agent_skill_profile_id in self._profiles:
            profile = self._profiles[step.agent_skill_profile_id]
            allowed_tools = list(profile.get("allowed_tools") or [])

        return StepContext(
            step_id=step.id,
            step_label=step.label,
            step_kind=step.kind,
            role=step.role,
            inputs=resolved_inputs,
            expected_outputs=expected_outputs,
            allowed_tools=allowed_tools,
            policy_hints=list(step.policy_hints),
            predecessor_step_ids=predecessors,
        )

    def _predecessor_ids(self, step_id: str) -> list[str]:
        return [e.source for e in self._graph.edges_to(step_id) if not e.is_back_edge()]
