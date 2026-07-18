"""Pure semantic graph-location analysis for the Visual Process editor."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from agent.visual_process.models import VisualProcessEdge, VisualProcessGraph
from ananta_contracts.visual_process_assistant import AssistantLocation

LOCATION_CONTRACT_VERSION = "ananta.visual_process.workflow_location.v1"


@dataclass(frozen=True)
class WorkflowLocationResult:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class VisualProcessLocationService:
    """Describe topology only from stable node/edge IDs and conditions."""

    def analyze(
        self,
        *,
        graph: VisualProcessGraph | dict[str, Any],
        location: AssistantLocation | dict[str, Any],
        draft_hash: str | None = None,
    ) -> WorkflowLocationResult:
        definition = graph if isinstance(graph, VisualProcessGraph) else VisualProcessGraph.model_validate(graph)
        target = location if isinstance(location, AssistantLocation) else AssistantLocation.model_validate(location)
        if target.graph_id != definition.id:
            raise ValueError("workflow_location_graph_mismatch")

        step_ids = sorted(step.id for step in definition.steps)
        known = set(step_ids)
        valid_edges = sorted(
            (edge for edge in definition.edges if edge.source in known and edge.target in known),
            key=lambda item: item.id,
        )
        forward_edges = [edge for edge in valid_edges if not edge.is_back_edge()]
        incoming = {step_id: [] for step_id in step_ids}
        outgoing = {step_id: [] for step_id in step_ids}
        for edge in valid_edges:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)
        for values in (*incoming.values(), *outgoing.values()):
            values.sort(key=lambda item: (item.id, item.source, item.target))

        forward_incoming = {step_id: [] for step_id in step_ids}
        forward_outgoing = {step_id: [] for step_id in step_ids}
        for edge in forward_edges:
            forward_outgoing[edge.source].append(edge)
            forward_incoming[edge.target].append(edge)
        starts = sorted(step_id for step_id in step_ids if not forward_incoming[step_id])
        distances = self._distances(starts, forward_outgoing)
        needs_components = any(edge.is_back_edge() for edge in valid_edges) or self._has_forward_cycle(
            step_ids,
            forward_incoming,
            forward_outgoing,
        )
        components = (
            self._strongly_connected_components(step_ids, outgoing)
            if needs_components
            else [(step_id,) for step_id in step_ids]
        )
        component_by_step = {step_id: component for component in components for step_id in component}
        self_loops = {edge.source for edge in valid_edges if edge.source == edge.target}
        loop_members = {
            step_id for step_id, component in component_by_step.items() if len(component) > 1 or step_id in self_loops
        }
        loop_members.update(
            endpoint for edge in valid_edges if edge.is_back_edge() for endpoint in (edge.source, edge.target)
        )
        graph_facts = {
            "step_count": len(step_ids),
            "edge_count": len(definition.edges),
            "valid_edge_count": len(valid_edges),
            "dangling_edge_count": len(definition.edges) - len(valid_edges),
            "start_step_ids": starts,
            "multiple_starts": len(starts) > 1,
            "reachable_step_ids": sorted(distances),
            "unreachable_step_ids": sorted(known - set(distances)),
            "unconnected_step_ids": sorted(
                step_id for step_id in step_ids if not incoming[step_id] and not outgoing[step_id]
            ),
            "loop_step_ids": sorted(loop_members),
            "dead_end_step_ids": sorted(step_id for step_id in step_ids if not forward_outgoing[step_id]),
        }
        focused = self._focused_facts(
            definition=definition,
            target=target,
            known=known,
            starts=starts,
            distances=distances,
            incoming=incoming,
            outgoing=outgoing,
            forward_incoming=forward_incoming,
            forward_outgoing=forward_outgoing,
            component_by_step=component_by_step,
            loop_members=loop_members,
            self_loops=self_loops,
        )
        definition_hash = definition.base_graph_hash or definition.definition_hash()
        resolved_draft_hash = str(draft_hash or "") or (
            definition_hash if definition.base_graph_hash else definition.definition_hash()
        )
        payload = {
            "contract_version": LOCATION_CONTRACT_VERSION,
            "graph_id": definition.id,
            "definition_revision": definition.definition_revision,
            "definition_hash": definition_hash,
            "draft_hash": resolved_draft_hash,
            "location": target.model_dump(mode="json"),
            "graph_facts": graph_facts,
            "focused_facts": focused,
            "localized_message_key": self._message_key(target, focused),
        }
        return WorkflowLocationResult(payload)

    @staticmethod
    def _has_forward_cycle(
        step_ids: list[str],
        incoming: dict[str, list[VisualProcessEdge]],
        outgoing: dict[str, list[VisualProcessEdge]],
    ) -> bool:
        indegree = {step_id: len(incoming[step_id]) for step_id in step_ids}
        queue = deque(step_id for step_id in step_ids if indegree[step_id] == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for edge in outgoing[node]:
                indegree[edge.target] -= 1
                if indegree[edge.target] == 0:
                    queue.append(edge.target)
        return visited != len(step_ids)

    @staticmethod
    def _distances(
        starts: list[str],
        outgoing: dict[str, list[VisualProcessEdge]],
    ) -> dict[str, int]:
        distances = {step_id: 0 for step_id in starts}
        queue = deque(starts)
        while queue:
            source = queue.popleft()
            next_distance = distances[source] + 1
            for edge in outgoing[source]:
                if edge.target not in distances or next_distance < distances[edge.target]:
                    distances[edge.target] = next_distance
                    queue.append(edge.target)
        return distances

    @staticmethod
    def _strongly_connected_components(
        step_ids: list[str],
        outgoing: dict[str, list[VisualProcessEdge]],
    ) -> list[tuple[str, ...]]:
        adjacency = {node: sorted({edge.target for edge in outgoing[node]}) for node in step_ids}
        reverse = {node: [] for node in step_ids}
        for source, targets in adjacency.items():
            for target in targets:
                reverse[target].append(source)
        for values in reverse.values():
            values.sort()

        visited: set[str] = set()
        finish_order: list[str] = []
        for root in step_ids:
            if root in visited:
                continue
            stack: list[tuple[str, bool]] = [(root, False)]
            while stack:
                node, expanded = stack.pop()
                if expanded:
                    finish_order.append(node)
                    continue
                if node in visited:
                    continue
                visited.add(node)
                stack.append((node, True))
                for target in reversed(adjacency[node]):
                    if target not in visited:
                        stack.append((target, False))

        assigned: set[str] = set()
        result: list[tuple[str, ...]] = []
        for root in reversed(finish_order):
            if root in assigned:
                continue
            component: list[str] = []
            stack = [root]
            assigned.add(root)
            while stack:
                node = stack.pop()
                component.append(node)
                for source in reversed(reverse[node]):
                    if source not in assigned:
                        assigned.add(source)
                        stack.append(source)
            result.append(tuple(sorted(component)))
        return sorted(result)

    @staticmethod
    def _edge_fact(edge: VisualProcessEdge) -> dict[str, Any]:
        return {
            "edge_id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "condition_kind": edge.condition.kind,
            "is_back_edge": edge.is_back_edge(),
        }

    def _focused_facts(
        self,
        *,
        definition: VisualProcessGraph,
        target: AssistantLocation,
        known: set[str],
        starts: list[str],
        distances: dict[str, int],
        incoming: dict[str, list[VisualProcessEdge]],
        outgoing: dict[str, list[VisualProcessEdge]],
        forward_incoming: dict[str, list[VisualProcessEdge]],
        forward_outgoing: dict[str, list[VisualProcessEdge]],
        component_by_step: dict[str, tuple[str, ...]],
        loop_members: set[str],
        self_loops: set[str],
    ) -> dict[str, Any]:
        if target.target_kind == "edge":
            edge = next((item for item in definition.edges if item.id == target.entity_id), None)
            return {
                "entity_exists": edge is not None,
                "target_kind": "edge",
                "edge": self._edge_fact(edge) if edge is not None else None,
                "endpoint_integrity": bool(edge and edge.source in known and edge.target in known),
                "error_path": bool(edge and edge.condition.kind == "on_failure"),
                "loop_path": bool(edge and edge.is_back_edge()),
            }
        if target.target_kind in {"canvas", "palette_item"} or target.entity_id is None:
            return {
                "entity_exists": True,
                "target_kind": target.target_kind,
                "start_count": len(starts),
                "has_unreachable_steps": len(distances) != len(known),
            }
        step = definition.step_by_id(target.entity_id)
        if step is None:
            return {"entity_exists": False, "target_kind": target.target_kind}
        step_id = step.id
        component = component_by_step.get(step_id, (step_id,))
        predecessor_ids = sorted({edge.source for edge in forward_incoming[step_id]})
        successor_ids = sorted({edge.target for edge in forward_outgoing[step_id]})
        return {
            "entity_exists": True,
            "target_kind": target.target_kind,
            "step_id": step_id,
            "step_kind": step.kind,
            "is_start": step_id in starts,
            "start_distance": distances.get(step_id),
            "near_start": distances.get(step_id, 99) <= 1,
            "reachable": step_id in distances,
            "disconnected": not incoming[step_id] and not outgoing[step_id],
            "predecessor_step_ids": predecessor_ids,
            "successor_step_ids": successor_ids,
            "incoming_edges": [self._edge_fact(edge) for edge in incoming[step_id]],
            "outgoing_edges": [self._edge_fact(edge) for edge in outgoing[step_id]],
            "branch": len(successor_ids) > 1,
            "merge": len(predecessor_ids) > 1,
            "gate": bool(step.gate),
            "dead_end": not forward_outgoing[step_id],
            "loop": {
                "member": step_id in loop_members,
                "component_step_ids": list(component),
                "self_loop": step_id in self_loops,
                "incoming_back_edge_ids": [edge.id for edge in incoming[step_id] if edge.is_back_edge()],
                "outgoing_back_edge_ids": [edge.id for edge in outgoing[step_id] if edge.is_back_edge()],
            },
            "error_path": {
                "incoming_edge_ids": [edge.id for edge in incoming[step_id] if edge.condition.kind == "on_failure"],
                "outgoing_edge_ids": [edge.id for edge in outgoing[step_id] if edge.condition.kind == "on_failure"],
            },
        }

    @staticmethod
    def _message_key(target: AssistantLocation, facts: dict[str, Any]) -> str:
        if not facts.get("entity_exists", True):
            return "visual_process.location.entity_missing"
        if target.target_kind == "edge":
            if facts.get("error_path"):
                return "visual_process.location.failure_edge"
            if facts.get("loop_path"):
                return "visual_process.location.loop_edge"
            return "visual_process.location.edge"
        if facts.get("disconnected"):
            return "visual_process.location.disconnected_step"
        if facts.get("gate"):
            return "visual_process.location.approval_gate"
        if facts.get("branch"):
            return "visual_process.location.branch_step"
        if facts.get("loop", {}).get("member"):
            return "visual_process.location.loop_step"
        if facts.get("is_start"):
            return "visual_process.location.start_step"
        if facts.get("dead_end"):
            return "visual_process.location.end_step"
        return "visual_process.location.step"


visual_process_location_service = VisualProcessLocationService()


__all__ = [
    "LOCATION_CONTRACT_VERSION",
    "VisualProcessLocationService",
    "WorkflowLocationResult",
    "visual_process_location_service",
]
