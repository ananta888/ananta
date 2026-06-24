"""Artifact Dataflow Validator (VPDF-002) + Backend Graph Validator (VPAD-002).

Two levels:
  GraphValidator      — structural checks (dangling edges, cycles, isolated nodes)
  DataflowValidator   — I/O contract checks (required inputs satisfied, type mismatches)
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from agent.visual_process.models import (
    ArtifactRef,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
)
from agent.visual_process.task_kind_registry import (
    is_legacy_kind,
    suggested_replacement,
)


# ── Validation result ─────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    severity: str           # "error" | "warning" | "info"
    code: str
    message: str
    step_id: str | None = None
    edge_id: str | None = None
    artifact_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "step_id": self.step_id,
            "edge_id": self.edge_id,
            "artifact_name": self.artifact_name,
        }


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error_count": len(self.errors()),
            "warning_count": len(self.warnings()),
            "issues": [i.as_dict() for i in self.issues],
        }


# ── Graph Validator (VPAD-002) ────────────────────────────────────────────────

class GraphValidator:
    """Validates structural integrity of a VisualProcessGraph."""

    def validate(self, graph: VisualProcessGraph) -> ValidationResult:
        issues: list[ValidationIssue] = []
        step_ids = set(graph.step_ids())

        # Must have at least one step
        if not graph.steps:
            issues.append(ValidationIssue("error", "empty_graph", "Graph has no steps"))
            return ValidationResult(valid=False, issues=issues)

        # Name required
        if not graph.name.strip():
            issues.append(ValidationIssue("error", "missing_name", "Graph name is required"))

        # Dangling edge endpoints
        for edge in graph.edges:
            if edge.source not in step_ids:
                issues.append(ValidationIssue(
                    "error", "dangling_edge_source",
                    f"Edge source '{edge.source}' not found",
                    edge_id=edge.id,
                ))
            if edge.target not in step_ids:
                issues.append(ValidationIssue(
                    "error", "dangling_edge_target",
                    f"Edge target '{edge.target}' not found",
                    edge_id=edge.id,
                ))
            if edge.source == edge.target and not edge.is_back_edge():
                issues.append(ValidationIssue(
                    "error", "self_loop_forward",
                    f"Self-loop on step '{edge.source}' must be a back_edge",
                    edge_id=edge.id,
                ))

        # Unreachable steps (no path from entry via forward edges)
        reachable = self._reachable(graph)
        for step in graph.steps:
            if step.id not in reachable:
                issues.append(ValidationIssue(
                    "warning", "unreachable_step",
                    f"Step '{step.label}' ({step.id}) is not reachable from any entry step",
                    step_id=step.id,
                ))

        # Isolated steps (no edges at all in a graph that has edges)
        if graph.edges:
            connected = {e.source for e in graph.edges} | {e.target for e in graph.edges}
            for step in graph.steps:
                if step.id not in connected:
                    issues.append(ValidationIssue(
                        "warning", "unreachable_step",
                        f"Step '{step.label}' ({step.id}) has no connections",
                        step_id=step.id,
                    ))

        # Cycles (excluding back_edges)
        if graph.has_cycles():
            issues.append(ValidationIssue(
                "error", "cycle_detected",
                "Graph has a forward-edge cycle. Use back_edge for intentional loops.",
            ))

        # Loop policy validation for back_edges
        for edge in graph.edges:
            if edge.is_back_edge():
                lp = edge.condition.loop_policy
                if lp and lp.kind != "none" and lp.max_iterations > 20:
                    issues.append(ValidationIssue(
                        "warning", "high_iteration_count",
                        f"Back edge loop max_iterations={lp.max_iterations} is very high",
                        edge_id=edge.id,
                    ))

        # Expression syntax check (VPEXPR-001)
        for edge in graph.edges:
            if edge.condition.kind == "expression":
                expr = edge.condition.expression or ""
                try:
                    ast.parse(expr, mode="eval")
                except SyntaxError as exc:
                    issues.append(ValidationIssue(
                        "warning", "expression_syntax_error",
                        f"Edge expression syntax error: {exc}",
                        edge_id=edge.id,
                    ))

        # Fork/join single-outgoing/incoming warnings (VPCF-001, VPPAR-001)
        outgoing: dict[str, int] = {}
        incoming: dict[str, int] = {}
        for edge in graph.edges:
            if not edge.is_back_edge():
                outgoing[edge.source] = outgoing.get(edge.source, 0) + 1
                incoming[edge.target] = incoming.get(edge.target, 0) + 1

        for step in graph.steps:
            if step.kind in ("fork", "parallel"):
                if outgoing.get(step.id, 0) <= 1:
                    code = "fork_single_outgoing" if step.kind == "fork" else "parallel_single_outgoing"
                    issues.append(ValidationIssue(
                        "warning", code,
                        f"Step '{step.label}' (kind={step.kind}) has only one outgoing edge; "
                        "parallel/fork semantics require at least two branches",
                        step_id=step.id,
                    ))
            if step.kind == "join":
                if incoming.get(step.id, 0) <= 1:
                    issues.append(ValidationIssue(
                        "warning", "join_single_incoming",
                        f"Step '{step.label}' (kind=join) has only one incoming edge; "
                        "join semantics require at least two branches",
                        step_id=step.id,
                    ))
            # approval kind implies gate=true
            if step.kind == "approval" and not step.gate:
                issues.append(ValidationIssue(
                    "info", "approval_gate_missing",
                    f"Step '{step.label}' (kind=approval) should have gate=true for explicit approval",
                    step_id=step.id,
                ))

        # Legacy kind warnings (VPWRK-001)
        for step in graph.steps:
            if is_legacy_kind(step.kind):
                replacement = suggested_replacement(step.kind)
                issues.append(ValidationIssue(
                    "warning", "legacy_task_kind",
                    f"Step '{step.label}' uses legacy kind '{step.kind}'; "
                    f"consider using '{replacement}' instead",
                    step_id=step.id,
                ))

        # high_risk_no_gate (VPEVOL-002)
        for step in graph.steps:
            if step.kind in ("evolve_project",) and step.metadata.get("apply_allowed") and not step.gate:
                issues.append(ValidationIssue(
                    "warning", "high_risk_no_gate",
                    f"Step '{step.label}' has apply_allowed=true but gate=false; "
                    "recommend setting gate=true for evolve_project steps that apply changes",
                    step_id=step.id,
                ))

        errors = [i for i in issues if i.severity == "error"]
        return ValidationResult(valid=len(errors) == 0, issues=issues)

    @staticmethod
    def _reachable(graph: VisualProcessGraph) -> set[str]:
        entry = {s.id for s in graph.entry_steps()}
        forward = {(e.source, e.target) for e in graph.edges if not e.is_back_edge()}
        visited: set[str] = set()
        queue = list(entry)
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            for src, tgt in forward:
                if src == node and tgt not in visited:
                    queue.append(tgt)
        return visited


# ── Dataflow Validator (VPDF-002) ─────────────────────────────────────────────

class DataflowValidator:
    """Validates I/O contracts across the graph.

    Checks that every required input of a step is satisfied by the output
    of a predecessor step (or is marked optional).  Also checks ArtifactKind
    compatibility (VPTYPE-001).
    """

    def validate(self, graph: VisualProcessGraph) -> ValidationResult:
        issues: list[ValidationIssue] = []

        # Build available artifacts per step (name → kind, transitively from predecessors)
        available = self._compute_available(graph)

        for step in graph.steps:
            avail_for_step = available.get(step.id, {})
            for inp in step.io.required_inputs():
                if inp.name not in avail_for_step:
                    issues.append(ValidationIssue(
                        "error", "unsatisfied_input",
                        f"Step '{step.label}' requires input '{inp.name}' "
                        f"({inp.kind}) but no predecessor produces it",
                        step_id=step.id,
                        artifact_name=inp.name,
                    ))
                else:
                    # Kind mismatch check (VPTYPE-001)
                    produced_kind = avail_for_step[inp.name]
                    if produced_kind != inp.kind:
                        issues.append(ValidationIssue(
                            "warning", "artifact_kind_mismatch",
                            f"Step '{step.label}' expects '{inp.name}' as kind='{inp.kind}' "
                            f"but predecessor produces it as kind='{produced_kind}'",
                            step_id=step.id,
                            artifact_name=inp.name,
                        ))

        # Warn on unused outputs
        consumed = self._consumed_artifacts(graph)
        for step in graph.steps:
            for out in step.io.outputs:
                key = f"{step.id}:{out.name}"
                if key not in consumed:
                    issues.append(ValidationIssue(
                        "info", "unused_output",
                        f"Step '{step.label}' produces '{out.name}' but nothing consumes it",
                        step_id=step.id,
                        artifact_name=out.name,
                    ))

        # rerank should have 2 inputs (VPRAG-002)
        for step in graph.steps:
            if step.kind == "rerank" and len(step.io.inputs) < 2:
                issues.append(ValidationIssue(
                    "warning", "rerank_missing_input",
                    f"Step '{step.label}' (kind=rerank) should have two inputs: "
                    "query (text) and candidates (dataset)",
                    step_id=step.id,
                ))
            if step.kind in ("vector_encode", "turboquant_encode") and any(
                o.kind not in ("vector", "unknown") for o in step.io.outputs
            ):
                pass  # informational, not an error

        errors = [i for i in issues if i.severity == "error"]
        return ValidationResult(valid=len(errors) == 0, issues=issues)

    def _compute_available(self, graph: VisualProcessGraph) -> dict[str, dict[str, str]]:
        """For each step, the dict of artifact name → kind available from predecessors."""
        forward = {(e.source, e.target) for e in graph.edges if not e.is_back_edge()}
        step_outputs: dict[str, dict[str, str]] = {
            s.id: {a.name: a.kind for a in s.io.outputs} for s in graph.steps
        }
        available: dict[str, dict[str, str]] = {}
        visited: set[str] = set()

        def walk(sid: str, inherited: dict[str, str]) -> None:
            if sid in visited:
                return
            visited.add(sid)
            avail = {**inherited, **step_outputs.get(sid, {})}
            available[sid] = avail
            for src, tgt in forward:
                if src == sid:
                    walk(tgt, avail)

        for entry in graph.entry_steps():
            walk(entry.id, {})
        return available

    def _consumed_artifacts(self, graph: VisualProcessGraph) -> set[str]:
        consumed: set[str] = set()
        forward = {(e.source, e.target) for e in graph.edges if not e.is_back_edge()}
        step_index = {s.id: s for s in graph.steps}
        for edge_src, edge_tgt in forward:
            tgt_step = step_index.get(edge_tgt)
            if tgt_step:
                for inp in tgt_step.io.inputs:
                    consumed.add(f"{edge_src}:{inp.name}")
        return consumed


# ── Combined validator ────────────────────────────────────────────────────────

class VisualProcessValidator:
    """Runs both GraphValidator and DataflowValidator and merges results."""

    def __init__(self) -> None:
        self._graph = GraphValidator()
        self._dataflow = DataflowValidator()

    def validate(self, graph: VisualProcessGraph) -> ValidationResult:
        gr = self._graph.validate(graph)
        if not gr.valid:
            # Don't run dataflow if graph is structurally broken
            return gr
        dr = self._dataflow.validate(graph)
        all_issues = gr.issues + dr.issues
        errors = [i for i in all_issues if i.severity == "error"]
        return ValidationResult(valid=len(errors) == 0, issues=all_issues)
