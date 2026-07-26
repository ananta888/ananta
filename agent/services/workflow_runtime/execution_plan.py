"""Versioned, runtime-neutral execution plan contract.

The contract deliberately contains no LangChain, LangGraph, or Temporal types. The
hub compiles and validates a plan before delegating nodes to worker runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.model_routing_contract import (
    MODEL_ROUTING_JSON_SCHEMA,
    ModelRoutingConfig,
    ModelRoutingContractError,
    sanitize_model_routing_metadata,
)
from agent.services.workflow_runtime._serialization import redact_json, sha256_json
from agent.services.workflow_runtime.errors import ContractIssue, ContractValidationError

EXECUTION_PLAN_SCHEMA = "ananta.execution_plan.v1"
SIDE_EFFECT_CLASSES = frozenset({"none", "read", "idempotent_write", "non_idempotent_write"})
CONDITION_OPERATORS = frozenset({"always", "all", "any", "not", "eq", "ne", "in", "exists"})
MERGE_STRATEGIES = frozenset({"object-by-node-id", "ordered-by-node-id"})
MERGE_PARTIAL_FAILURE_POLICIES = frozenset({"fail", "omit"})
JOIN_MODES = frozenset({"all", "any"})
NODE_FAILURE_POLICIES = frozenset({"continue", "fail"})
RESERVED_METADATA_KEYS = frozenset(
    {
        "allowed_tools",
        "authorization",
        "budgets",
        "capabilities",
        "policy_version",
        "tenant_id",
    }
)


@dataclass(frozen=True)
class ExecutionBudget:
    max_attempts: int = 1
    timeout_seconds: float = 300.0
    max_tokens: int | None = None
    max_cost_micros: int | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "ExecutionBudget":
        value = dict(raw or {})
        return cls(
            max_attempts=int(value.get("max_attempts", 1)),
            timeout_seconds=float(value.get("timeout_seconds", 300.0)),
            max_tokens=int(value["max_tokens"]) if value.get("max_tokens") is not None else None,
            max_cost_micros=(
                int(value["max_cost_micros"]) if value.get("max_cost_micros") is not None else None
            ),
        )

    def validate(self, path: str = "budget") -> tuple[ContractIssue, ...]:
        issues: list[ContractIssue] = []
        if self.max_attempts < 1:
            issues.append(ContractIssue("budget_max_attempts_invalid", path))
        if self.timeout_seconds <= 0:
            issues.append(ContractIssue("budget_timeout_invalid", path))
        if self.max_tokens is not None and self.max_tokens < 0:
            issues.append(ContractIssue("budget_max_tokens_invalid", path))
        if self.max_cost_micros is not None and self.max_cost_micros < 0:
            issues.append(ContractIssue("budget_max_cost_invalid", path))
        return tuple(issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "max_cost_micros": self.max_cost_micros,
        }


@dataclass(frozen=True)
class ArtifactContract:
    artifact_id: str
    media_type: str = "application/octet-stream"
    required: bool = True
    schema_ref: str = ""

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ArtifactContract":
        return cls(
            artifact_id=str(raw.get("artifact_id") or raw.get("id") or "").strip(),
            media_type=str(raw.get("media_type") or "application/octet-stream").strip(),
            required=bool(raw.get("required", True)),
            schema_ref=str(raw.get("schema_ref") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "media_type": self.media_type,
            "required": self.required,
            "schema_ref": self.schema_ref,
        }


@dataclass(frozen=True)
class ExecutionGate:
    gate_id: str
    gate_type: str = "approval"
    required_roles: tuple[str, ...] = ()
    expires_after_seconds: float | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ExecutionGate":
        return cls(
            gate_id=str(raw.get("gate_id") or raw.get("id") or "").strip(),
            gate_type=str(raw.get("gate_type") or "approval").strip(),
            required_roles=tuple(sorted({str(v).strip() for v in raw.get("required_roles", []) if str(v).strip()})),
            expires_after_seconds=(
                float(raw["expires_after_seconds"])
                if raw.get("expires_after_seconds") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "gate_type": self.gate_type,
            "required_roles": list(self.required_roles),
            "expires_after_seconds": self.expires_after_seconds,
        }


@dataclass(frozen=True)
class ExecutionNode:
    node_id: str
    node_type: str = "task"
    task_kind: str = "coding"
    required_capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    gate_id: str = ""
    side_effect_class: str = "none"
    budget: ExecutionBudget | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ExecutionNode":
        return cls(
            node_id=str(raw.get("node_id") or raw.get("step_id") or raw.get("id") or "").strip(),
            node_type=str(raw.get("node_type") or "task").strip(),
            task_kind=str(raw.get("task_kind") or "coding").strip(),
            required_capabilities=_clean_tuple(raw.get("required_capabilities")),
            allowed_tools=_clean_tuple(raw.get("allowed_tools")),
            input_artifacts=_clean_tuple(raw.get("input_artifacts")),
            output_artifacts=_clean_tuple(raw.get("output_artifacts")),
            gate_id=str(raw.get("gate_id") or "").strip(),
            side_effect_class=str(raw.get("side_effect_class") or "none").strip().lower(),
            budget=ExecutionBudget.from_mapping(raw["budget"]) if raw.get("budget") is not None else None,
            metadata=sanitize_model_routing_metadata(raw.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "task_kind": self.task_kind,
            "required_capabilities": list(self.required_capabilities),
            "allowed_tools": list(self.allowed_tools),
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "gate_id": self.gate_id,
            "side_effect_class": self.side_effect_class,
            "budget": self.budget.to_dict() if self.budget else None,
            "metadata": sanitize_model_routing_metadata(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionEdge:
    source: str
    target: str
    condition: dict[str, Any] = field(default_factory=lambda: {"op": "always"})

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ExecutionEdge":
        return cls(
            source=str(raw.get("source") or raw.get("from") or "").strip(),
            target=str(raw.get("target") or raw.get("to") or "").strip(),
            condition=dict(raw.get("condition") or {"op": "always"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "condition": dict(self.condition)}


@dataclass(frozen=True)
class ExecutionPlan:
    tenant_id: str
    plan_id: str
    workflow_id: str
    policy_version: str
    nodes: tuple[ExecutionNode, ...]
    edges: tuple[ExecutionEdge, ...] = ()
    capabilities: tuple[str, ...] = ()
    gates: tuple[ExecutionGate, ...] = ()
    artifacts: tuple[ArtifactContract, ...] = ()
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = EXECUTION_PLAN_SCHEMA

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, validate: bool = True) -> "ExecutionPlan":
        from agent.services.workflow_runtime.compatibility import (
            upcast_runtime_contract_for_loading,
        )

        raw = upcast_runtime_contract_for_loading(raw, contract_type="plan")
        plan = cls(
            tenant_id=str(raw.get("tenant_id") or "").strip(),
            plan_id=str(raw.get("plan_id") or "").strip(),
            workflow_id=str(raw.get("workflow_id") or "").strip(),
            policy_version=str(raw.get("policy_version") or "").strip(),
            nodes=tuple(ExecutionNode.from_mapping(v) for v in raw.get("nodes", [])),
            edges=tuple(ExecutionEdge.from_mapping(v) for v in raw.get("edges", [])),
            capabilities=_clean_tuple(raw.get("capabilities")),
            gates=tuple(ExecutionGate.from_mapping(v) for v in raw.get("gates", [])),
            artifacts=tuple(ArtifactContract.from_mapping(v) for v in raw.get("artifacts", [])),
            budget=ExecutionBudget.from_mapping(raw.get("budget")),
            metadata=dict(raw.get("metadata") or {}),
            schema=str(raw.get("schema") or EXECUTION_PLAN_SCHEMA),
        )
        provided_hash = str(raw.get("plan_hash") or "").strip()
        if provided_hash and provided_hash != plan.plan_hash:
            raise ContractValidationError("execution_plan_hash_mismatch")
        if validate:
            plan.assert_valid()
        return plan

    def validate(self) -> tuple[ContractIssue, ...]:
        issues: list[ContractIssue] = []
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("plan_id", self.plan_id),
            ("workflow_id", self.workflow_id),
            ("policy_version", self.policy_version),
        ):
            if not value:
                issues.append(ContractIssue(f"{name}_required", name))
        if self.schema != EXECUTION_PLAN_SCHEMA:
            issues.append(ContractIssue("execution_plan_schema_unsupported", "schema"))
        if not self.nodes:
            issues.append(ContractIssue("nodes_required", "nodes"))
        issues.extend(self.budget.validate())

        node_ids = [node.node_id for node in self.nodes]
        known_nodes = set(node_ids)
        if "" in known_nodes:
            issues.append(ContractIssue("node_id_required", "nodes"))
        if len(node_ids) != len(known_nodes):
            issues.append(ContractIssue("duplicate_node_id", "nodes"))

        gate_ids = [gate.gate_id for gate in self.gates]
        known_gates = set(gate_ids)
        if "" in known_gates:
            issues.append(ContractIssue("gate_id_required", "gates"))
        if len(gate_ids) != len(known_gates):
            issues.append(ContractIssue("duplicate_gate_id", "gates"))

        known_artifacts, artifact_issues = _validate_artifact_contracts(
            self.artifacts,
            self.nodes,
        )
        issues.extend(artifact_issues)

        declared_capabilities = set(self.capabilities)
        for index, node in enumerate(self.nodes):
            path = f"nodes[{index}]"
            if node.side_effect_class not in SIDE_EFFECT_CLASSES:
                issues.append(ContractIssue("side_effect_class_invalid", f"{path}.side_effect_class"))
            missing = set(node.required_capabilities) - declared_capabilities
            if missing:
                issues.append(
                    ContractIssue("capability_not_declared", f"{path}.required_capabilities", ",".join(sorted(missing)))
                )
            if node.gate_id and node.gate_id not in known_gates:
                issues.append(ContractIssue("gate_not_declared", f"{path}.gate_id", node.gate_id))
            for artifact_id in (*node.input_artifacts, *node.output_artifacts):
                if artifact_id not in known_artifacts:
                    issues.append(ContractIssue("artifact_not_declared", path, artifact_id))
            if node.budget:
                issues.extend(node.budget.validate(f"{path}.budget"))
            reserved = RESERVED_METADATA_KEYS.intersection(node.metadata)
            if reserved:
                issues.append(ContractIssue("reserved_metadata_key", f"{path}.metadata", ",".join(sorted(reserved))))
            issues.extend(_validate_node_runtime_metadata(node, path))

        edge_pairs: set[tuple[str, str]] = set()
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
        indegree: dict[str, int] = {node_id: 0 for node_id in known_nodes}
        for index, edge in enumerate(self.edges):
            path = f"edges[{index}]"
            if edge.source not in known_nodes:
                issues.append(ContractIssue("edge_source_unknown", f"{path}.source", edge.source))
            if edge.target not in known_nodes:
                issues.append(ContractIssue("edge_target_unknown", f"{path}.target", edge.target))
            if edge.source == edge.target:
                issues.append(ContractIssue("edge_self_cycle", path))
            pair = (edge.source, edge.target)
            if pair in edge_pairs:
                issues.append(ContractIssue("duplicate_edge", path))
            edge_pairs.add(pair)
            issues.extend(_validate_condition(edge.condition, f"{path}.condition"))
            if edge.source in known_nodes and edge.target in known_nodes and edge.target not in adjacency[edge.source]:
                adjacency[edge.source].add(edge.target)
                indegree[edge.target] += 1

        queue = sorted(node_id for node_id, count in indegree.items() if count == 0)
        visited = 0
        while queue:
            node_id = queue.pop(0)
            visited += 1
            for target in sorted(adjacency[node_id]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if len(known_nodes) > 0 and visited != len(known_nodes):
            issues.append(ContractIssue("execution_plan_cycle", "edges"))
        issues.extend(_validate_merge_sources(self.nodes, self.edges))

        reserved = RESERVED_METADATA_KEYS.intersection(self.metadata)
        if reserved:
            issues.append(ContractIssue("reserved_metadata_key", "metadata", ",".join(sorted(reserved))))
        issues.extend(_validate_plan_runtime_metadata(self.metadata))
        issues.extend(_validate_parallel_groups(self.nodes))
        return tuple(issues)

    def assert_valid(self) -> None:
        issues = self.validate()
        if issues:
            raise ContractValidationError(*issues)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "plan_id": self.plan_id,
            "workflow_id": self.workflow_id,
            "policy_version": self.policy_version,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "capabilities": list(self.capabilities),
            "gates": [gate.to_dict() for gate in self.gates],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "budget": self.budget.to_dict(),
            "metadata": dict(self.metadata),
        }
        if include_hash:
            payload["plan_hash"] = sha256_json(payload)
        return payload

    @property
    def plan_hash(self) -> str:
        return sha256_json(self.to_dict(include_hash=False))


class WorkflowRequestExecutionPlanAdapter:
    """Compatibility adapter for the existing ``WorkflowRequest`` v1 contract."""

    @staticmethod
    def adapt(
        request: Any,
        *,
        tenant_id: str,
        policy_version: str,
        default_budget: ExecutionBudget | None = None,
    ) -> ExecutionPlan:
        steps = tuple(getattr(request, "steps", ()) or ())
        nodes: list[ExecutionNode] = []
        edges: list[ExecutionEdge] = []
        gates: list[ExecutionGate] = []
        artifact_ids: set[str] = set(getattr(request, "input_artifacts", ()) or ())
        request_metadata = dict(getattr(request, "metadata", {}) or {})
        legacy_budget = request_metadata.get("execution_budget")
        if legacy_budget is not None and not isinstance(legacy_budget, dict):
            raise ValueError("legacy_execution_budget_invalid")
        resolved_budget = default_budget or ExecutionBudget.from_mapping(legacy_budget)
        declared_capabilities: set[str] = set(_clean_tuple(request_metadata.get("capabilities")))
        for step in steps:
            step_id = str(getattr(step, "step_id", "")).strip()
            step_metadata = dict(getattr(step, "metadata", {}) or {})
            model_routing = ModelRoutingConfig.from_metadata(step_metadata)
            required_capabilities = _clean_tuple(step_metadata.get("required_capabilities"))
            declared_capabilities.update(required_capabilities)
            gate_id = f"gate:{step_id}" if bool(getattr(step, "gate", False)) else ""
            if gate_id:
                gates.append(ExecutionGate(gate_id=gate_id))
            inputs = _clean_tuple(getattr(step, "input_artifacts", ()))
            outputs = _clean_tuple(getattr(step, "output_artifacts", ()))
            artifact_ids.update(inputs)
            artifact_ids.update(outputs)
            nodes.append(
                ExecutionNode(
                    node_id=step_id,
                    task_kind=str(getattr(step, "task_kind", "coding") or "coding"),
                    required_capabilities=required_capabilities,
                    allowed_tools=_clean_tuple(getattr(step, "allowed_tools", ())),
                    input_artifacts=inputs,
                    output_artifacts=outputs,
                    gate_id=gate_id,
                    side_effect_class=_legacy_side_effect_class(step_metadata),
                    metadata={
                        "legacy_role": str(getattr(step, "role", "") or ""),
                        "legacy_policy_scope_hash": sha256_json(
                            redact_json(dict(getattr(step, "policy_scope", {}) or {}))
                        ),
                        "legacy_metadata_hash": sha256_json(redact_json(step_metadata)),
                        "declared_operation": str(
                            step_metadata.get("operation_name")
                            or step_metadata.get("declared_operation")
                            or ""
                        ),
                        **(
                            {"model_routing": model_routing.as_metadata()}
                            if model_routing is not None
                            else {}
                        ),
                    },
                )
            )
            for dependency in getattr(step, "depends_on", ()) or ():
                edges.append(ExecutionEdge(source=str(dependency), target=step_id))
        artifacts = tuple(ArtifactContract(artifact_id=value) for value in sorted(artifact_ids))
        request_serializer = getattr(request, "to_dict", None)
        request_payload = (
            request_serializer()
            if callable(request_serializer)
            else {
                "workflow_id": str(getattr(request, "workflow_id", "")),
                "plan_id": str(getattr(request, "plan_id", "")),
            }
        )
        plan = ExecutionPlan(
            tenant_id=str(tenant_id).strip(),
            plan_id=str(getattr(request, "plan_id", "") or getattr(request, "workflow_id", "")).strip(),
            workflow_id=str(getattr(request, "workflow_id", "")).strip(),
            policy_version=str(policy_version).strip(),
            nodes=tuple(nodes),
            edges=tuple(edges),
            capabilities=tuple(sorted(declared_capabilities)),
            gates=tuple(gates),
            artifacts=artifacts,
            budget=resolved_budget,
            metadata={
                "adapted_from": "ananta.workflow_request.v1",
                "legacy_request_hash": sha256_json(redact_json(request_payload)),
                **_legacy_rollout_scope(
                    request_metadata,
                    tenant_id=str(tenant_id).strip(),
                    workflow_id=str(getattr(request, "workflow_id", "")).strip(),
                ),
            },
        )
        plan.assert_valid()
        return plan


def _clean_tuple(values: Any) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values or () if str(value).strip()}))


def _legacy_side_effect_class(metadata: dict[str, Any]) -> str:
    value = str(metadata.get("side_effect_class") or metadata.get("activity_class") or "none").strip().lower()
    return value if value in SIDE_EFFECT_CLASSES else "none"


def _legacy_rollout_scope(
    metadata: dict[str, Any],
    *,
    tenant_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    raw = metadata.get("workflow_rollout_scope")
    if isinstance(raw, dict):
        candidate = dict(raw)
    elif metadata.get("project_id"):
        candidate = {
            "project_id": metadata.get("project_id"),
            "profile_id": metadata.get("runtime_profile_id")
            or metadata.get("profile_id"),
        }
    else:
        return {}
    bounded = {
        "project_id": str(candidate.get("project_id") or "").strip()[:160],
        # Tenant/workflow are Hub compiler inputs, never request metadata.
        "tenant_id": str(tenant_id).strip()[:160],
        "profile_id": str(candidate.get("profile_id") or "").strip()[:160],
        "workflow_id": "",
    }
    if bounded["profile_id"]:
        bounded["workflow_id"] = str(workflow_id).strip()[:160]
    return {"workflow_rollout_scope": bounded}


def _validate_condition(condition: Any, path: str) -> tuple[ContractIssue, ...]:
    if not isinstance(condition, dict):
        return (ContractIssue("condition_mapping_required", path),)
    operator = str(condition.get("op") or "").strip()
    if operator not in CONDITION_OPERATORS:
        return (ContractIssue("condition_operator_invalid", f"{path}.op", operator),)
    issues: list[ContractIssue] = []
    if operator in {"all", "any"}:
        children = condition.get("conditions")
        if not isinstance(children, list) or not children:
            issues.append(ContractIssue("condition_children_required", f"{path}.conditions"))
        else:
            for index, child in enumerate(children):
                issues.extend(_validate_condition(child, f"{path}.conditions[{index}]"))
    elif operator == "not":
        issues.extend(_validate_condition(condition.get("condition"), f"{path}.condition"))
    elif operator in {"eq", "ne", "in", "exists"}:
        field_name = condition.get("field")
        if not isinstance(field_name, str) or not field_name.strip():
            issues.append(ContractIssue("condition_field_required", f"{path}.field"))
        if operator != "exists" and "value" not in condition:
            issues.append(ContractIssue("condition_value_required", f"{path}.value"))
    return tuple(issues)


def _validate_node_runtime_metadata(
    node: ExecutionNode,
    path: str,
) -> tuple[ContractIssue, ...]:
    """Validate framework-neutral routing, fan-out, and merge declarations.

    These fields affect orchestration semantics and therefore cannot be left to
    an individual runtime to interpret ad hoc.  Validation stays in the neutral
    plan contract so Native and LangGraph fail on the same malformed plan.
    """

    issues: list[ContractIssue] = []
    metadata = node.metadata
    if "parallel_limit" in metadata:
        issues.extend(
            _validate_positive_integer(
                metadata["parallel_limit"],
                path=f"{path}.metadata.parallel_limit",
                code="node_parallel_limit_invalid",
            )
        )
    if "parallel_group" in metadata and not _is_non_empty_string(metadata["parallel_group"]):
        issues.append(ContractIssue("parallel_group_invalid", f"{path}.metadata.parallel_group"))
    if "join_mode" in metadata and metadata["join_mode"] not in JOIN_MODES:
        issues.append(ContractIssue("join_mode_invalid", f"{path}.metadata.join_mode"))
    if "failure_policy" in metadata and metadata["failure_policy"] not in NODE_FAILURE_POLICIES:
        issues.append(ContractIssue("node_failure_policy_invalid", f"{path}.metadata.failure_policy"))
    if "model_routing" in metadata:
        try:
            ModelRoutingConfig.assert_runtime_mapping(metadata["model_routing"])
        except ModelRoutingContractError as exc:
            issues.append(
                ContractIssue(
                    exc.reason_code,
                    f"{path}.metadata.model_routing",
                    exc.detail,
                )
            )
        except Exception as exc:
            issues.append(
                ContractIssue(
                    "model_routing_invalid",
                    f"{path}.metadata.model_routing",
                    type(exc).__name__,
                )
            )

    merge_fields = {"merge_strategy", "partial_failure"}.intersection(metadata)
    if node.node_type != "merge" and merge_fields:
        issues.append(
            ContractIssue(
                "merge_metadata_on_non_merge_node",
                f"{path}.metadata",
                ",".join(sorted(merge_fields)),
            )
        )
    if node.node_type == "merge":
        strategy = metadata.get("merge_strategy")
        partial_failure = metadata.get("partial_failure", "fail")
        if strategy not in MERGE_STRATEGIES:
            issues.append(ContractIssue("merge_strategy_unsupported", f"{path}.metadata.merge_strategy"))
        if partial_failure not in MERGE_PARTIAL_FAILURE_POLICIES:
            issues.append(
                ContractIssue(
                    "merge_partial_failure_policy_invalid",
                    f"{path}.metadata.partial_failure",
                )
            )
    return tuple(issues)


def _validate_artifact_contracts(
    artifacts: tuple[ArtifactContract, ...],
    nodes: tuple[ExecutionNode, ...],
) -> tuple[set[str], tuple[ContractIssue, ...]]:
    issues: list[ContractIssue] = []
    artifact_ids = [artifact.artifact_id for artifact in artifacts]
    known_artifacts = set(artifact_ids)
    if "" in known_artifacts:
        issues.append(ContractIssue("artifact_id_required", "artifacts"))
    if len(artifact_ids) != len(known_artifacts):
        issues.append(ContractIssue("duplicate_artifact_id", "artifacts"))
    producers: dict[str, list[str]] = {}
    for node in nodes:
        for artifact_id in node.output_artifacts:
            producers.setdefault(artifact_id, []).append(node.node_id)
    issues.extend(
        ContractIssue(
            "artifact_multiple_producers",
            "nodes",
            f"{artifact_id}:{','.join(sorted(node_ids))}",
        )
        for artifact_id, node_ids in sorted(producers.items())
        if len(node_ids) > 1
    )
    return known_artifacts, tuple(issues)


def _validate_merge_sources(
    nodes: tuple[ExecutionNode, ...],
    edges: tuple[ExecutionEdge, ...],
) -> tuple[ContractIssue, ...]:
    targets = {edge.target for edge in edges}
    return tuple(
        ContractIssue("merge_sources_required", f"nodes[{index}]")
        for index, node in enumerate(nodes)
        if node.node_type == "merge" and node.node_id not in targets
    )


def _validate_plan_runtime_metadata(metadata: dict[str, Any]) -> tuple[ContractIssue, ...]:
    if "parallel_limit" not in metadata:
        return ()
    return _validate_positive_integer(
        metadata["parallel_limit"],
        path="metadata.parallel_limit",
        code="plan_parallel_limit_invalid",
    )


def _validate_parallel_groups(nodes: tuple[ExecutionNode, ...]) -> tuple[ContractIssue, ...]:
    limits: dict[str, set[int]] = {}
    for node in nodes:
        if "parallel_limit" not in node.metadata:
            continue
        group = str(node.metadata.get("parallel_group") or "default")
        value = node.metadata["parallel_limit"]
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        limits.setdefault(group, set()).add(value)
    return tuple(
        ContractIssue("parallel_group_limit_conflict", "nodes", group)
        for group, values in sorted(limits.items())
        if len(values) > 1
    )


def _validate_positive_integer(value: Any, *, path: str, code: str) -> tuple[ContractIssue, ...]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return (ContractIssue(code, path),)
    return ()


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


EXECUTION_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": EXECUTION_PLAN_SCHEMA,
    "type": "object",
    "required": [
        "schema",
        "tenant_id",
        "plan_id",
        "workflow_id",
        "policy_version",
        "nodes",
        "edges",
        "capabilities",
        "gates",
        "artifacts",
        "budget",
        "metadata",
        "plan_hash",
    ],
    "properties": {
        "schema": {"const": EXECUTION_PLAN_SCHEMA},
        "tenant_id": {"type": "string", "minLength": 1},
        "plan_id": {"type": "string", "minLength": 1},
        "workflow_id": {"type": "string", "minLength": 1},
        "policy_version": {"type": "string", "minLength": 1},
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/executionNode"},
        },
        "edges": {"type": "array", "items": {"type": "object"}},
        "capabilities": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "gates": {"type": "array", "items": {"type": "object"}},
        "artifacts": {"type": "array", "items": {"type": "object"}},
        "budget": {"type": "object"},
        "metadata": {
            "type": "object",
            "properties": {"parallel_limit": {"type": "integer", "minimum": 1}},
        },
        "plan_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
    "$defs": {
        "executionNode": {
            "type": "object",
            "required": [
                "node_id",
                "node_type",
                "task_kind",
                "required_capabilities",
                "allowed_tools",
                "input_artifacts",
                "output_artifacts",
                "gate_id",
                "side_effect_class",
                "budget",
                "metadata",
            ],
            "properties": {
                "node_id": {"type": "string", "minLength": 1},
                "node_type": {"type": "string", "minLength": 1},
                "task_kind": {"type": "string", "minLength": 1},
                "required_capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "input_artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "output_artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "gate_id": {"type": "string"},
                "side_effect_class": {"enum": sorted(SIDE_EFFECT_CLASSES)},
                "budget": {"type": ["object", "null"]},
                "metadata": {
                    "type": "object",
                    "properties": {
                        "parallel_group": {"type": "string", "minLength": 1},
                        "parallel_limit": {"type": "integer", "minimum": 1},
                        "join_mode": {"enum": sorted(JOIN_MODES)},
                        "failure_policy": {"enum": sorted(NODE_FAILURE_POLICIES)},
                        "merge_strategy": {"enum": sorted(MERGE_STRATEGIES)},
                        "partial_failure": {
                            "enum": sorted(MERGE_PARTIAL_FAILURE_POLICIES)
                        },
                        "model_routing": MODEL_ROUTING_JSON_SCHEMA,
                    },
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"node_type": {"const": "merge"}},
                        "required": ["node_type"],
                    },
                    "then": {
                        "properties": {
                            "metadata": {"required": ["merge_strategy"]}
                        }
                    },
                }
            ],
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}
