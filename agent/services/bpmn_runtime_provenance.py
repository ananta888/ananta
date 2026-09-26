"""Closed UI lineage derived from the immutable Hub plan, never Worker data."""

from collections.abc import Mapping


def bpmn_step_provenance(plan: Mapping) -> dict[str, dict]:
    """Runtime activation IDs are plan node IDs, not Registry evidence IDs."""
    if not (plan.get("metadata") or {}).get("bpmn_definition_hash"):
        return {}
    output = {}
    for node in plan.get("nodes", ()):
        node_id = node["node_id"]
        origin = (node.get("metadata") or {}).get("bpmn_activation_origin") or {}
        value = {"element_id": origin.get("source_id") or node_id, "activation_id": node_id}
        for scope in origin.get("scope", ()):
            if type(scope.get("iteration")) is int and scope["iteration"] >= 0:
                value["iteration"] = scope["iteration"]
        output[node_id] = value
    return output
