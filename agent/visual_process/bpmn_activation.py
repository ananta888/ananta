"""Bounded Hub compilation into the existing DAG; no runtime or task queue.

Only closed, source-bound intermediate nodes supplied by the parent BPMN
compiler belong here. XML recognition/admission stays with that compiler.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.execution_plan import ExecutionBudget, ExecutionEdge, ExecutionNode, ExecutionPlan
from agent.visual_process.bpmn_activation_contracts import (
    ACTIVATION_KEY,
    ORIGIN_KEY,
    SCHEMA,
    ActivationExpansion,
    ActivationLimits,
    BpmnActivationError,
    DefinitionPin,
    DefinitionResolver,
    component_sha256,
)
from agent.visual_process.bpmn_activation_rebinding import rebind_condition

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_EFFECTS = {"none": 0, "read": 1, "idempotent_write": 2, "non_idempotent_write": 3}


@dataclass(frozen=True)
class _Fragment:
    entry: str
    exit: str
    results: dict[str, str]
    edges: dict[str, str]


class BpmnActivationCompiler:
    """Reusable, stateless facade. Every compile owns its snapshot and counters."""

    def __init__(self, definitions: DefinitionResolver, *, limits: ActivationLimits = ActivationLimits()):
        self._definitions = definitions
        self._limits = limits

    def compile(self, reference: DefinitionPin, *, tenant_id: str, policy_version: str) -> ActivationExpansion:
        return _Expansion(self._definitions, self._limits, reference, tenant_id, policy_version).compile()


class _Expansion:
    """One bounded expansion transaction, with no externally visible mutation."""

    def __init__(self, definitions, limits, root, tenant_id, policy_version):
        self.definitions, self.limits, self.root = definitions, limits, root
        self.tenant_id, self.policy_version = tenant_id, policy_version
        self.snapshots = {}
        self.nodes, self.edges, self.gates = [], [], []
        self.edge_origins = {}
        self.blockers = {}

    def compile(self):
        component = self._resolve(self.root, self.root.component_id)
        self._region(
            self.root,
            (),
            (),
            component.plan.budget,
            set(component.allowed_tools),
            set(component.plan.capabilities),
            "non_idempotent_write",
        )
        manifest = {
            "schema": SCHEMA,
            "definition": self.root.to_dict(),
            "limits": asdict(self.limits),
            "definitions": [
                pin.to_dict() for pin in sorted(self.snapshots, key=lambda p: (p.component_id, p.version, p.sha256))
            ],
            "edge_origins": self.edge_origins,
            "blockers": [issue.to_dict() for issue in self.blockers.values()],
        }
        plan = replace(
            component.plan,
            nodes=tuple(self.nodes),
            edges=tuple(self.edges),
            gates=tuple(self.gates),
            metadata={**deepcopy(component.plan.metadata), "bpmn_activation_expansion": manifest},
        )
        # Normalize through the shared wire contract before admission hashing
        # (e.g. dataclass timeout=20 must round-trip as wire timeout=20.0).
        plan = ExecutionPlan.from_mapping(plan.to_dict(include_hash=False))
        return ActivationExpansion(plan, tuple(self.blockers.values()))

    def _resolve(self, pin, owner):
        if pin in self.snapshots:
            return self.snapshots[pin]
        try:
            candidate = self.definitions.resolve(pin.component_id, pin.version)
        except (KeyError, ValueError) as exc:
            raise BpmnActivationError("bpmn_definition_missing", owner, pin.component_id) from exc
        # The shared registry intentionally permits compatible-version fallback.
        # BPMN calls do not: the returned version must be the exact pinned one.
        if (candidate.component_id, candidate.version) != (pin.component_id, pin.version):
            raise BpmnActivationError("bpmn_definition_version_drift", owner)
        snapshot = deepcopy(candidate)
        if component_sha256(snapshot) != pin.sha256:
            raise BpmnActivationError("bpmn_definition_hash_drift", owner)
        if snapshot.plan.tenant_id != self.tenant_id or snapshot.policy_version != self.policy_version:
            raise BpmnActivationError("bpmn_definition_authority_mismatch", owner)
        if (
            len(snapshot.plan.nodes) > self.limits.max_nodes
            or len(snapshot.plan.edges) > self.limits.max_edges
            or len(snapshot.plan.gates) > self.limits.max_nodes
        ):
            raise BpmnActivationError("bpmn_activation_expansion_budget", owner)
        snapshot.assert_valid()
        issues = snapshot.plan.validate()
        if issues:
            raise BpmnActivationError("bpmn_activation_invalid_dag", owner, ",".join(issue.code for issue in issues))
        if (
            snapshot.plan.artifacts
            or snapshot.input_artifacts
            or snapshot.output_artifacts
            or snapshot.artifact_contract
        ):
            raise BpmnActivationError("bpmn_activation_artifact_mapping_unsupported", owner)
        if snapshot.input_schema != {"type": "object"} or snapshot.output_schema != {"type": "object"}:
            raise BpmnActivationError("bpmn_activation_variable_scope_unsupported", owner)
        self.snapshots[pin] = snapshot
        return snapshot

    def _region(self, pin, path, stack, ceiling, tools, capabilities, effect):
        owner = path[-1]["element_id"] if path else pin.component_id
        key = (pin.component_id, pin.version)
        if key in stack:
            raise BpmnActivationError("bpmn_activation_recursion_unsupported", owner)
        if len(stack) > self.limits.max_depth:
            raise BpmnActivationError("bpmn_activation_depth_exceeded", owner)
        component = self._resolve(pin, owner)
        plan = component.plan
        if set(component.allowed_tools) - tools or set(plan.capabilities) - capabilities:
            raise BpmnActivationError("bpmn_activation_authority_escalation", owner)
        self._budget(plan.budget, ceiling, owner)
        entry, exit = self._boundary(plan, owner)
        node_ids = {node.node_id: self._identity(pin, path, "node", node.node_id) for node in plan.nodes}
        edge_ids = {edge.edge_id: self._identity(pin, path, "edge", edge.edge_id) for edge in plan.edges}
        source_edges = {edge.edge_id: edge for edge in plan.edges}
        result_ids = {
            node.node_id: node_ids[node.node_id] for node in plan.nodes if node.node_type != "bpmn_activation"
        }
        gate_ids = {gate.gate_id: self._identity(pin, path, "gate", gate.gate_id) for gate in plan.gates}
        self.gates.extend(replace(gate, gate_id=gate_ids[gate.gate_id]) for gate in plan.gates)
        emitted_start = len(self.nodes)
        fragments = {}
        for node in plan.nodes:
            budget = node.budget or plan.budget
            self._budget(budget, plan.budget, node.node_id)
            if set(node.allowed_tools) - tools or _EFFECTS[node.side_effect_class] > _EFFECTS[effect]:
                raise BpmnActivationError("bpmn_activation_authority_escalation", node.node_id)
            if ORIGIN_KEY in node.metadata:
                raise BpmnActivationError("bpmn_activation_origin_reserved", node.node_id)
            if node.node_type == "bpmn_activation":
                fragments[node.node_id] = self._activation(node, pin, path, (*stack, key), budget)
                continue
            if node.node_type not in {"task", "bpmn_control"} or ACTIVATION_KEY in node.metadata:
                raise BpmnActivationError("bpmn_activation_node_unsupported", node.node_id)
            if node.metadata.get("failure_policy", "fail") != "fail":
                raise BpmnActivationError("bpmn_activation_failure_policy_unsupported", node.node_id)
            metadata = deepcopy(node.metadata)
            if node.node_type == "bpmn_control":
                control = metadata["bpmn_control"]
                for edge in control["outgoing"]:
                    source_edge = source_edges.get(edge["id"])
                    if source_edge is None or (source_edge.source, source_edge.target) != (
                        node.node_id,
                        edge["target"],
                    ):
                        raise BpmnActivationError("bpmn_activation_control_edge_mismatch", node.node_id)
                    edge.update(
                        id=edge_ids[edge["id"]],
                        target=node_ids[edge["target"]],
                        condition=rebind_condition(edge["condition"], result_ids, edge_ids, element_id=node.node_id),
                    )
            metadata[ORIGIN_KEY] = self._origin(pin, path, node.node_id)
            self._node(
                replace(
                    node,
                    node_id=node_ids[node.node_id],
                    gate_id=gate_ids.get(node.gate_id, ""),
                    budget=budget,
                    metadata=metadata,
                ),
                node.node_id,
            )
            fragments[node.node_id] = _Fragment(node_ids[node.node_id], node_ids[node.node_id], {}, {})
        # Targets of controls can themselves be expanded regions. Bind after all
        # fragments exist, while retaining the exact source sequence-flow IDs.
        actual_entries = {node_ids[key]: value.entry for key, value in fragments.items()}
        local_controls = {node_ids[node.node_id] for node in plan.nodes if node.node_type == "bpmn_control"}
        for emitted in self.nodes[emitted_start:]:
            if emitted.node_id in local_controls:
                for outgoing in emitted.metadata["bpmn_control"]["outgoing"]:
                    outgoing["target"] = actual_entries[outgoing["target"]]
        for edge in plan.edges:
            condition = rebind_condition(edge.condition, result_ids, edge_ids, element_id=edge.edge_id)
            self._edge(
                fragments[edge.source].exit,
                fragments[edge.target].entry,
                condition,
                edge_ids[edge.edge_id],
                self._origin(pin, path, edge.edge_id),
            )
        return _Fragment(fragments[entry].entry, fragments[exit].exit, result_ids, edge_ids)

    def _activation(self, node, pin, path, stack, budget):
        spec = node.metadata.get(ACTIVATION_KEY)
        if not isinstance(spec, dict) or set(node.metadata) != {ACTIVATION_KEY}:
            raise BpmnActivationError("bpmn_activation_contract_invalid", node.node_id)
        if any(key in spec for key in ("local_variables", "input_mapping", "output_mapping")):
            raise BpmnActivationError("bpmn_activation_variable_scope_unsupported", node.node_id)
        if node.gate_id:
            raise BpmnActivationError("bpmn_activation_boundary_gate_unsupported", node.node_id)
        kind = spec.get("kind")
        common = {"kind", "body"}
        if kind == "subprocess":
            if set(spec) != common:
                raise BpmnActivationError("bpmn_activation_subprocess_semantics_unsupported", node.node_id)
        elif kind == "while":
            if "bpmn_control_v1" not in self._resolve(pin, node.node_id).plan.capabilities:
                raise BpmnActivationError("bpmn_activation_control_capability_required", node.node_id)
            fields = {
                "max_iterations",
                "entry_condition",
                "repeat_condition",
                "body_flow_id",
                "back_flow_id",
                "exit_flow_id",
            }
            if set(spec) != common | fields:
                raise BpmnActivationError("bpmn_activation_loop_contract_invalid", node.node_id)
            maximum = spec["max_iterations"]
            if type(maximum) is not int or not 0 <= maximum <= self.limits.max_iterations:
                raise BpmnActivationError("bpmn_activation_iteration_limit", node.node_id)
            flow_ids = [spec[key] for key in ("body_flow_id", "back_flow_id", "exit_flow_id")]
            if (
                any(not isinstance(value, str) or not _ID.fullmatch(value) for value in flow_ids)
                or len(set(flow_ids)) != 3
            ):
                raise BpmnActivationError("bpmn_activation_loop_flow_invalid", node.node_id)
        else:
            raise BpmnActivationError("bpmn_activation_kind_unsupported", node.node_id)
        body = DefinitionPin.from_mapping(spec["body"], element_id=node.node_id)
        child_path = (*path, {"element_id": node.node_id, "kind": kind})
        # These are concrete integration gaps. A successful preview
        # must never implicitly advertise the full BPMNX-008/011 capability.
        self.blockers.setdefault(
            "read_model",
            BpmnActivationError(
                "bpmn_activation_request_projection_missing",
                node.node_id,
                "Public status binds request.steps IDs; expanded activations need "
                "an explicit request/read-model projection.",
            ),
        )
        self.blockers.setdefault(
            "deadline",
            BpmnActivationError(
                "bpmn_activation_deadline_binding_required",
                node.node_id,
                "The parent-owned persisted run deadline must be bound and verified "
                "for this expanded plan before admission.",
            ),
        )
        if kind == "subprocess":
            self.blockers.setdefault(
                "scope",
                BpmnActivationError(
                    "bpmn_activation_scoped_input_binding_required",
                    node.node_id,
                    "The compiler does not yet emit or validate scoped input/output projections; "
                    "ambient Native input is not confinement.",
                ),
            )
            return self._region(
                body,
                child_path,
                stack,
                budget,
                set(node.allowed_tools),
                set(node.required_capabilities),
                node.side_effect_class,
            )
        return self._while(node, pin, body, child_path, stack, budget)

    def _while(self, node, pin, body, path, stack, budget):
        spec = node.metadata[ACTIVATION_KEY]
        maximum = spec["max_iterations"]
        checks = [self._identity(pin, path, "check", str(i)) for i in range(maximum + 1)]
        done = self._identity(pin, path, "exit", node.node_id)
        # Validate even an unreachable/zero-count body and repeat condition.
        template = self._resolve(body, node.node_id)
        direct_results = {
            item.node_id: item.node_id for item in template.plan.nodes if item.node_type != "bpmn_activation"
        }
        direct_edges = {edge.edge_id: edge.edge_id for edge in template.plan.edges}
        rebind_condition(spec["repeat_condition"], direct_results, direct_edges, element_id=node.node_id)
        if maximum == 0:
            # Unreachable bodies are still validated, including nested calls.
            # Use the same bounded compiler in a disposable compilation scope.
            validation = _Expansion(self.definitions, self.limits, self.root, self.tenant_id, self.policy_version)
            validation.snapshots = self.snapshots
            validation._region(
                body,
                path,
                stack,
                budget,
                set(node.allowed_tools),
                set(node.required_capabilities),
                node.side_effect_class,
            )
            self.blockers.update(validation.blockers)
        previous = None
        for iteration in range(maximum + 1):
            condition = rebind_condition(
                spec["entry_condition"] if iteration == 0 else spec["repeat_condition"],
                {} if previous is None else previous.results,
                {} if previous is None else previous.edges,
                element_id=node.node_id,
            )
            outgoing = []
            exit_edge = self._identity(pin, path, "exit-flow", str(iteration))
            outgoing.append(
                {"id": exit_edge, "target": done, "condition": {"op": "not", "condition": condition}, "default": False}
            )
            self._selected_edge(checks[iteration], done, exit_edge, pin, path, spec["exit_flow_id"])
            if iteration < maximum:
                iteration_path = (*path[:-1], {**path[-1], "iteration": iteration})
                fragment = self._region(
                    body,
                    iteration_path,
                    stack,
                    budget,
                    set(node.allowed_tools),
                    set(node.required_capabilities),
                    node.side_effect_class,
                )
                continue_edge = self._identity(pin, path, "body-flow", str(iteration))
                outgoing.append(
                    {"id": continue_edge, "target": fragment.entry, "condition": condition, "default": False}
                )
                self._selected_edge(
                    checks[iteration], fragment.entry, continue_edge, pin, iteration_path, spec["body_flow_id"]
                )
                self._edge(
                    fragment.exit,
                    checks[iteration + 1],
                    {"op": "always"},
                    self._identity(pin, path, "back-flow", str(iteration)),
                    self._origin(pin, iteration_path, spec["back_flow_id"]),
                )
                previous = fragment
            metadata = {
                "bpmn_control": {"kind": "exclusive", "outgoing": outgoing},
                ORIGIN_KEY: {
                    **self._origin(pin, path, node.node_id),
                    "check_index": iteration,
                    "limit_check": iteration == maximum,
                },
            }
            self._node(
                ExecutionNode(checks[iteration], node_type="bpmn_control", budget=budget, metadata=metadata),
                node.node_id,
            )
        self._node(
            ExecutionNode(
                done,
                node_type="bpmn_control",
                budget=budget,
                metadata={
                    "bpmn_control": {"kind": "exclusive", "outgoing": []},
                    ORIGIN_KEY: self._origin(pin, path, node.node_id),
                },
            ),
            node.node_id,
        )
        return _Fragment(checks[0], done, {}, {})

    def _selected_edge(self, source, target, edge_id, pin, path, source_id):
        self._edge(
            source,
            target,
            {"op": "eq", "field": f"results.{source}.selected_edge", "value": edge_id},
            edge_id,
            self._origin(pin, path, source_id),
        )

    def _node(self, node, owner):
        if len(self.nodes) >= self.limits.max_nodes:
            raise BpmnActivationError("bpmn_activation_node_budget_exceeded", owner)
        self.nodes.append(node)

    def _edge(self, source, target, condition, edge_id, origin):
        if len(self.edges) >= self.limits.max_edges:
            raise BpmnActivationError("bpmn_activation_edge_budget_exceeded", origin["source_id"])
        self.edges.append(ExecutionEdge(source, target, condition, edge_id))
        self.edge_origins[edge_id] = origin

    def _identity(self, pin, path, kind, source_id):
        return "bpa_" + sha256_json(
            {
                "root": self.root.to_dict(),
                "definition": pin.to_dict(),
                "path": path,
                "kind": kind,
                "source_id": source_id,
            }
        )

    @staticmethod
    def _origin(pin, path, source_id):
        return {"definition": pin.to_dict(), "path": deepcopy(list(path)), "source_id": source_id}

    @staticmethod
    def _boundary(plan, owner):
        ids = [node.node_id for node in plan.nodes] + [edge.edge_id for edge in plan.edges]
        if len(set(ids)) != len(ids) or any(not _ID.fullmatch(value) for value in ids):
            raise BpmnActivationError("bpmn_activation_source_identity_invalid", owner)
        targets, sources = {edge.target for edge in plan.edges}, {edge.source for edge in plan.edges}
        entries = [node.node_id for node in plan.nodes if node.node_id not in targets]
        exits = [node.node_id for node in plan.nodes if node.node_id not in sources]
        if len(entries) != 1 or len(exits) != 1:
            raise BpmnActivationError("bpmn_activation_single_entry_exit_required", owner)
        return entries[0], exits[0]

    @staticmethod
    def _budget(value: ExecutionBudget, ceiling: ExecutionBudget, owner):
        for budget in (value, ceiling):
            if (
                type(budget.max_attempts) is not int
                or budget.max_attempts < 1
                or type(budget.timeout_seconds) not in {int, float}
                or not math.isfinite(budget.timeout_seconds)
                or budget.timeout_seconds <= 0
                or type(budget.max_cost_micros) is not int
                or budget.max_cost_micros < 0
                or budget.max_tokens is not None
                and (type(budget.max_tokens) is not int or budget.max_tokens < 0)
            ):
                raise BpmnActivationError("bpmn_activation_finite_budget_required", owner)
        for field in ("max_attempts", "timeout_seconds", "max_cost_micros", "max_tokens"):
            parent, child = getattr(ceiling, field), getattr(value, field)
            if parent is not None and (child is None or child > parent):
                raise BpmnActivationError("bpmn_activation_budget_escalation", owner, field)
