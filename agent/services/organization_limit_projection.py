"""Shared API projection for authoritative organization limit profiles."""

from __future__ import annotations

from typing import Any

from agent.models.organization_models import OrganizationLimitProfile


def organization_limit_profile_projection(limits: OrganizationLimitProfile) -> dict[str, Any]:
    return {
        "revision": str(limits.revision),
        "policy_hash": limits.content_hash(),
        "max_teams": limits.max_team_instances_per_organization,
        "max_units": limits.max_units_per_organization,
        "max_role_slots": limits.max_role_slots_per_organization,
        "max_assignments": limits.max_assignments_per_organization,
        "max_relations": limits.max_relations_per_organization,
        "max_patch_operations": limits.max_patch_operations,
        "max_page_size": limits.topology_max_page_size,
        "max_depth": limits.topology_max_depth,
        "max_render_nodes": limits.canvas_render_node_limit,
        "max_render_edges": limits.canvas_render_edge_limit,
    }
