"""VACGE-003: EffectiveConfigResolver.

Resolves the effective Ananta config for a concrete (surface, task_kind, path)
tuple by applying the merge order:
  root AGENTS.md → profile AGENTS.md → agent_profile → goal_template
  → context bundle → runtime overrides

No LLM is called. All logic is purely structural graph traversal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.config_graph_builder_service import (
    ConfigGraph,
    EDGE_ACTIVATES,
    EDGE_ASSIGNED_TO,
    EDGE_BLOCKED_BY_POLICY,
    EDGE_CONTAINS,
    EDGE_EFFECTIVE_AFTER_MERGE,
    EDGE_INHERITS_FROM,
    EDGE_MAY_CALL_TOOL,
    EDGE_USES_PROFILE,
    EDGE_USES_TEMPLATE,
    NODE_AGENT_PROFILE,
    NODE_GOAL_TEMPLATE,
    NODE_INSTRUCTION_LAYER,
    NODE_PATH_RULE,
    NODE_POLICY,
    NODE_TOOL,
    NODE_TOOL_GROUP,
)


@dataclass
class EffectiveConfig:
    """Resolved effective config snapshot for one (surface, task_kind, path) tuple."""

    surface: str
    task_kind: str | None
    path: str | None

    # Resolved instruction layers in merge order
    instruction_layers: list[dict[str, Any]] = field(default_factory=list)
    # Active agent profile (if matched)
    agent_profile: dict[str, Any] | None = None
    # Active goal template (if matched)
    goal_template: dict[str, Any] | None = None
    # Effective AI modes (allowed/blocked) for path
    effective_ai_modes_allowed: list[str] = field(default_factory=list)
    effective_ai_modes_blocked: list[str] = field(default_factory=list)
    # Activated tools
    tools_allowed: list[str] = field(default_factory=list)
    # Tool policy diagnostics
    tool_policy_missing: bool = False
    # Active policies
    policies_active: list[dict[str, Any]] = field(default_factory=list)
    # Merge trace: which nodes contributed in which order
    merge_trace: list[dict[str, Any]] = field(default_factory=list)
    # Warnings from resolution
    warnings: list[str] = field(default_factory=list)
    # Effective nodes (node IDs) in this config slice
    effective_node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "task_kind": self.task_kind,
            "path": self.path,
            "instruction_layers": self.instruction_layers,
            "agent_profile": self.agent_profile,
            "goal_template": self.goal_template,
            "effective_ai_modes_allowed": self.effective_ai_modes_allowed,
            "effective_ai_modes_blocked": self.effective_ai_modes_blocked,
            "tools_allowed": self.tools_allowed,
            "tool_policy_missing": self.tool_policy_missing,
            "policies_active": self.policies_active,
            "merge_trace": self.merge_trace,
            "warnings": self.warnings,
            "effective_node_ids": self.effective_node_ids,
        }


class EffectiveConfigResolver:
    """Resolves effective config from a ConfigGraph snapshot.

    Parameters
    ----------
    graph:
        A pre-built ConfigGraph (from ConfigGraphBuilderService.build()).
    """

    def __init__(self, graph: ConfigGraph) -> None:
        self._graph = graph
        self._edge_index: dict[str, list[Any]] = {}
        self._build_edge_index()

    def _build_edge_index(self) -> None:
        for edge in self._graph.edges:
            self._edge_index.setdefault(edge.source, []).append(edge)
            self._edge_index.setdefault(edge.target, []).append(edge)

    def resolve(
        self,
        *,
        surface: str,
        task_kind: str | None = None,
        path: str | None = None,
    ) -> EffectiveConfig:
        result = EffectiveConfig(surface=surface, task_kind=task_kind, path=path)

        # 1. Root instruction layer (always first)
        root_layer = self._graph.nodes.get("instruction_layer::root")
        if root_layer:
            result.instruction_layers.append({
                "layer": "root",
                "source_file": root_layer.source_file,
                "overridable": False,
            })
            result.merge_trace.append({
                "step": 1, "source": "instruction_layer::root",
                "description": "Root AGENTS.md (global baseline)",
            })
            result.effective_node_ids.append("instruction_layer::root")

        # 2. Agent profile matching
        profile_node_id = self._match_profile(surface, task_kind)
        if profile_node_id:
            pnode = self._graph.nodes[profile_node_id]
            result.agent_profile = {"node_id": profile_node_id, **pnode.data}
            result.effective_node_ids.append(profile_node_id)
            result.merge_trace.append({
                "step": 2, "source": profile_node_id,
                "description": f"Agent profile '{pnode.label}' matched for surface={surface}",
            })

            # Profile instruction layer
            profile_layer_id = f"instruction_layer::{pnode.data.get('profile_id', '')}"
            if profile_layer_id in self._graph.nodes:
                plnode = self._graph.nodes[profile_layer_id]
                result.instruction_layers.append({
                    "layer": "profile",
                    "profile_id": pnode.data.get("profile_id"),
                    "source_file": plnode.source_file,
                    "overridable": True,
                })
                result.effective_node_ids.append(profile_layer_id)
                result.merge_trace.append({
                    "step": 3, "source": profile_layer_id,
                    "description": f"Profile AGENTS.md overrides root for {pnode.label}",
                })

            # Tools allowed via profile
            result.tools_allowed = self._collect_tools_for_profile(profile_node_id)
            if not result.tools_allowed:
                result.tool_policy_missing = True
                result.warnings.append(
                    f"No explicit tool policy for profile {profile_node_id!r}; default-deny applied"
                )

        else:
            result.warnings.append(f"No agent profile matched for surface={surface!r}, task_kind={task_kind!r}")

        # 3. Goal template
        if task_kind:
            tmpl_id = f"goal_template::{task_kind}"
            if tmpl_id in self._graph.nodes:
                tnode = self._graph.nodes[tmpl_id]
                result.goal_template = {"node_id": tmpl_id, **tnode.data}
                if tnode.stale:
                    result.warnings.append(f"goal_template::{task_kind} may be stale (derived from profile map)")
                result.effective_node_ids.append(tmpl_id)
                result.merge_trace.append({
                    "step": 4, "source": tmpl_id,
                    "description": f"Goal template '{task_kind}' applied",
                })

        # 4. Path-based AI mode restrictions
        if path:
            path_rules = self._collect_path_rules(path)
            for rule in path_rules:
                result.effective_node_ids.append(rule["node_id"])
                result.merge_trace.append({
                    "step": 5, "source": rule["node_id"],
                    "description": f"Path rule '{rule['path_glob']}' restricts AI modes",
                })
            if path_rules:
                # Intersection of allowed modes across all matching rules
                all_allowed: set[str] | None = None
                all_blocked: set[str] = set()
                for rule in path_rules:
                    allowed = set(rule.get("allowed_ai_modes") or [])
                    blocked = set(rule.get("blocked_ai_modes") or [])
                    all_blocked |= blocked
                    if allowed:
                        all_allowed = (all_allowed & allowed) if all_allowed is not None else allowed
                result.effective_ai_modes_blocked = sorted(all_blocked)
                result.effective_ai_modes_allowed = sorted(all_allowed or set())
                if all_blocked and not (all_allowed or set()) - all_blocked:
                    result.warnings.append(
                        f"Path {path!r} has all modes blocked — only deterministic ops available"
                    )

        # 5. Active policies
        result.policies_active = self._collect_policies(profile_node_id)

        return result

    def _match_profile(self, surface: str, task_kind: str | None) -> str | None:
        # Direct surface match
        direct_id = f"agent_profile::{surface}"
        if direct_id in self._graph.nodes:
            return direct_id

        # Match via surface → uses_profile edge
        surface_id = f"surface::{surface}"
        if surface_id in self._graph.nodes:
            for edge in self._edge_index.get(surface_id, []):
                if edge.edge_type == EDGE_USES_PROFILE and edge.source == surface_id:
                    if edge.target in self._graph.nodes:
                        return edge.target

        # Match via task_kind
        if task_kind:
            kind_profile_id = f"agent_profile::{task_kind}"
            if kind_profile_id in self._graph.nodes:
                return kind_profile_id

        return None

    def _collect_tools_for_profile(self, profile_node_id: str) -> list[str]:
        tools: list[str] = []
        for edge in self._edge_index.get(profile_node_id, []):
            if edge.edge_type == EDGE_MAY_CALL_TOOL and edge.source == profile_node_id:
                tool_node = self._graph.nodes.get(edge.target)
                if tool_node and tool_node.node_type == NODE_TOOL:
                    tools.append(tool_node.data.get("name") or tool_node.label)

        return sorted(set(tools))

    def _collect_path_rules(self, path: str) -> list[dict[str, Any]]:
        import fnmatch
        matched: list[dict[str, Any]] = []
        norm = path.replace("\\", "/").lstrip("/")
        for nid, node in self._graph.nodes.items():
            if node.node_type != NODE_PATH_RULE or not node.runtime_active:
                continue
            glob = str(node.data.get("path_glob") or "")
            if fnmatch.fnmatch(norm, glob) or (
                glob.endswith("/**") and norm.startswith(glob[:-3])
            ):
                matched.append({"node_id": nid, **node.data})
        return matched

    def _collect_policies(self, profile_node_id: str | None) -> list[dict[str, Any]]:
        policies: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(nid: str) -> None:
            if nid in seen:
                return
            seen.add(nid)
            node = self._graph.nodes.get(nid)
            if node and node.node_type == NODE_POLICY and node.runtime_active:
                policies.append({"node_id": nid, **node.data})

        if profile_node_id:
            for edge in self._edge_index.get(profile_node_id, []):
                if edge.edge_type == EDGE_BLOCKED_BY_POLICY:
                    _add(edge.target)

        for nid, node in self._graph.nodes.items():
            if node.node_type == NODE_POLICY and node.runtime_active:
                _add(nid)

        return policies
