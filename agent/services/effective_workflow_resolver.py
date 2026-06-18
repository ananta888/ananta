"""Effective workflow resolver for the Ananta Config Compass.

The resolver is intentionally read-only.  It combines existing read models and
does not dispatch work, mutate config, or call an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.config_graph_builder_service import (
    ConfigGraph,
    ConfigGraphNode,
    NODE_PATH_RULE,
)
from agent.services.config_graph_effective_resolver import EffectiveConfigResolver
from agent.services.hub_worker_graph_service import HubWorkerGraphService


SCHEMA = "ananta.effective_workflow.v1"


@dataclass
class EffectiveWorkflowNode:
    id: str
    node_type: str
    label: str
    runtime_active: bool = True
    declared_value: Any = None
    effective_value: Any = None
    source_file: str | None = None
    source_kind: str | None = None
    source_pointer: str | None = None
    writable: bool = False
    readonly_reason: str = ""
    reason: str = ""
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    edit_target: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "label": self.label,
            "runtime_active": self.runtime_active,
            "declared_value": self.declared_value,
            "effective_value": self.effective_value,
            "source_file": self.source_file,
            "source_kind": self.source_kind,
            "source_pointer": self.source_pointer,
            "writable": self.writable,
            "readonly_reason": self.readonly_reason,
            "reason": self.reason,
            "diagnostics": self.diagnostics,
            "edit_target": self.edit_target,
            "data": self.data,
        }


@dataclass
class EffectiveWorkflowEdge:
    source: str
    target: str
    edge_type: str
    label: str = ""
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "label": self.label,
            "reason": self.reason,
            "data": self.data,
        }


class EffectiveWorkflowResolver:
    """Build a deterministic explanation for surface/path/task_kind."""

    def resolve(
        self,
        *,
        graph: ConfigGraph,
        user_config: dict[str, Any],
        surface: str,
        path: str | None = None,
        task_kind: str | None = None,
        blueprints: list[dict[str, Any]] | None = None,
        templates: list[dict[str, Any]] | None = None,
        include_readonly: bool = True,
        include_diagnostics: bool = True,
        include_alternatives: bool = True,
    ) -> dict[str, Any]:
        request = {
            "surface": str(surface or "").strip(),
            "path": str(path or "").strip() or None,
            "task_kind": str(task_kind or "").strip() or None,
            "include_readonly": bool(include_readonly),
            "include_diagnostics": bool(include_diagnostics),
            "include_alternatives": bool(include_alternatives),
        }
        nodes: dict[str, EffectiveWorkflowNode] = {}
        edges: list[EffectiveWorkflowEdge] = []
        warnings: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []

        def add_warning(code: str, message: str, **details: Any) -> None:
            warnings.append({
                "severity": "warning",
                "code": code,
                "message": message,
                "details": details,
            })

        request_id = "request::current"
        self._add(nodes, EffectiveWorkflowNode(
            id=request_id,
            node_type="workflow_request",
            label="Workflow Request",
            declared_value=request,
            effective_value=request,
            source_kind="request",
            writable=False,
            readonly_reason="request payload",
            reason="Operator supplied surface/path/task_kind.",
            data=request,
        ))

        effective = EffectiveConfigResolver(graph).resolve(
            surface=request["surface"],
            task_kind=request["task_kind"],
            path=request["path"],
        )

        previous = request_id
        for node_id in effective.effective_node_ids:
            node = graph.nodes.get(node_id)
            if not node:
                continue
            compass_node = self._from_config_node(node)
            self._add(nodes, compass_node)
            edges.append(EffectiveWorkflowEdge(
                source=previous,
                target=compass_node.id,
                edge_type=self._edge_type_for_node(compass_node.node_type),
                label=compass_node.node_type,
                reason=compass_node.reason,
            ))
            previous = compass_node.id

        for item in effective.merge_trace:
            trace.append({
                "step": item.get("step"),
                "node_id": item.get("source"),
                "message": item.get("description"),
                "source_ref": self._source_ref_for_graph_node(graph.nodes.get(str(item.get("source") or ""))),
            })
        for warning in effective.warnings:
            code = "effective_config_warning"
            if "No agent profile matched" in warning:
                code = "unknown_surface_or_profile"
                blocked.append({
                    "code": code,
                    "message": warning,
                    "edit_target": {
                        "editor": "config_graph",
                        "route": "/config-graph",
                        "entity_id": request["surface"],
                    },
                })
            if "No explicit tool policy" in warning:
                code = "missing_tool_policy_default_deny"
            add_warning(code, warning)

        self._add_hub_worker_slice(
            nodes=nodes,
            edges=edges,
            user_config=user_config,
            path=request["path"],
            task_kind=request["task_kind"],
            previous=previous,
            add_warning=add_warning,
        )

        selected_blueprint = self._select_blueprint(
            blueprints or [],
            task_kind=request["task_kind"],
            surface=request["surface"],
            add_warning=add_warning,
        )
        template_by_id = {
            str(tpl.get("id") or ""): tpl
            for tpl in (templates or [])
            if str(tpl.get("id") or "")
        }
        if selected_blueprint:
            self._add_blueprint_slice(
                nodes=nodes,
                edges=edges,
                blueprint=selected_blueprint,
                template_by_id=template_by_id,
                previous=request_id,
            )

        selected = {
            "surface": request["surface"],
            "path_rules": self._selected_nodes(nodes, "path_rule"),
            "instruction_layers": self._selected_nodes(nodes, "instruction_layer"),
            "agent_profile": effective.agent_profile,
            "blueprint": selected_blueprint,
            "team_type": selected_blueprint.get("base_team_type_name") if selected_blueprint else None,
            "roles": selected_blueprint.get("roles", []) if selected_blueprint else [],
            "templates": self._selected_templates(selected_blueprint, template_by_id),
            "taskflow": self._selected_nodes(nodes, "taskflow"),
            "worker_routing": self._selected_nodes(nodes, "worker_instance"),
            "models": self._selected_nodes(nodes, "model_provider"),
            "tools": {
                "allowed": effective.tools_allowed,
                "missing_policy": effective.tool_policy_missing,
            },
            "context_sources": self._selected_nodes(nodes, "context_source"),
            "write_policy": self._write_policy_summary(effective, request["path"]),
            "verification": self._selected_nodes(nodes, "verification_rule"),
        }

        status = "ok"
        if blocked:
            status = "blocked"
        elif warnings:
            status = "warning"

        result = {
            "schema": SCHEMA,
            "request": request,
            "summary": self._summary(request, selected, status),
            "status": status,
            "effective_chain": self._main_chain(nodes, edges, request_id),
            "graph": {
                "nodes": {node_id: node.to_dict() for node_id, node in nodes.items()},
                "edges": [edge.to_dict() for edge in edges],
            },
            "selected": selected,
            "alternatives": self._alternatives(
                blueprints or [],
                selected_blueprint,
                user_config,
                include=include_alternatives,
            ),
            "blocked": blocked,
            "warnings": warnings if include_diagnostics else [],
            "explanation_trace": trace,
            "edit_links": self._edit_links(nodes),
            "source_index": self._source_index(nodes),
        }
        return result

    def options(
        self,
        *,
        graph: ConfigGraph,
        user_config: dict[str, Any],
        blueprints: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        surfaces = sorted(
            node.data.get("surface")
            or node.data.get("surface_id")
            or node.id.removeprefix("surface::")
            or node.label
            for node in graph.nodes.values()
            if node.node_type == "surface"
        )
        task_kinds = sorted(
            node.data.get("task_kind") or node.label
            for node in graph.nodes.values()
            if node.node_type in {"task_kind", "goal_template"}
        )
        hw_graph = HubWorkerGraphService().build(user_config=user_config)
        workers = sorted(
            node["data"].get("worker_id") or node["label"]
            for node in hw_graph.get("nodes", {}).values()
            if node.get("node_type") == "worker_instance"
        )
        path_suggestions = sorted(
            node.data.get("path_glob")
            for node in graph.nodes.values()
            if node.node_type == NODE_PATH_RULE and node.data.get("path_glob")
        )
        return {
            "schema": "ananta.effective_workflow.options.v1",
            "surfaces": surfaces,
            "task_kinds": task_kinds,
            "path_suggestions": path_suggestions,
            "blueprints": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "role_count": len(item.get("roles") or []),
                    "artifact_count": len(item.get("artifacts") or []),
                }
                for item in (blueprints or [])
            ],
            "workers": workers,
        }

    def compare(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        left_selected = dict(left.get("selected") or {})
        right_selected = dict(right.get("selected") or {})
        keys = sorted(set(left_selected) | set(right_selected))
        differences = []
        for key in keys:
            if left_selected.get(key) != right_selected.get(key):
                differences.append({
                    "field": key,
                    "left": left_selected.get(key),
                    "right": right_selected.get(key),
                })
        return {
            "schema": "ananta.effective_workflow.compare.v1",
            "left_request": left.get("request"),
            "right_request": right.get("request"),
            "status": "changed" if differences else "same",
            "differences": differences,
            "left_summary": left.get("summary"),
            "right_summary": right.get("summary"),
        }

    def _from_config_node(self, node: ConfigGraphNode) -> EffectiveWorkflowNode:
        return EffectiveWorkflowNode(
            id=node.id,
            node_type=node.node_type,
            label=node.label,
            runtime_active=node.runtime_active,
            declared_value=node.declared_value if node.declared_value is not None else node.data,
            effective_value=node.effective_value if node.effective_value is not None else node.data,
            source_file=node.source_file,
            source_kind=node.source_kind,
            source_pointer=node.source_pointer,
            writable=node.writable,
            readonly_reason="" if node.writable else "source is readonly or derived",
            reason=f"{node.node_type} participates in the effective configuration.",
            diagnostics=[
                {"severity": "warning", "message": item}
                for item in node.diagnostics
            ],
            edit_target=self._edit_target_for_config_node(node),
            data=node.data,
        )

    def _add_hub_worker_slice(
        self,
        *,
        nodes: dict[str, EffectiveWorkflowNode],
        edges: list[EffectiveWorkflowEdge],
        user_config: dict[str, Any],
        path: str | None,
        task_kind: str | None,
        previous: str,
        add_warning,
    ) -> None:
        hw_graph = HubWorkerGraphService().build(user_config=user_config, path=path)
        for diagnostic in hw_graph.get("diagnostics") or []:
            add_warning("hub_worker_diagnostic", str(diagnostic))
        hub_id = "hub::ananta"
        for node_id, raw in (hw_graph.get("nodes") or {}).items():
            raw_type = str(raw.get("node_type") or "")
            if raw_type not in {"hub", "worker_instance", "fallback_chain", "taskflow", "taskflow_step"}:
                continue
            compass_id = node_id
            self._add(nodes, EffectiveWorkflowNode(
                id=compass_id,
                node_type=raw_type,
                label=str(raw.get("label") or node_id),
                runtime_active=bool(raw.get("runtime_active", True)),
                declared_value=raw.get("data"),
                effective_value=raw.get("data"),
                source_file=raw.get("source_file"),
                source_kind=raw.get("source_kind"),
                source_pointer=raw.get("source_pointer"),
                writable=bool(raw.get("writable")),
                readonly_reason="" if raw.get("writable") else "hub/worker read model",
                reason="Hub/worker routing read model contributes runtime routing.",
                diagnostics=[
                    {"severity": "warning", "message": item}
                    for item in (raw.get("diagnostics") or [])
                ],
                edit_target={
                    "editor": "hub_worker_graph",
                    "route": "/hub-worker-graph",
                    "entity_id": node_id,
                    "source_ref": {
                        "source_file": raw.get("source_file"),
                        "source_kind": raw.get("source_kind"),
                        "source_pointer": raw.get("source_pointer"),
                    },
                },
                data=dict(raw.get("data") or {}),
            ))
        if hub_id in nodes and previous in nodes:
            edges.append(EffectiveWorkflowEdge(
                source=previous,
                target=hub_id,
                edge_type="routes_to_worker",
                label="Routing",
                reason="Hub is the control plane for worker routing.",
            ))
        for raw_edge in hw_graph.get("edges") or []:
            source = raw_edge.get("source")
            target = raw_edge.get("target")
            if source in nodes and target in nodes:
                if task_kind and raw_edge.get("data", {}).get("task_kind") not in {None, "", task_kind}:
                    continue
                edges.append(EffectiveWorkflowEdge(
                    source=source,
                    target=target,
                    edge_type=str(raw_edge.get("edge_type") or ""),
                    label=str(raw_edge.get("label") or ""),
                    reason="Derived from HubWorkerGraphService.",
                    data=dict(raw_edge.get("data") or {}),
                ))

    def _add_blueprint_slice(
        self,
        *,
        nodes: dict[str, EffectiveWorkflowNode],
        edges: list[EffectiveWorkflowEdge],
        blueprint: dict[str, Any],
        template_by_id: dict[str, dict[str, Any]],
        previous: str,
    ) -> None:
        blueprint_id = f"blueprint::{blueprint.get('id')}"
        self._add(nodes, EffectiveWorkflowNode(
            id=blueprint_id,
            node_type="blueprint",
            label=str(blueprint.get("name") or blueprint_id),
            declared_value=blueprint,
            effective_value=blueprint,
            source_kind="hub_api",
            source_pointer=f"/teams/blueprints/{blueprint.get('id')}",
            writable=not bool(blueprint.get("is_seed")),
            readonly_reason="seed blueprint" if blueprint.get("is_seed") else "",
            reason="Blueprint selected by task/surface matching heuristic.",
            edit_target={
                "editor": "blueprint_config",
                "route": "/blueprint-config",
                "entity_id": blueprint.get("id"),
            },
            data=blueprint,
        ))
        edges.append(EffectiveWorkflowEdge(
            source=previous,
            target=blueprint_id,
            edge_type="selects_blueprint",
            label="Blueprint",
            reason="Selected blueprint contributes roles, templates and artifacts.",
        ))
        team_type = str(blueprint.get("base_team_type_name") or "").strip()
        if team_type:
            team_id = f"team_type::{team_type}"
            self._add(nodes, EffectiveWorkflowNode(
                id=team_id,
                node_type="team_type",
                label=team_type,
                declared_value=team_type,
                effective_value=team_type,
                source_kind="hub_api",
                source_pointer=f"/teams/blueprints/{blueprint.get('id')}/base_team_type_name",
                writable=not bool(blueprint.get("is_seed")),
                readonly_reason="seed blueprint" if blueprint.get("is_seed") else "",
                reason="Blueprint declares base team type.",
                edit_target={"editor": "blueprint_config", "route": "/blueprint-config", "entity_id": blueprint.get("id")},
            ))
            edges.append(EffectiveWorkflowEdge(
                source=blueprint_id,
                target=team_id,
                edge_type="uses_team_type",
            ))
        for role in sorted(blueprint.get("roles") or [], key=lambda item: item.get("sort_order") or 0):
            role_key = role.get("id") or role.get("name")
            role_id = f"role::{role_key}"
            self._add(nodes, EffectiveWorkflowNode(
                id=role_id,
                node_type="role",
                label=str(role.get("name") or role_id),
                declared_value=role,
                effective_value=role,
                source_kind="hub_api",
                source_pointer=f"/teams/blueprints/{blueprint.get('id')}/roles/{role_key}",
                writable=not bool(blueprint.get("is_seed")),
                readonly_reason="seed blueprint" if blueprint.get("is_seed") else "",
                reason="Blueprint role is part of selected workflow.",
                edit_target={"editor": "blueprint_config", "route": "/blueprint-config", "entity_id": blueprint.get("id")},
                data=role,
            ))
            edges.append(EffectiveWorkflowEdge(
                source=blueprint_id,
                target=role_id,
                edge_type="contains_role",
                label=str(role.get("sort_order") or ""),
            ))
            template_id = str(role.get("template_id") or "")
            template = template_by_id.get(template_id)
            if template:
                tpl_node_id = f"template::{template_id}"
                self._add(nodes, EffectiveWorkflowNode(
                    id=tpl_node_id,
                    node_type="template",
                    label=str(template.get("name") or template_id),
                    declared_value={
                        "id": template.get("id"),
                        "name": template.get("name"),
                        "description": template.get("description"),
                    },
                    effective_value={
                        "id": template.get("id"),
                        "name": template.get("name"),
                        "description": template.get("description"),
                    },
                    source_kind="hub_api",
                    source_pointer=f"/templates/{template_id}",
                    writable=True,
                    reason="Role uses this template.",
                    edit_target={"editor": "blueprint_config", "route": "/blueprint-config", "entity_id": blueprint.get("id")},
                    data={"template_id": template_id, "name": template.get("name")},
                ))
                edges.append(EffectiveWorkflowEdge(
                    source=role_id,
                    target=tpl_node_id,
                    edge_type="role_uses_template",
                    label=str(template.get("name") or ""),
                ))
        for artifact in sorted(blueprint.get("artifacts") or [], key=lambda item: item.get("sort_order") or 0):
            artifact_key = artifact.get("id") or artifact.get("title")
            artifact_id = f"artifact::{artifact_key}"
            self._add(nodes, EffectiveWorkflowNode(
                id=artifact_id,
                node_type="artifact",
                label=str(artifact.get("title") or artifact_id),
                declared_value=artifact,
                effective_value=artifact,
                source_kind="hub_api",
                source_pointer=f"/teams/blueprints/{blueprint.get('id')}/artifacts/{artifact_key}",
                writable=not bool(blueprint.get("is_seed")),
                readonly_reason="seed blueprint" if blueprint.get("is_seed") else "",
                reason="Blueprint artifact contributes expected output or policy.",
                edit_target={"editor": "blueprint_config", "route": "/blueprint-config", "entity_id": blueprint.get("id")},
                data=artifact,
            ))
            edges.append(EffectiveWorkflowEdge(
                source=blueprint_id,
                target=artifact_id,
                edge_type="derived_from",
                label=str(artifact.get("kind") or ""),
            ))

    def _select_blueprint(self, blueprints: list[dict[str, Any]], *, task_kind: str | None, surface: str, add_warning) -> dict[str, Any] | None:
        if not blueprints:
            add_warning("no_blueprints_available", "No blueprints are available from the Hub API.")
            return None
        needle_parts = [part for part in [task_kind, surface] if part]
        scores: list[tuple[int, dict[str, Any]]] = []
        for blueprint in blueprints:
            haystack = " ".join([
                str(blueprint.get("name") or ""),
                str(blueprint.get("description") or ""),
                " ".join(str(role.get("name") or "") for role in blueprint.get("roles") or []),
                " ".join(str(artifact.get("title") or "") for artifact in blueprint.get("artifacts") or []),
            ]).lower().replace("-", "_")
            score = 0
            for part in needle_parts:
                normalized = str(part or "").lower().replace("-", "_")
                if normalized and normalized in haystack:
                    score += 2
                for token in normalized.split("_"):
                    if len(token) >= 4 and token in haystack:
                        score += 1
            if score:
                scores.append((score, blueprint))
        if not scores:
            add_warning(
                "no_unique_blueprint_match",
                "No blueprint clearly matches the requested surface/task_kind.",
                task_kind=task_kind,
                surface=surface,
            )
            return None
        scores.sort(key=lambda item: item[0], reverse=True)
        if len(scores) > 1 and scores[0][0] == scores[1][0]:
            add_warning(
                "ambiguous_blueprint_match",
                "Multiple blueprints match equally; no blueprint selected automatically.",
                candidates=[item[1].get("name") for item in scores if item[0] == scores[0][0]],
            )
            return None
        return scores[0][1]

    @staticmethod
    def _add(nodes: dict[str, EffectiveWorkflowNode], node: EffectiveWorkflowNode) -> None:
        nodes.setdefault(node.id, node)

    @staticmethod
    def _edge_type_for_node(node_type: str) -> str:
        return {
            "path_rule": "matches_path_rule",
            "instruction_layer": "applies_instruction_layer",
            "agent_profile": "activates_profile",
            "goal_template": "role_uses_template",
            "model_provider": "worker_uses_model",
            "tool_group": "allows_tool",
            "tool": "allows_tool",
            "context_source": "uses_context_source",
        }.get(node_type, "derived_from")

    @staticmethod
    def _edit_target_for_config_node(node: ConfigGraphNode) -> dict[str, Any]:
        editor = "config_graph"
        route = "/config-graph"
        if node.node_type in {"blueprint", "role", "template", "artifact"}:
            editor = "blueprint_config"
            route = "/blueprint-config"
        if node.node_type in {"hub", "worker_instance", "taskflow"}:
            editor = "hub_worker_graph"
            route = "/hub-worker-graph"
        if not node.writable:
            editor = "readonly"
        return {
            "editor": editor,
            "route": route,
            "entity_id": node.id,
            "source_ref": {
                "source_file": node.source_file,
                "source_kind": node.source_kind,
                "source_pointer": node.source_pointer,
            },
        }

    @staticmethod
    def _source_ref_for_graph_node(node: ConfigGraphNode | None) -> dict[str, Any] | None:
        if not node:
            return None
        return {
            "source_file": node.source_file,
            "source_kind": node.source_kind,
            "source_pointer": node.source_pointer,
            "writable": node.writable,
        }

    @staticmethod
    def _selected_nodes(nodes: dict[str, EffectiveWorkflowNode], node_type: str) -> list[dict[str, Any]]:
        return [
            node.to_dict()
            for node in nodes.values()
            if node.node_type == node_type
        ]

    @staticmethod
    def _selected_templates(blueprint: dict[str, Any] | None, template_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        if not blueprint:
            return []
        ids = {
            str(role.get("template_id") or "")
            for role in blueprint.get("roles") or []
            if role.get("template_id")
        }
        return [
            {
                "id": tpl.get("id"),
                "name": tpl.get("name"),
                "description": tpl.get("description"),
            }
            for tid, tpl in template_by_id.items()
            if tid in ids
        ]

    @staticmethod
    def _write_policy_summary(effective, path: str | None) -> dict[str, Any]:
        blocked = set(effective.effective_ai_modes_blocked or [])
        return {
            "path": path,
            "blocked_ai_modes": sorted(blocked),
            "code_generation_blocked": "code_gen" in blocked or "full_llm" in blocked,
            "reason": "Derived from matching path_ai_modes rules.",
        }

    @staticmethod
    def _summary(request: dict[str, Any], selected: dict[str, Any], status: str) -> str:
        blueprint = selected.get("blueprint") or {}
        profile = selected.get("agent_profile") or {}
        worker_count = len(selected.get("worker_routing") or [])
        return (
            f"{request.get('surface') or 'unknown surface'} / "
            f"{request.get('task_kind') or 'any task'} resolves with "
            f"profile={profile.get('profile_id') or 'none'}, "
            f"blueprint={blueprint.get('name') or 'none'}, "
            f"workers={worker_count}, status={status}."
        )

    @staticmethod
    def _main_chain(nodes: dict[str, EffectiveWorkflowNode], edges: list[EffectiveWorkflowEdge], start_id: str) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        current = start_id
        visited: set[str] = set()
        while current in nodes and current not in visited:
            visited.add(current)
            chain.append(nodes[current].to_dict())
            next_edge = next((edge for edge in edges if edge.source == current), None)
            if not next_edge:
                break
            current = next_edge.target
        return chain

    @staticmethod
    def _alternatives(
        blueprints: list[dict[str, Any]],
        selected_blueprint: dict[str, Any] | None,
        user_config: dict[str, Any],
        *,
        include: bool,
    ) -> dict[str, Any]:
        if not include:
            return {}
        selected_id = selected_blueprint.get("id") if selected_blueprint else None
        hw_graph = HubWorkerGraphService().build(user_config=user_config)
        return {
            "blueprints": [
                {"id": item.get("id"), "name": item.get("name")}
                for item in blueprints
                if item.get("id") != selected_id
            ],
            "workers": [
                {"id": node_id, "label": node.get("label"), "runtime_active": node.get("runtime_active")}
                for node_id, node in (hw_graph.get("nodes") or {}).items()
                if node.get("node_type") == "worker_instance"
            ],
        }

    @staticmethod
    def _edit_links(nodes: dict[str, EffectiveWorkflowNode]) -> list[dict[str, Any]]:
        links = []
        seen: set[tuple[str, str]] = set()
        for node in nodes.values():
            target = node.edit_target or {}
            route = str(target.get("route") or "")
            entity_id = str(target.get("entity_id") or "")
            if not route or (route, entity_id) in seen:
                continue
            seen.add((route, entity_id))
            links.append({
                "node_id": node.id,
                "label": node.label,
                "editor": target.get("editor"),
                "route": route,
                "entity_id": entity_id,
                "writable": node.writable,
            })
        return links

    @staticmethod
    def _source_index(nodes: dict[str, EffectiveWorkflowNode]) -> list[dict[str, Any]]:
        indexed = []
        seen: set[tuple[str, str, str]] = set()
        for node in nodes.values():
            key = (
                str(node.source_file or ""),
                str(node.source_kind or ""),
                str(node.source_pointer or ""),
            )
            if key in seen or not any(key):
                continue
            seen.add(key)
            indexed.append({
                "node_id": node.id,
                "label": node.label,
                "source_file": node.source_file,
                "source_kind": node.source_kind,
                "source_pointer": node.source_pointer,
                "writable": node.writable,
                "readonly_reason": node.readonly_reason,
            })
        return indexed
