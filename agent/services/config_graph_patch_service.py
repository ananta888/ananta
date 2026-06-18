"""VACGE-007: ConfigGraphPatchService + validation.

Validates and applies patches to the Ananta config graph.
Patches may modify node data or add/remove edges.

Risk tiers
----------
LOW      : read-only / additive changes
MEDIUM   : policy changes, profile reassignment
HIGH     : blocking or removing active nodes, path rule changes
CRITICAL : removing instruction layers, changing root config
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from agent.services.config_graph_builder_service import (
    ALL_EDGE_TYPES,
    ALL_NODE_TYPES,
    ConfigGraph,
    ConfigGraphEdge,
    EDGE_ASSIGNED_TO,
    EDGE_BLOCKED_BY_POLICY,
    EDGE_CONTAINS,
    EDGE_MAY_CALL_TOOL,
    EDGE_REQUIRES_APPROVAL,
    EDGE_USES_PROFILE,
    NODE_INSTRUCTION_LAYER,
    NODE_PATH_RULE,
    NODE_POLICY,
    NODE_AGENT_PROFILE,
    NODE_ROLE,
    NODE_SURFACE,
    NODE_TOOL,
    NODE_TOOL_GROUP,
)

# Risk tiers
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"

_RISK_ORDER = [RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL]


@dataclass
class PatchOp:
    """One atomic patch operation."""

    op: str  # set_data / add_edge / remove_edge / remove_node / add_node
    target: str  # node_id or edge id
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "target": self.target, "data": self.data}


@dataclass
class ValidationResult:
    valid: bool = True
    risk_tier: str = RISK_LOW
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_approval: bool = False
    risk_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "risk_tier": self.risk_tier,
            "errors": self.errors,
            "warnings": self.warnings,
            "requires_approval": self.requires_approval,
            "risk_score": self.risk_score,
        }


@dataclass
class ApplyResult:
    success: bool = True
    applied_ops: list[dict[str, Any]] = field(default_factory=list)
    skipped_ops: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    new_snapshot_id: str = ""
    source_diffs: list[dict[str, Any]] = field(default_factory=list)
    rollback_artifact: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "applied_ops": self.applied_ops,
            "skipped_ops": self.skipped_ops,
            "errors": self.errors,
            "new_snapshot_id": self.new_snapshot_id,
            "source_diffs": self.source_diffs,
            "rollback_artifact": self.rollback_artifact,
        }


class ConfigGraphPatchService:
    """Validates and applies structural patches to a ConfigGraph.

    Parameters
    ----------
    require_approval_above:
        Risk tier that triggers approval requirement. Default: ``high``.
    max_ops_per_patch:
        Safety limit on batch patch size. Default: 50.
    """

    def __init__(
        self,
        *,
        require_approval_above: str = RISK_HIGH,
        max_ops_per_patch: int = 50,
    ) -> None:
        self._approval_threshold = require_approval_above
        self._max_ops = max_ops_per_patch

    def validate(self, graph: ConfigGraph, ops: list[PatchOp]) -> ValidationResult:
        """Validate a list of patch ops against the graph without applying them."""
        result = ValidationResult()

        if not ops:
            result.warnings.append("Empty patch list")
            return result

        if len(ops) > self._max_ops:
            result.valid = False
            result.errors.append(f"Patch too large: {len(ops)} ops (max {self._max_ops})")
            result.risk_tier = RISK_CRITICAL
            result.risk_score = 1.0
            return result

        op_risks: list[str] = []
        for i, op in enumerate(ops):
            op_result = self._validate_op(graph, op, i)
            result.errors.extend(op_result.errors)
            result.warnings.extend(op_result.warnings)
            op_risks.append(op_result.risk_tier)

        if result.errors:
            result.valid = False

        result.risk_tier = self._max_risk(op_risks) if op_risks else RISK_LOW
        result.risk_score = self._tier_score(result.risk_tier)

        # Approval required?
        threshold_idx = _RISK_ORDER.index(self._approval_threshold)
        tier_idx = _RISK_ORDER.index(result.risk_tier)
        result.requires_approval = tier_idx >= threshold_idx

        return result

    def apply(
        self,
        graph: ConfigGraph,
        ops: list[PatchOp],
        *,
        skip_validation: bool = False,
    ) -> ApplyResult:
        """Apply ops to a graph in-place. Returns an ApplyResult."""
        import uuid

        result = ApplyResult()

        if not skip_validation:
            val = self.validate(graph, ops)
            if not val.valid:
                result.success = False
                result.errors.extend(val.errors)
                return result
            if val.requires_approval:
                result.success = False
                result.errors.append(
                    f"Patch requires approval (risk={val.risk_tier}). "
                    "Submit via /api/config-graph/apply-patch with approval token."
                )
                return result

        for op in ops:
            try:
                self._apply_op(graph, op)
                result.applied_ops.append(op.to_dict())
            except Exception as exc:
                result.errors.append(f"op {op.op} on {op.target}: {exc}")
                result.skipped_ops.append(op.to_dict())

        if result.errors:
            result.success = False

        result.new_snapshot_id = str(uuid.uuid4())[:12]
        graph.snapshot_id = result.new_snapshot_id
        return result

    def apply_approved(
        self, graph: ConfigGraph, ops: list[PatchOp], approval_token: str
    ) -> ApplyResult:
        """Apply high/critical ops with an approval token (VACGE-007)."""
        val = self.validate(graph, ops)
        if not val.valid:
            result = ApplyResult(success=False)
            result.errors.extend(val.errors)
            return result
        from agent.services.config_graph_approval_service import ConfigGraphApprovalService

        approval = ConfigGraphApprovalService().validate(
            ops=[op.to_dict() for op in ops],
            risk_tier=val.risk_tier,
            approval_token=approval_token,
        )
        if not approval.approved:
            result = ApplyResult(success=False)
            result.errors.append(f"Invalid approval token: {approval.reason_code}")
            return result
        return self.apply(graph, ops, skip_validation=True)

    # ── Op dispatch ───────────────────────────────────────────────────────────

    def _validate_op(self, graph: ConfigGraph, op: PatchOp, idx: int) -> ValidationResult:
        r = ValidationResult()
        dispatch = {
            "set_data": self._validate_set_data,
            "add_edge": self._validate_add_edge,
            "remove_edge": self._validate_remove_edge,
            "remove_node": self._validate_remove_node,
            "add_node": self._validate_add_node,
        }
        fn = dispatch.get(op.op)
        if fn is None:
            r.valid = False
            r.errors.append(f"op[{idx}]: unknown op '{op.op}'")
            r.risk_tier = RISK_CRITICAL
            return r
        return fn(graph, op, idx)

    def _validate_set_data(
        self, graph: ConfigGraph, op: PatchOp, idx: int
    ) -> ValidationResult:
        r = ValidationResult()
        node = graph.nodes.get(op.target)
        if node is None:
            r.valid = False
            r.errors.append(f"op[{idx}] set_data: node '{op.target}' not found")
            return r
        if node.node_type == NODE_INSTRUCTION_LAYER and "content" in op.data:
            r.risk_tier = RISK_CRITICAL
            r.warnings.append(f"op[{idx}]: modifying instruction layer content is high-risk")
        elif node.node_type in (NODE_PATH_RULE, NODE_POLICY):
            r.risk_tier = RISK_HIGH
        else:
            r.risk_tier = RISK_MEDIUM
        return r

    def _validate_add_edge(
        self, graph: ConfigGraph, op: PatchOp, idx: int
    ) -> ValidationResult:
        r = ValidationResult()
        src = op.data.get("source") or ""
        tgt = op.data.get("target") or ""
        etype = op.data.get("edge_type") or ""
        if not src or not tgt or not etype:
            r.valid = False
            r.errors.append(f"op[{idx}] add_edge: requires source, target, edge_type in data")
            return r
        if src not in graph.nodes:
            r.valid = False
            r.errors.append(f"op[{idx}] add_edge: source node '{src}' not found")
        if tgt not in graph.nodes:
            r.valid = False
            r.errors.append(f"op[{idx}] add_edge: target node '{tgt}' not found")
        if etype not in ALL_EDGE_TYPES:
            r.valid = False
            r.errors.append(f"op[{idx}] add_edge: unknown edge_type '{etype}'")
        if src in graph.nodes and tgt in graph.nodes and etype in ALL_EDGE_TYPES:
            semantic_error = self._edge_semantic_error(
                graph.nodes[src].node_type,
                etype,
                graph.nodes[tgt].node_type,
            )
            if semantic_error:
                r.valid = False
                r.errors.append(f"op[{idx}] add_edge: {semantic_error}")
        r.risk_tier = RISK_MEDIUM
        return r

    def _validate_remove_edge(
        self, graph: ConfigGraph, op: PatchOp, idx: int
    ) -> ValidationResult:
        r = ValidationResult()
        src = op.data.get("source") or ""
        tgt = op.data.get("target") or ""
        etype = op.data.get("edge_type") or ""
        found = any(
            e.source == src and e.target == tgt and e.edge_type == etype
            for e in graph.edges
        )
        if not found:
            r.warnings.append(f"op[{idx}] remove_edge: edge not found (no-op)")
        r.risk_tier = RISK_MEDIUM
        return r

    def _validate_remove_node(
        self, graph: ConfigGraph, op: PatchOp, idx: int
    ) -> ValidationResult:
        r = ValidationResult()
        node = graph.nodes.get(op.target)
        if node is None:
            r.warnings.append(f"op[{idx}] remove_node: '{op.target}' not found (no-op)")
            return r
        if node.node_type == NODE_INSTRUCTION_LAYER and "::root" in op.target:
            r.valid = False
            r.errors.append("op remove_node: cannot remove root instruction layer")
            r.risk_tier = RISK_CRITICAL
        elif node.node_type in (NODE_INSTRUCTION_LAYER, NODE_PATH_RULE):
            r.risk_tier = RISK_CRITICAL
            r.warnings.append(f"op[{idx}]: removing a {node.node_type} node is critical-risk")
        else:
            r.risk_tier = RISK_HIGH
        return r

    def _validate_add_node(
        self, graph: ConfigGraph, op: PatchOp, idx: int
    ) -> ValidationResult:
        r = ValidationResult()
        nid = str(op.data.get("id") or op.target or "")
        ntype = str(op.data.get("node_type") or "")
        if not nid or not ntype:
            r.valid = False
            r.errors.append(f"op[{idx}] add_node: requires id and node_type in data")
            return r
        if ntype not in ALL_NODE_TYPES:
            r.valid = False
            r.errors.append(f"op[{idx}] add_node: unknown node_type '{ntype}'")
            r.risk_tier = RISK_HIGH
            return r
        if nid in graph.nodes:
            r.warnings.append(f"op[{idx}] add_node: node '{nid}' already exists (will overwrite)")
        r.risk_tier = RISK_LOW
        return r

    # ── Apply ops ─────────────────────────────────────────────────────────────

    def _apply_op(self, graph: ConfigGraph, op: PatchOp) -> None:
        if op.op == "set_data":
            node = graph.nodes[op.target]
            node.data.update(op.data)
        elif op.op == "add_edge":
            graph.add_edge(ConfigGraphEdge(
                source=str(op.data["source"]),
                target=str(op.data["target"]),
                edge_type=str(op.data["edge_type"]),
                priority=int(op.data.get("priority") or 0),
                condition=op.data.get("condition"),
                policy_effect=op.data.get("policy_effect"),
            ))
        elif op.op == "remove_edge":
            src = op.data.get("source")
            tgt = op.data.get("target")
            etype = op.data.get("edge_type")
            graph.edges = [
                e for e in graph.edges
                if not (e.source == src and e.target == tgt and e.edge_type == etype)
            ]
        elif op.op == "remove_node":
            graph.nodes.pop(op.target, None)
            graph.edges = [
                e for e in graph.edges
                if e.source != op.target and e.target != op.target
            ]
        elif op.op == "add_node":
            from agent.services.config_graph_builder_service import ConfigGraphNode
            node = ConfigGraphNode(
                id=str(op.data.get("id") or op.target),
                node_type=str(op.data.get("node_type") or ""),
                label=str(op.data.get("label") or ""),
                source_file=op.data.get("source_file"),
                runtime_active=bool(op.data.get("runtime_active", True)),
                data=dict(op.data.get("data") or {}),
            )
            graph.add_node(node)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _max_risk(risks: list[str]) -> str:
        max_idx = max((_RISK_ORDER.index(r) for r in risks if r in _RISK_ORDER), default=0)
        return _RISK_ORDER[max_idx]

    @staticmethod
    def _tier_score(tier: str) -> float:
        scores = {RISK_LOW: 0.1, RISK_MEDIUM: 0.35, RISK_HIGH: 0.7, RISK_CRITICAL: 1.0}
        return scores.get(tier, 0.0)

    @staticmethod
    def _edge_semantic_error(source_type: str, edge_type: str, target_type: str) -> str | None:
        allowed = {
            EDGE_CONTAINS: {
                (NODE_AGENT_PROFILE, NODE_INSTRUCTION_LAYER),
                (NODE_TOOL_GROUP, NODE_TOOL),
            },
            EDGE_USES_PROFILE: {(NODE_SURFACE, NODE_AGENT_PROFILE)},
            EDGE_ASSIGNED_TO: {(NODE_AGENT_PROFILE, NODE_ROLE)},
            EDGE_MAY_CALL_TOOL: {
                (NODE_AGENT_PROFILE, NODE_TOOL),
                (NODE_AGENT_PROFILE, NODE_TOOL_GROUP),
            },
            EDGE_REQUIRES_APPROVAL: {
                (NODE_TOOL, NODE_POLICY),
                (NODE_PATH_RULE, NODE_POLICY),
            },
            EDGE_BLOCKED_BY_POLICY: {
                (NODE_AGENT_PROFILE, NODE_POLICY),
                (NODE_PATH_RULE, NODE_POLICY),
            },
        }
        allowed_pairs = allowed.get(edge_type)
        if allowed_pairs is None:
            return None
        if (source_type, target_type) not in allowed_pairs:
            return (
                f"edge_type '{edge_type}' is not allowed from "
                f"{source_type} to {target_type}"
            )
        return None
