"""Config graph models: constants, schema identifiers and data classes.

Extracted from config_graph_builder_service.py (VACGE-001/002, FSR-M09).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

GRAPH_SCHEMA = "ananta_configuration_graph.v1"

# ── Node types ────────────────────────────────────────────────────────────────
NODE_SURFACE = "surface"
NODE_GOAL_TEMPLATE = "goal_template"
NODE_TASK_KIND = "task_kind"
NODE_SUBTASK_STEP = "subtask_step"
NODE_AGENT_PROFILE = "agent_profile"
NODE_ROLE = "role"
NODE_AGENT_INSTANCE = "agent_instance"
NODE_WORKER_BACKEND = "worker_backend"
NODE_MODEL_PROVIDER = "model_provider"
NODE_MODEL_PROFILE = "model_profile"
NODE_EMBEDDING_MODEL = "embedding_model"
NODE_RESTRICTED_INFERENCE_ROOT = "restricted_inference"
NODE_RESTRICTED_INFERENCE_MODEL = "restricted_inference_model"
NODE_RESTRICTED_INFERENCE_TASK = "restricted_inference_task"
NODE_CODECOMPASS_RANKING = "codecompass_ranking"
NODE_TOOL = "tool"
NODE_TOOL_GROUP = "tool_group"
NODE_POLICY = "policy"
NODE_PATH_RULE = "path_rule"
NODE_CONTEXT_SOURCE = "context_source"
NODE_CODECOMPASS_PROFILE = "codecompass_profile"
NODE_RAG_PROFILE = "rag_profile"
NODE_ARTIFACT_RULE = "artifact_rule"
NODE_WRITE_RULE = "write_rule"
NODE_VERIFICATION_RULE = "verification_rule"
NODE_HANDOFF_RULE = "handoff_rule"
NODE_INSTRUCTION_LAYER = "instruction_layer"
NODE_RUNTIME_OVERRIDE = "runtime_override"
NODE_TRACE_EVENT = "trace_event"
NODE_HUB = "hub"
NODE_WORKER_INSTANCE = "worker_instance"
NODE_WORKER_ADAPTER = "worker_adapter"
NODE_TASKFLOW = "taskflow"
NODE_TASKFLOW_STEP = "taskflow_step"
NODE_TEMPLATE_VARIANT = "template_variant"
NODE_ROUTING_RULE = "routing_rule"
NODE_FALLBACK_CHAIN = "fallback_chain"
NODE_PATH_CONFIG_BUNDLE = "path_config_bundle"
NODE_CLONE_SOURCE = "clone_source"

ALL_NODE_TYPES = frozenset({
    NODE_SURFACE, NODE_GOAL_TEMPLATE, NODE_TASK_KIND, NODE_SUBTASK_STEP,
    NODE_AGENT_PROFILE, NODE_ROLE, NODE_AGENT_INSTANCE, NODE_WORKER_BACKEND,
    NODE_MODEL_PROVIDER, NODE_MODEL_PROFILE, NODE_EMBEDDING_MODEL,
    NODE_RESTRICTED_INFERENCE_ROOT, NODE_RESTRICTED_INFERENCE_MODEL,
    NODE_RESTRICTED_INFERENCE_TASK, NODE_CODECOMPASS_RANKING,
    NODE_TOOL, NODE_TOOL_GROUP, NODE_POLICY,
    NODE_PATH_RULE, NODE_CONTEXT_SOURCE, NODE_CODECOMPASS_PROFILE, NODE_RAG_PROFILE,
    NODE_ARTIFACT_RULE, NODE_WRITE_RULE, NODE_VERIFICATION_RULE, NODE_HANDOFF_RULE,
    NODE_INSTRUCTION_LAYER, NODE_RUNTIME_OVERRIDE, NODE_TRACE_EVENT,
    NODE_HUB, NODE_WORKER_INSTANCE, NODE_WORKER_ADAPTER, NODE_TASKFLOW,
    NODE_TASKFLOW_STEP, NODE_TEMPLATE_VARIANT, NODE_ROUTING_RULE,
    NODE_FALLBACK_CHAIN, NODE_PATH_CONFIG_BUNDLE, NODE_CLONE_SOURCE,
})

# ── Edge types ────────────────────────────────────────────────────────────────
EDGE_ACTIVATES = "activates"
EDGE_CONTAINS = "contains"
EDGE_INHERITS_FROM = "inherits_from"
EDGE_OVERRIDES = "overrides"
EDGE_USES_PROFILE = "uses_profile"
EDGE_USES_TEMPLATE = "uses_template"
EDGE_CREATES_SUBTASK = "creates_subtask"
EDGE_ASSIGNED_TO = "assigned_to"
EDGE_MAY_CALL_TOOL = "may_call_tool"
EDGE_BLOCKED_BY_POLICY = "blocked_by_policy"
EDGE_REQUIRES_APPROVAL = "requires_approval"
EDGE_USES_CONTEXT_SOURCE = "uses_context_source"
EDGE_USES_MODEL = "uses_model"
EDGE_USES_EMBEDDING_MODEL = "uses_embedding_model"
EDGE_USES_RESTRICTED_INFERENCE = "uses_restricted_inference"
EDGE_ROUTES_TO_BACKEND = "routes_to_backend"
EDGE_HANDS_OFF_TO = "hands_off_to"
EDGE_DEPENDS_ON = "depends_on"
EDGE_VERIFIES_WITH = "verifies_with"
EDGE_READS_PATH = "reads_path"
EDGE_WRITES_PATH = "writes_path"
EDGE_APPLIES_TO_PATH = "applies_to_path"
EDGE_EMITS_TRACE = "emits_trace"
EDGE_EFFECTIVE_AFTER_MERGE = "effective_after_merge"
EDGE_CONTROLS_WORKER = "controls_worker"
EDGE_ROUTES_TASK_TO_WORKER = "routes_task_to_worker"
EDGE_USES_TEMPLATE_VARIANT = "uses_template_variant"
EDGE_EXECUTES_STEP = "executes_step"
EDGE_HANDS_OFF_ARTIFACT_TO = "hands_off_artifact_to"
EDGE_FALLS_BACK_TO = "falls_back_to"
EDGE_CLONED_FROM = "cloned_from"
EDGE_APPLIES_TO_PATH_BUNDLE = "applies_to_path_bundle"
EDGE_USES_HUB_DEFAULT = "uses_hub_default"
EDGE_OVERRIDES_HUB_DEFAULT = "overrides_hub_default"

ALL_EDGE_TYPES = frozenset({
    EDGE_ACTIVATES, EDGE_CONTAINS, EDGE_INHERITS_FROM, EDGE_OVERRIDES,
    EDGE_USES_PROFILE, EDGE_USES_TEMPLATE, EDGE_CREATES_SUBTASK, EDGE_ASSIGNED_TO,
    EDGE_MAY_CALL_TOOL, EDGE_BLOCKED_BY_POLICY, EDGE_REQUIRES_APPROVAL,
    EDGE_USES_CONTEXT_SOURCE, EDGE_USES_MODEL, EDGE_USES_EMBEDDING_MODEL,
    EDGE_USES_RESTRICTED_INFERENCE, EDGE_ROUTES_TO_BACKEND, EDGE_HANDS_OFF_TO,
    EDGE_DEPENDS_ON, EDGE_VERIFIES_WITH, EDGE_READS_PATH, EDGE_WRITES_PATH,
    EDGE_APPLIES_TO_PATH, EDGE_EMITS_TRACE, EDGE_EFFECTIVE_AFTER_MERGE,
    EDGE_CONTROLS_WORKER, EDGE_ROUTES_TASK_TO_WORKER, EDGE_USES_TEMPLATE_VARIANT,
    EDGE_EXECUTES_STEP, EDGE_HANDS_OFF_ARTIFACT_TO, EDGE_FALLS_BACK_TO,
    EDGE_CLONED_FROM, EDGE_APPLIES_TO_PATH_BUNDLE, EDGE_USES_HUB_DEFAULT,
    EDGE_OVERRIDES_HUB_DEFAULT,
})

# ── View IDs ──────────────────────────────────────────────────────────────────
VIEW_PROFILE_ACTIVATION = "profile_activation_view"
VIEW_PLANNING_FLOW = "planning_flow_view"
VIEW_AGENT_RUNTIME = "agent_runtime_view"
VIEW_POLICY_PATH = "policy_path_view"
VIEW_CONTEXT_PIPELINE = "context_pipeline_view"
VIEW_EFFECTIVE_CONFIG = "effective_config_view"
VIEW_CONFIGURATION_OVERVIEW = "configuration_overview_view"

VIEW_IDS = {
    "configurationOverview": VIEW_CONFIGURATION_OVERVIEW,
    "profileActivation": VIEW_PROFILE_ACTIVATION,
    "planningFlow": VIEW_PLANNING_FLOW,
    "agentRuntime": VIEW_AGENT_RUNTIME,
    "policyPath": VIEW_POLICY_PATH,
    "contextPipeline": VIEW_CONTEXT_PIPELINE,
    "effectiveConfig": VIEW_EFFECTIVE_CONFIG,
}

# ── Path-character classification ─────────────────────────────────────────────
#
# A "path character" describes the operational nature of a profile or rule —
# independent of whether it uses LLM or deterministic processing.
#
PATH_CHARACTER_TEST        = "test"        # test design, TDD, coverage
PATH_CHARACTER_ANALYSIS    = "analysis"    # repo analysis, review, diagnostics
PATH_CHARACTER_OPS         = "ops"         # incident, admin repair
PATH_CHARACTER_MAINTENANCE = "maintenance" # bug fix, patch, refactor
PATH_CHARACTER_CREATIVE    = "creative"    # new project, feature, evolution
PATH_CHARACTER_EXPLAIN     = "explain"     # explanation / chat surfaces
PATH_CHARACTER_UNKNOWN     = "unknown"

_ROLE_CHARACTER_RULES: list[tuple[tuple[str, ...], str]] = [
    (("test", "tdd", "verify", "coverage"), PATH_CHARACTER_TEST),
    (("analys", "review", "diag", "audit", "evidenc"), PATH_CHARACTER_ANALYSIS),
    (("repair", "incident", "admin", "triage", "mitigation"), PATH_CHARACTER_OPS),
    (("fix", "patch", "refactor", "behavior_preserv"), PATH_CHARACTER_MAINTENANCE),
    (("explain", "chat", "explainer"), PATH_CHARACTER_EXPLAIN),  # before creative: "architecture_explainer"
    (("project", "feature", "evolution", "architect", "implement", "bounded"), PATH_CHARACTER_CREATIVE),
]

PATH_CHARACTER_LABELS: dict[str, str] = {
    PATH_CHARACTER_TEST:        "Testpfad",
    PATH_CHARACTER_ANALYSIS:    "Analysepfad",
    PATH_CHARACTER_OPS:         "Betriebspfad",
    PATH_CHARACTER_MAINTENANCE: "Wartungspfad",
    PATH_CHARACTER_CREATIVE:    "Entwicklungspfad",
    PATH_CHARACTER_EXPLAIN:     "Erklärpfad",
    PATH_CHARACTER_UNKNOWN:     "Allgemein",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ConfigGraphNode:
    id: str
    node_type: str
    label: str
    source_file: str | None = None
    source_line: int | None = None
    source_kind: str | None = None
    source_pointer: str | None = None
    writable: bool = False
    runtime_source: str | None = None
    runtime_active: bool = True
    stale: bool = False
    effective_value: Any = None
    declared_value: Any = None
    data: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "label": self.label,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_kind": self.source_kind,
            "source_pointer": self.source_pointer,
            "writable": self.writable,
            "runtime_source": self.runtime_source,
            "runtime_active": self.runtime_active,
            "stale": self.stale,
            "effective_value": self.effective_value,
            "declared_value": self.declared_value,
            "data": self.data,
            "diagnostics": self.diagnostics,
        }


@dataclass
class ConfigGraphEdge:
    source: str
    target: str
    edge_type: str
    priority: int = 0
    condition: str | None = None
    policy_effect: str | None = None
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "priority": self.priority,
            "condition": self.condition,
            "policy_effect": self.policy_effect,
            "source_ref": self.source_ref,
        }


@dataclass
class ConfigGraph:
    schema: str = GRAPH_SCHEMA
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    nodes: dict[str, ConfigGraphNode] = field(default_factory=dict)
    edges: list[ConfigGraphEdge] = field(default_factory=list)
    views: dict[str, list[str]] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def add_node(self, node: ConfigGraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: ConfigGraphEdge) -> None:
        if edge.source in self.nodes and edge.target in self.nodes:
            self.edges.append(edge)

    def add_to_view(self, view_id: str, node_id: str) -> None:
        self.views.setdefault(view_id, [])
        if node_id not in self.views[view_id]:
            self.views[view_id].append(node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "snapshot_id": self.snapshot_id,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "views": self.views,
            "diagnostics": self.diagnostics,
            "generated_at": self.generated_at,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }
