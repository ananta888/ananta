"""Revision-bound hierarchy/graph read-model projection for organizations."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from agent.models.organization_models import OrganizationLimitProfile
from agent.ports.organization_definitions import OrganizationTopologyReadPort
from agent.services.organization_limit_projection import organization_limit_profile_projection

RUNTIME_EDGE_KINDS = {
    "runtime_task_dependency",
    "handoff_instance",
    "gate_state",
    "escalation_event",
}

ALLOWED_PARENT_KINDS: dict[str, set[str]] = {
    "coordination_unit": {"organization", "coordination_unit"},
    "value_stream": {"organization", "coordination_unit", "value_stream"},
    "team": {"organization", "coordination_unit", "value_stream"},
    "role_slot": {"team"},
    "assignment": {"role_slot"},
}


class OrganizationProjectionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class OrganizationProjectionService:
    def __init__(self, *, topology_reader: OrganizationTopologyReadPort) -> None:
        self._topology_reader = topology_reader

    def project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        limits: OrganizationLimitProfile,
        include_runtime_overlay: bool = False,
        cursor: str | None = None,
        page_size: int | None = None,
        max_depth: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_page_size = page_size or limits.topology_default_page_size
        effective_depth = max_depth or limits.topology_max_depth
        if isinstance(effective_page_size, bool) or not 1 <= effective_page_size <= limits.topology_max_page_size:
            raise OrganizationProjectionError("ORGANIZATION_TOPOLOGY_PAGE_SIZE_INVALID")
        if isinstance(effective_depth, bool) or not 1 <= effective_depth <= limits.topology_max_depth:
            raise OrganizationProjectionError("ORGANIZATION_TOPOLOGY_DEPTH_INVALID")

        requested_filters = dict(filters or {})
        requested_node_kinds = {
            str(value).strip() for value in requested_filters.get("kinds") or [] if str(value).strip()
        }
        snapshot = self._topology_reader.load_topology_snapshot(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            include_runtime_overlay=include_runtime_overlay,
            cursor=cursor,
            limit=effective_page_size,
            max_depth=effective_depth,
            filters=requested_filters,
        )
        if snapshot.get("organization_id") != organization_id:
            raise OrganizationProjectionError("ORGANIZATION_TOPOLOGY_SCOPE_MISMATCH")
        if not snapshot.get("definition_revision") or not snapshot.get("snapshot_hash"):
            raise OrganizationProjectionError("ORGANIZATION_TOPOLOGY_REVISION_MISSING")

        nodes: list[dict[str, Any]] = []
        node_kind: dict[str, str] = {organization_id: "organization"}
        unit_id_by_key: dict[str, str] = {}
        for unit in snapshot.get("units", []):
            unit_id = str(unit["id"])
            unit_id_by_key[str(unit["unit_key"])] = unit_id
            node_kind[unit_id] = str(unit["unit_kind"])

        # The Organization node is the stable projection boundary and is
        # intentionally retained even when an exact kinds filter omits it.
        # Every descendant kind, however, is filtered by the read repository.
        organization_metadata: dict[str, Any] = {"selection_target": {"organization_id": organization_id}}
        if requested_node_kinds and "organization" not in requested_node_kinds:
            organization_metadata["projection_boundary"] = True
        raw_nodes: list[dict[str, Any]] = [
            {
                "id": organization_id,
                "stable_key": str(snapshot.get("organization_key") or organization_id),
                "kind": "organization",
                "parent_id": None,
                "label": snapshot.get("name") or organization_id,
                "metadata": organization_metadata,
            }
        ]
        for unit in snapshot.get("units", []):
            unit_id = str(unit["id"])
            parent_id = (
                str(unit.get("parent_unit_id"))
                if unit.get("parent_unit_id")
                else unit_id_by_key.get(str(unit.get("parent_unit_key") or ""), organization_id)
            )
            raw_nodes.append(
                {
                    "id": unit_id,
                    "stable_key": str(unit["unit_key"]),
                    "kind": unit["unit_kind"],
                    "parent_id": parent_id,
                    "label": unit.get("name") or unit["unit_key"],
                    "depth": int(unit.get("depth", 1)),
                    "child_count": unit.get("child_count"),
                    "team_id": unit.get("team_id"),
                    "unit_id": unit_id,
                    "has_more_children": bool(unit.get("has_more_children", False)),
                    "metadata": {
                        "unit_key": unit["unit_key"],
                        "selection_target": {
                            "organization_id": organization_id,
                            "unit_id": unit_id,
                            "team_id": unit.get("team_id"),
                        },
                    },
                }
            )
        for slot in snapshot.get("role_slots", []):
            slot_id = str(slot["id"])
            parent_id = str(slot["unit_id"])
            if parent_id in node_kind and node_kind[parent_id] != "team":
                raise OrganizationProjectionError("ORGANIZATION_ROLE_SLOT_PARENT_INVALID")
            node_kind[slot_id] = "role_slot"
            raw_nodes.append(
                {
                    "id": slot_id,
                    "stable_key": str(slot.get("stable_key") or f"{parent_id}:slot:{slot['slot_key']}"),
                    "kind": "role_slot",
                    "parent_id": parent_id,
                    "label": slot.get("name") or slot["slot_key"],
                    "unit_id": parent_id,
                    "role_slot_id": slot_id,
                    "metadata": {
                        "role_template_ref": slot.get("role_template_ref"),
                        "default_count": slot.get("default_count"),
                        "selection_target": {
                            "organization_id": organization_id,
                            "unit_id": parent_id,
                            "role_slot_id": slot_id,
                        },
                    },
                }
            )
        for assignment in snapshot.get("assignments", []):
            assignment_id = str(assignment["id"])
            parent_id = str(assignment["role_slot_id"])
            if parent_id in node_kind and node_kind[parent_id] != "role_slot":
                raise OrganizationProjectionError("ORGANIZATION_ASSIGNMENT_PARENT_INVALID")
            node_kind[assignment_id] = "assignment"
            raw_nodes.append(
                {
                    "id": assignment_id,
                    "stable_key": str(assignment.get("stable_key") or assignment_id),
                    "kind": "assignment",
                    "parent_id": parent_id,
                    "label": assignment.get("agent_name") or assignment.get("agent_url") or assignment_id,
                    "role_slot_id": parent_id,
                    "assignment_id": assignment_id,
                    "metadata": {
                        "selection_target": {
                            "organization_id": organization_id,
                            "role_slot_id": parent_id,
                            "assignment_id": assignment_id,
                        },
                    },
                }
            )

        child_counts = Counter(str(node["parent_id"]) for node in raw_nodes if node.get("parent_id"))
        raw_by_id = {str(node["id"]): node for node in raw_nodes}
        for node in raw_nodes:
            parent = raw_by_id.get(str(node.get("parent_id") or ""))
            if parent is None:
                continue
            if str(parent["kind"]) not in ALLOWED_PARENT_KINDS.get(str(node["kind"]), set()):
                raise OrganizationProjectionError("ORGANIZATION_TOPOLOGY_PARENT_KIND_INVALID")
        depth_cache: dict[str, int] = {organization_id: 0}
        omitted_parent_node_ids: list[str] = []
        for node in raw_nodes:
            node_id = str(node["id"])
            normalized = dict(node)
            normalized["depth"] = _node_depth(node_id, raw_by_id, depth_cache)
            parent_id = str(node.get("parent_id") or "")
            if parent_id and parent_id not in raw_by_id:
                # Cursor pages and exact node-kind filters may exclude a
                # parent.  Keep its identity as bounded metadata for UI
                # continuation, but never emit a dangling parent reference or
                # hierarchy edge.
                metadata = dict(normalized.get("metadata") or {})
                metadata["hierarchy_boundary"] = {
                    "omitted_parent_id": parent_id,
                    "reason": "page_or_filter_boundary",
                }
                normalized["metadata"] = metadata
                normalized["parent_id"] = None
                omitted_parent_node_ids.append(node_id)
            declared_child_count = node.get("child_count")
            normalized["child_count"] = int(
                declared_child_count if declared_child_count is not None else child_counts.get(node_id, 0)
            )
            nodes.append(normalized)

        edges = [
            {
                "id": f"contains:{node['parent_id']}:{node['id']}",
                "namespace": "hierarchy",
                "kind": "contains",
                "source_id": node["parent_id"],
                "target_id": node["id"],
                "read_only": True,
                "metadata": {"derived_from": "parent_id"},
            }
            for node in nodes
            if node["parent_id"] is not None
        ]
        for relation in snapshot.get("relations", []):
            source = unit_id_by_key.get(str(relation.get("source_unit_key") or ""), relation.get("source_unit_id"))
            target = unit_id_by_key.get(str(relation.get("target_unit_key") or ""), relation.get("target_unit_id"))
            if source not in node_kind or target not in node_kind:
                raise OrganizationProjectionError("ORGANIZATION_RELATION_DANGLING")
            edges.append(
                {
                    "id": relation["id"],
                    "namespace": "organization",
                    "kind": relation["kind"],
                    "source_id": source,
                    "target_id": target,
                    "read_only": False,
                    "metadata": {
                        "definition_relation_ref": relation.get("definition_relation_ref"),
                    },
                }
            )

        runtime_edges: list[dict[str, Any]] = []
        if include_runtime_overlay:
            raw_runtime_edges = list(snapshot.get("runtime_edges", []))
            if len(raw_runtime_edges) > limits.runtime_overlay_max_events:
                raise OrganizationProjectionError("ORGANIZATION_RUNTIME_OVERLAY_LIMIT_EXCEEDED")
            for edge in raw_runtime_edges:
                if edge.get("kind") not in RUNTIME_EDGE_KINDS:
                    raise OrganizationProjectionError("ORGANIZATION_RUNTIME_EDGE_KIND_INVALID")
                source_id = str(edge.get("source_id") or edge.get("source_node_id") or "")
                target_id = str(edge.get("target_id") or edge.get("target_node_id") or "")
                if source_id not in node_kind or target_id not in node_kind:
                    raise OrganizationProjectionError("ORGANIZATION_RUNTIME_EDGE_DANGLING")
                runtime_edges.append(
                    {
                        "id": edge["id"],
                        "namespace": "runtime",
                        "kind": edge["kind"],
                        "source_id": source_id,
                        "target_id": target_id,
                        "read_only": True,
                        "metadata": {
                            "runtime_ref": edge.get("runtime_ref"),
                            "provenance_count": int(edge.get("provenance_count", 1)),
                            "drill_down_refs": list(edge.get("drill_down_refs") or []),
                        },
                    }
                )

        requested_namespaces = {str(value) for value in requested_filters.get("edge_namespaces") or []}
        if requested_namespaces:
            edges = [edge for edge in edges if edge["namespace"] in requested_namespaces]
            runtime_edges = [edge for edge in runtime_edges if edge["namespace"] in requested_namespaces]

        if (
            len(nodes) > limits.canvas_render_node_limit
            or len(edges) + len(runtime_edges) > limits.canvas_render_edge_limit
        ):
            raise OrganizationProjectionError("ORGANIZATION_CANVAS_LIMIT_EXCEEDED")
        runtime_overlay = None
        if include_runtime_overlay:
            overlay_revision = str(snapshot.get("runtime_definition_revision") or snapshot["definition_revision"])
            runtime_overlay = {
                "definition_revision": overlay_revision,
                "snapshot_hash": str(snapshot.get("runtime_snapshot_hash") or snapshot["snapshot_hash"]),
                "generated_at": str(snapshot.get("runtime_generated_at") or datetime.now(timezone.utc).isoformat()),
                "stale": overlay_revision != str(snapshot["definition_revision"]),
                "nodes": list(snapshot.get("runtime_nodes") or []),
                "edges": runtime_edges,
            }

        diagnostics = [_normalize_diagnostic(value) for value in snapshot.get("diagnostics") or []]
        if omitted_parent_node_ids:
            diagnostics.append(
                {
                    "severity": "info",
                    "reason_code": "ORGANIZATION_HIERARCHY_PARENT_OMITTED",
                    "message": (
                        "One or more hierarchy parents are outside the current "
                        "page or node-kind filter; their edges were omitted."
                    ),
                    "node_ids": sorted(set(omitted_parent_node_ids)),
                }
            )

        return {
            "organization_id": organization_id,
            "definition_revision": snapshot["definition_revision"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "nodes": nodes,
            "edges": edges,
            "runtime_overlay": runtime_overlay,
            "diagnostics": diagnostics,
            "limits": organization_limit_profile_projection(limits),
            "next_cursor": snapshot.get("next_cursor"),
            "truncated": bool(snapshot.get("next_cursor")),
        }


def _node_depth(
    node_id: str,
    nodes: dict[str, dict[str, Any]],
    cache: dict[str, int],
    visiting: set[str] | None = None,
) -> int:
    if node_id in cache:
        return cache[node_id]
    path = visiting or set()
    if node_id in path:
        raise OrganizationProjectionError("ORGANIZATION_TOPOLOGY_HIERARCHY_CYCLE")
    path.add(node_id)
    current = nodes[node_id]
    parent_id = str(current.get("parent_id") or "")
    if not parent_id or parent_id not in nodes:
        cache[node_id] = int(current.get("depth", 1))
        path.remove(node_id)
        return cache[node_id]
    cache[node_id] = _node_depth(parent_id, nodes, cache, path) + 1
    path.remove(node_id)
    return cache[node_id]


def _normalize_diagnostic(value: Any) -> dict[str, Any]:
    raw = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
    return {
        "severity": raw.get("severity", "warning"),
        "reason_code": raw.get("reason_code", "ORGANIZATION_TOPOLOGY_DIAGNOSTIC"),
        "message": raw.get("message") or raw.get("human_message") or "Organization topology diagnostic.",
        **({"node_ids": list(raw["node_ids"])} if raw.get("node_ids") else {}),
        **({"policy_id": raw["policy_id"]} if raw.get("policy_id") else {}),
    }


__all__ = [
    "ALLOWED_PARENT_KINDS",
    "OrganizationProjectionError",
    "OrganizationProjectionService",
    "RUNTIME_EDGE_KINDS",
]
