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
    ModelRoutingConfig,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
)
from agent.visual_process.task_kind_registry import (
    get_task_kind_info,
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

        # high_risk_no_gate — evolve_project with apply_allowed OR evolution_apply without gate
        for step in graph.steps:
            if step.kind == "evolve_project" and step.metadata.get("apply_allowed") and not step.gate:
                issues.append(ValidationIssue(
                    "warning", "high_risk_no_gate",
                    f"Step '{step.label}' has apply_allowed=true but gate=false; "
                    "recommend setting gate=true for evolve_project steps that apply changes",
                    step_id=step.id,
                ))
            if step.kind == "evolution_apply" and not step.gate:
                issues.append(ValidationIssue(
                    "error", "evolution_apply_requires_gate",
                    f"Step '{step.label}' (kind=evolution_apply) must have gate=true — "
                    "EvolutionService.apply() modifies the codebase via MutationGateService",
                    step_id=step.id,
                ))

        # turboquant_mse: funktionierender experimenteller Encoder — nur Hinweis, kein Warning
        for step in graph.steps:
            if step.kind == "turboquant_mse":
                issues.append(ValidationIssue(
                    "info", "turboquant_mse_experimental",
                    f"Step '{step.label}' verwendet TurboQuantMseEncoder (TQ-012): "
                    "sign-rotation + 4-bit scalar quant, encode/decode funktioniert. "
                    "Experimentell (kein Produktions-Codebook). TQ-013 ProdStub ist ein separater, "
                    "unbenutzter Stub und betrifft diesen Step nicht.",
                    step_id=step.id,
                ))

        # domain_cluster accuracy note
        for step in graph.steps:
            if step.kind == "domain_cluster":
                issues.append(ValidationIssue(
                    "info", "domain_cluster_deterministic",
                    f"Step '{step.label}': domain_cluster uses deterministic signal-based clustering "
                    "(path/package/graph cohesion). Leiden/Louvain/KMeans are NOT implemented in production.",
                    step_id=step.id,
                ))

        # embed_api requires provider config
        for step in graph.steps:
            if step.kind == "embed_api":
                provider = step.metadata.get("provider", "")
                if provider in ("openai", "openai_compatible") and not step.metadata.get("base_url"):
                    issues.append(ValidationIssue(
                        "warning", "embed_api_missing_base_url",
                        f"Step '{step.label}' (kind=embed_api) uses provider='{provider}' "
                        "but no base_url is configured in metadata",
                        step_id=step.id,
                    ))

        # codecompass_index_build should precede vector/fts search in same graph
        cc_kinds = {"codecompass_vector_search", "codecompass_fts_search", "codecompass_graph_expand"}
        cc_search_steps = [s for s in graph.steps if s.kind in cc_kinds]
        if cc_search_steps and not any(s.kind == "codecompass_index_build" for s in graph.steps):
            for step in cc_search_steps:
                issues.append(ValidationIssue(
                    "info", "codecompass_no_index_step",
                    f"Step '{step.label}' (kind={step.kind}) uses CodeCompass but no "
                    "codecompass_index_build step is present. Index must exist beforehand.",
                    step_id=step.id,
                ))

        # evolution_validate should follow evolution_analyze (useful order hint)
        ev_apply = [s for s in graph.steps if s.kind == "evolution_apply"]
        ev_validate = [s for s in graph.steps if s.kind == "evolution_validate"]
        if ev_apply and not ev_validate:
            for step in ev_apply:
                issues.append(ValidationIssue(
                    "info", "evolution_apply_without_validate",
                    f"Step '{step.label}' (kind=evolution_apply) without a preceding "
                    "evolution_validate step. Recommend: analyze → validate → (gate) → apply",
                    step_id=step.id,
                ))

        # VPRT-003: Runtime-Truth consistency checks
        self._check_runtime_truth(graph, issues)
        self._check_model_routing(graph, issues)

        errors = [i for i in issues if i.severity == "error"]
        return ValidationResult(valid=len(errors) == 0, issues=issues)

    @staticmethod
    def _check_runtime_truth(graph: VisualProcessGraph, issues: list[ValidationIssue]) -> None:
        """VPRT-003: Validate consistency between step kinds, policy hints, and runtime truth."""
        non_executable_states = {"registered_only", "not_implemented", "design_only", "exposed_not_wired"}
        for step in graph.steps:
            info = get_task_kind_info(step.kind)
            if info is None:
                continue

            impl_state: str = info.get("implementation_state", "unknown")
            impl_status: str = info.get("implementation_status", "unknown")
            backend: str = info.get("backend_service", "")
            uses_network: bool = bool(info.get("uses_network", False))

            # Steps with registered_only / not_implemented in an executable graph
            if impl_state in non_executable_states and not info.get("dispatch_capable", False):
                issues.append(ValidationIssue(
                    "warning", "step_not_executable",
                    f"Step '{step.label}' (kind={step.kind}) hat implementation_state='{impl_state}'. "
                    f"Der Step ist im Editor sichtbar, aber ohne VP-Execution-Adapter nicht ausführbar. "
                    f"Backend: {backend}",
                    step_id=step.id,
                ))

            # requires_approval in registry but no gate on step
            if info.get("requires_approval", False) and not step.gate:
                # evolution_apply already has a hard error, skip duplicate
                if step.kind != "evolution_apply":
                    issues.append(ValidationIssue(
                        "warning", "requires_approval_no_gate",
                        f"Step '{step.label}' (kind={step.kind}) erfordert laut Registry "
                        "zwingend Approval (requires_approval=true), hat aber gate=false.",
                        step_id=step.id,
                    ))

    @staticmethod
    def _check_model_routing(graph: VisualProcessGraph, issues: list[ValidationIssue]) -> None:
        known_profiles: set[str] | None = None
        try:
            from agent.services.model_invocation_service import ModelInvocationService
            resolver = ModelInvocationService._get_resolver()
            if resolver is not None:
                known_profiles = set(resolver._by_id.keys())
        except Exception:
            known_profiles = None

        try:
            ModelRoutingConfig.from_metadata(graph.metadata)
        except Exception as exc:
            issues.append(ValidationIssue(
                "warning",
                "model_routing_invalid",
                f"Graph model_routing is invalid: {exc}",
            ))

        for step in graph.steps:
            try:
                routing = ModelRoutingConfig.from_metadata(step.metadata)
            except Exception as exc:
                issues.append(ValidationIssue(
                    "warning",
                    "model_routing_invalid",
                    f"Step '{step.label}' model_routing is invalid: {exc}",
                    step_id=step.id,
                ))
                continue
            if routing is None:
                continue
            if routing.preferred_profile_id and known_profiles is not None and routing.preferred_profile_id not in known_profiles:
                issues.append(ValidationIssue(
                    "warning",
                    "model_profile_missing",
                    f"Step '{step.label}' references unknown model profile '{routing.preferred_profile_id}'",
                    step_id=step.id,
                ))
            if routing.allow_cloud and not step.gate and (
                routing.require_approval_on_cloud_escalation
                or routing.require_approval_above_estimated_cost is not None
            ):
                issues.append(ValidationIssue(
                    "info",
                    "model_cloud_gate_missing",
                    f"Step '{step.label}' may require approval for cloud or cost escalation but gate=false.",
                    step_id=step.id,
                ))

            # uses_network=true — inform user
            if uses_network:
                issues.append(ValidationIssue(
                    "info", "step_uses_network",
                    f"Step '{step.label}' (kind={step.kind}) macht Netzwerk-Anfragen "
                    f"(uses_network=true, backend={backend}). "
                    "Stell sicher, dass Netzwerk-Egress in deiner Umgebung erlaubt ist.",
                    step_id=step.id,
                ))

            # stub/not_implemented status — hard warning
            if impl_status in ("stub", "not_implemented"):
                issues.append(ValidationIssue(
                    "warning", "step_is_stub",
                    f"Step '{step.label}' (kind={step.kind}) hat implementation_status='{impl_status}'. "
                    "Dieser Step ist ein Stub (NotImplementedError) und nicht ausführbar.",
                    step_id=step.id,
                ))

            # high/critical risk without approval gate
            risk = info.get("risk_level", "none")
            if risk in ("high", "critical") and not step.gate and step.kind not in ("shell_execute", "command_execute", "run_tests", "patch_apply", "script", "git_op"):
                issues.append(ValidationIssue(
                    "info", "high_risk_step_no_gate",
                    f"Step '{step.label}' (kind={step.kind}) hat risk_level='{risk}' aber gate=false. "
                    "Erwäge gate=true für kritische Steps.",
                    step_id=step.id,
                ))

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

        # turboquant_mse: output should declare kind="vector"
        for step in graph.steps:
            if step.kind == "turboquant_mse" and step.io.outputs:
                non_vector = [o for o in step.io.outputs if o.kind not in ("vector", "dataset", "unknown")]
                if non_vector:
                    issues.append(ValidationIssue(
                        "info", "turboquant_output_kind",
                        f"Step '{step.label}': turboquant_mse outputs should use kind='vector' or 'dataset'",
                        step_id=step.id,
                    ))

        # codecompass_vector_search / fts_search: should output dataset
        for step in graph.steps:
            if step.kind in ("codecompass_vector_search", "codecompass_fts_search") and step.io.outputs:
                non_dataset = [o for o in step.io.outputs if o.kind not in ("dataset", "json", "unknown")]
                if non_dataset:
                    issues.append(ValidationIssue(
                        "info", "codecompass_search_output_kind",
                        f"Step '{step.label}': CodeCompass search outputs should use kind='dataset' or 'json'",
                        step_id=step.id,
                    ))

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
