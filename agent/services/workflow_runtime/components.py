"""Versioned, policy-narrowing reusable workflow components.

The compiler expands component nodes into the framework-neutral
``ExecutionPlan``.  It never executes a component and therefore remains a Hub
planning concern shared by Native and LangGraph adapters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from jsonschema import Draft202012Validator

from agent.services.workflow_runtime.execution_plan import (
    ArtifactContract,
    ExecutionEdge,
    ExecutionGate,
    ExecutionNode,
    ExecutionPlan,
)

WORKFLOW_COMPONENT_SCHEMA = "ananta.workflow_component.v1"
COMPILED_COMPONENT_OUTPUT_SCHEMA_KEY = "component_output_schema"
COMPILED_COMPONENT_ARTIFACT_CONTRACT_KEY = "component_artifact_contract"
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True)
class WorkflowComponent:
    component_id: str
    version: str
    policy_version: str
    plan: ExecutionPlan
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    compatible_versions: tuple[str, ...] = ()
    artifact_contract: dict[str, Any] = field(default_factory=dict)
    schema: str = WORKFLOW_COMPONENT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "WorkflowComponent":
        component = cls(
            component_id=str(raw.get("component_id") or "").strip(),
            version=str(raw.get("version") or "").strip(),
            policy_version=str(raw.get("policy_version") or "").strip(),
            plan=ExecutionPlan.from_mapping(dict(raw.get("plan") or {})),
            input_schema=dict(raw.get("input_schema") or {"type": "object"}),
            output_schema=dict(raw.get("output_schema") or {"type": "object"}),
            input_artifacts=_clean_tuple(raw.get("input_artifacts") or ()),
            output_artifacts=_clean_tuple(raw.get("output_artifacts") or ()),
            required_capabilities=_clean_tuple(raw.get("required_capabilities") or ()),
            allowed_tools=_clean_tuple(raw.get("allowed_tools") or ()),
            compatible_versions=_clean_tuple(raw.get("compatible_versions") or ()),
            artifact_contract=dict(raw.get("artifact_contract") or {}),
            schema=str(raw.get("schema") or WORKFLOW_COMPONENT_SCHEMA),
        )
        component.assert_valid()
        return component

    def assert_valid(self) -> None:
        if self.schema != WORKFLOW_COMPONENT_SCHEMA:
            raise ValueError("workflow_component_schema_unsupported")
        if not self.component_id or not _SEMVER.fullmatch(self.version) or not self.policy_version:
            raise ValueError("workflow_component_identity_invalid")
        for compatible in self.compatible_versions:
            if not _SEMVER.fullmatch(compatible):
                raise ValueError("workflow_component_compatible_version_invalid")
        Draft202012Validator.check_schema(self.input_schema)
        Draft202012Validator.check_schema(self.output_schema)
        if self.artifact_contract:
            Draft202012Validator.check_schema(self.artifact_contract)
        declared_artifacts = {artifact.artifact_id for artifact in self.plan.artifacts}
        if set(self.input_artifacts + self.output_artifacts) - declared_artifacts:
            raise ValueError("workflow_component_interface_artifact_unknown")
        if set(self.required_capabilities) - set(self.plan.capabilities):
            raise ValueError("workflow_component_capability_not_declared")
        used_tools = {tool for node in self.plan.nodes for tool in node.allowed_tools}
        if used_tools - set(self.allowed_tools):
            raise ValueError("workflow_component_tool_not_declared")
        if self.plan.policy_version != self.policy_version:
            raise ValueError("workflow_component_policy_mismatch")

    def validate_input(self, value: Any) -> None:
        errors = sorted(Draft202012Validator(self.input_schema).iter_errors(value), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"workflow_component_input_invalid:{errors[0].json_path}")

    def validate_output(self, value: Any) -> None:
        errors = sorted(Draft202012Validator(self.output_schema).iter_errors(value), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"workflow_component_output_invalid:{errors[0].json_path}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "component_id": self.component_id,
            "version": self.version,
            "policy_version": self.policy_version,
            "plan": self.plan.to_dict(),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "required_capabilities": list(self.required_capabilities),
            "allowed_tools": list(self.allowed_tools),
            "compatible_versions": list(self.compatible_versions),
            "artifact_contract": dict(self.artifact_contract),
        }


class WorkflowComponentRegistry:
    """Explicit registry instance; no process-global component authority."""

    def __init__(self) -> None:
        self._components: dict[tuple[str, str], WorkflowComponent] = {}

    def register(self, component: WorkflowComponent) -> None:
        component.assert_valid()
        key = (component.component_id, component.version)
        existing = self._components.get(key)
        if existing is not None and existing.to_dict() != component.to_dict():
            raise ValueError("workflow_component_version_conflict")
        self._components[key] = component

    def resolve(self, component_id: str, version: str) -> WorkflowComponent:
        exact = self._components.get((str(component_id), str(version)))
        if exact is not None:
            return exact
        compatible = [
            component
            for (registered_id, _), component in self._components.items()
            if registered_id == str(component_id) and str(version) in component.compatible_versions
        ]
        if len(compatible) != 1:
            raise KeyError("workflow_component_version_not_found")
        return compatible[0]

    def versions(self, component_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                (version for registered_id, version in self._components if registered_id == component_id),
                key=_version_tuple,
            )
        )


class WorkflowComponentCompiler:
    def __init__(self, registry: WorkflowComponentRegistry, *, max_depth: int = 8):
        if max_depth < 1:
            raise ValueError("workflow_component_depth_invalid")
        self._registry = registry
        self._max_depth = max_depth

    def compile(self, plan: ExecutionPlan) -> ExecutionPlan:
        """Expand all component nodes and validate the complete graph first."""

        plan.assert_valid()
        return self._compile(plan, stack=())

    def _compile(self, plan: ExecutionPlan, *, stack: tuple[tuple[str, str], ...]) -> ExecutionPlan:
        fragments: dict[str, _Fragment] = {}
        artifacts: dict[str, ArtifactContract] = {item.artifact_id: item for item in plan.artifacts}
        gates: dict[str, ExecutionGate] = {item.gate_id: item for item in plan.gates}
        component_versions: dict[str, str] = {}

        for node in plan.nodes:
            reference = _component_reference(node)
            if reference is None:
                fragments[node.node_id] = _Fragment((node,), (), (node.node_id,), (node.node_id,))
                continue
            component = self._registry.resolve(*reference)
            key = (component.component_id, component.version)
            if key in stack:
                raise ValueError("workflow_component_recursive_cycle")
            if len(stack) >= self._max_depth:
                raise ValueError("workflow_component_depth_exceeded")
            self._assert_narrowing(plan=plan, placeholder=node, component=component)
            component.validate_input(dict(node.metadata.get("component_input") or {}))
            nested = self._compile(component.plan, stack=(*stack, key))
            fragment, extra_artifacts, extra_gates = _prefix_component(
                placeholder=node,
                component=component,
                compiled_plan=nested,
            )
            fragments[node.node_id] = fragment
            for artifact in extra_artifacts:
                if artifact.artifact_id in artifacts and artifacts[artifact.artifact_id] != artifact:
                    raise ValueError("workflow_component_artifact_conflict")
                artifacts[artifact.artifact_id] = artifact
            for gate in extra_gates:
                if gate.gate_id in gates and gates[gate.gate_id] != gate:
                    raise ValueError("workflow_component_gate_conflict")
                gates[gate.gate_id] = gate
            component_versions[node.node_id] = f"{component.component_id}@{component.version}"

        nodes = tuple(node for root_node in plan.nodes for node in fragments[root_node.node_id].nodes)
        edges: list[ExecutionEdge] = [
            edge for root_node in plan.nodes for edge in fragments[root_node.node_id].edges
        ]
        for edge in plan.edges:
            source_fragment = fragments[edge.source]
            target_fragment = fragments[edge.target]
            for source in source_fragment.exits:
                for target in target_fragment.entries:
                    edges.append(ExecutionEdge(source=source, target=target, condition=dict(edge.condition)))

        metadata = dict(plan.metadata)
        if component_versions:
            metadata["compiled_components"] = dict(sorted(component_versions.items()))
        compiled = ExecutionPlan(
            tenant_id=plan.tenant_id,
            plan_id=plan.plan_id,
            workflow_id=plan.workflow_id,
            policy_version=plan.policy_version,
            nodes=nodes,
            edges=tuple(edges),
            capabilities=plan.capabilities,
            gates=tuple(gates[key] for key in sorted(gates)),
            artifacts=tuple(artifacts[key] for key in sorted(artifacts)),
            budget=plan.budget,
            metadata=metadata,
        )
        compiled.assert_valid()
        return compiled

    @staticmethod
    def _assert_narrowing(
        *, plan: ExecutionPlan, placeholder: ExecutionNode, component: WorkflowComponent
    ) -> None:
        if component.policy_version != plan.policy_version:
            raise ValueError("workflow_component_policy_escalation")
        if set(component.required_capabilities) - set(plan.capabilities):
            raise ValueError("workflow_component_capability_escalation")
        if set(component.allowed_tools) - set(placeholder.allowed_tools):
            raise ValueError("workflow_component_tool_escalation")
        if placeholder.gate_id:
            raise ValueError("workflow_component_placeholder_gate_unsupported")
        if len(component.input_artifacts) != len(placeholder.input_artifacts):
            raise ValueError("workflow_component_input_artifact_mismatch")
        if len(component.output_artifacts) != len(placeholder.output_artifacts):
            raise ValueError("workflow_component_output_artifact_mismatch")


@dataclass(frozen=True)
class _Fragment:
    nodes: tuple[ExecutionNode, ...]
    edges: tuple[ExecutionEdge, ...]
    entries: tuple[str, ...]
    exits: tuple[str, ...]


def _prefix_component(
    *,
    placeholder: ExecutionNode,
    component: WorkflowComponent,
    compiled_plan: ExecutionPlan,
) -> tuple[_Fragment, tuple[ArtifactContract, ...], tuple[ExecutionGate, ...]]:
    prefix = f"{placeholder.node_id}/"
    input_map = dict(zip(component.input_artifacts, placeholder.input_artifacts, strict=True))
    output_map = dict(zip(component.output_artifacts, placeholder.output_artifacts, strict=True))
    interface_map = {**input_map, **output_map}

    def artifact_id(value: str) -> str:
        return interface_map.get(value, f"{prefix}{value}")

    gate_map = {gate.gate_id: f"{prefix}{gate.gate_id}" for gate in compiled_plan.gates}
    node_map = {node.node_id: f"{prefix}{node.node_id}" for node in compiled_plan.nodes}
    exit_ids = {
        node.node_id
        for node in compiled_plan.nodes
        if not any(edge.source == node.node_id for edge in compiled_plan.edges)
    }
    nodes = tuple(
        replace(
            node,
            node_id=node_map[node.node_id],
            input_artifacts=tuple(artifact_id(value) for value in node.input_artifacts),
            output_artifacts=tuple(artifact_id(value) for value in node.output_artifacts),
            gate_id=gate_map.get(node.gate_id, ""),
            metadata={
                **dict(node.metadata),
                "component_id": component.component_id,
                "component_version": component.version,
                "component_instance": placeholder.node_id,
                **(
                    {
                        COMPILED_COMPONENT_OUTPUT_SCHEMA_KEY: dict(component.output_schema),
                        COMPILED_COMPONENT_ARTIFACT_CONTRACT_KEY: dict(component.artifact_contract),
                    }
                    if node.node_id in exit_ids
                    else {}
                ),
            },
        )
        for node in compiled_plan.nodes
    )
    edges = tuple(
        ExecutionEdge(
            source=node_map[edge.source],
            target=node_map[edge.target],
            condition=dict(edge.condition),
        )
        for edge in compiled_plan.edges
    )
    indegree = {node.node_id: 0 for node in compiled_plan.nodes}
    outdegree = {node.node_id: 0 for node in compiled_plan.nodes}
    for edge in compiled_plan.edges:
        indegree[edge.target] += 1
        outdegree[edge.source] += 1
    entries = tuple(node_map[node.node_id] for node in compiled_plan.nodes if indegree[node.node_id] == 0)
    exits = tuple(node_map[node.node_id] for node in compiled_plan.nodes if outdegree[node.node_id] == 0)
    artifacts = tuple(
        replace(artifact, artifact_id=artifact_id(artifact.artifact_id))
        for artifact in compiled_plan.artifacts
        if artifact.artifact_id not in interface_map
    )
    gates = tuple(replace(gate, gate_id=gate_map[gate.gate_id]) for gate in compiled_plan.gates)
    return _Fragment(nodes, edges, entries, exits), artifacts, gates


def _component_reference(node: ExecutionNode) -> tuple[str, str] | None:
    if node.node_type != "component":
        return None
    raw = node.metadata.get("component")
    if not isinstance(raw, dict):
        raise ValueError("workflow_component_reference_required")
    component_id = str(raw.get("id") or raw.get("component_id") or "").strip()
    version = str(raw.get("version") or "").strip()
    if not component_id or not _SEMVER.fullmatch(version):
        raise ValueError("workflow_component_reference_invalid")
    return component_id, version


def validate_compiled_component_output(node: ExecutionNode, value: Any) -> None:
    """Validate output at the flattened component boundary in every runtime."""

    raw_schema = node.metadata.get(COMPILED_COMPONENT_OUTPUT_SCHEMA_KEY)
    if raw_schema is None:
        return
    if not isinstance(raw_schema, dict):
        raise ValueError("workflow_component_compiled_output_schema_invalid")
    errors = sorted(
        Draft202012Validator(raw_schema).iter_errors(value),
        key=lambda item: list(item.path),
    )
    if errors:
        raise ValueError(f"workflow_component_output_invalid:{errors[0].json_path}")


def _clean_tuple(values: Any) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values or () if str(value).strip()}))


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("workflow_component_version_invalid")
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]
