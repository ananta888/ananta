from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .artifact_grants import is_grant_active, validate_source_artifact_grant_payload
from .artifact_usage import validate_source_artifact_usage_payload
from .goal_artifact_repository import GoalArtifactRepository
from .output_artifacts import validate_goal_output_artifact_payload


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class GoalArtifactServiceError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        suffix = f":{detail}" if detail else ""
        super().__init__(f"{reason_code}{suffix}")
        self.reason_code = reason_code
        self.detail = detail


class GoalArtifactService:
    def __init__(self, *, repository: GoalArtifactRepository | None = None) -> None:
        self._repository = repository or GoalArtifactRepository()

    def get_goal_graph(self, goal_id: str) -> dict[str, Any]:
        return self._repository.get_graph(goal_id)

    def create_grant(self, *, goal_id: str, grant: dict[str, Any]) -> dict[str, Any]:
        graph = self.get_goal_graph(goal_id)
        payload = dict(grant)
        payload.setdefault("schema", "source_artifact_grant.v1")
        payload["goal_id"] = goal_id
        errors = validate_source_artifact_grant_payload(payload)
        if errors:
            raise GoalArtifactServiceError("invalid_source_grant", "; ".join(errors))
        if any(item.get("grant_id") == payload.get("grant_id") for item in graph.get("source_grants", [])):
            raise GoalArtifactServiceError("grant_conflict")
        graph["source_grants"].append(payload)
        graph["updated_at"] = _now_iso()
        self._repository.save_graph(graph)
        return payload

    def revoke_grant(self, *, goal_id: str, grant_id: str, revoked_at: str | None = None, revoke_reason: str = "") -> dict[str, Any]:
        graph = self.get_goal_graph(goal_id)
        for item in graph.get("source_grants", []):
            if str(item.get("grant_id") or "") != str(grant_id):
                continue
            item["revoked_at"] = revoked_at or _now_iso()
            if revoke_reason:
                item["revoke_reason"] = revoke_reason
            graph["updated_at"] = _now_iso()
            self._repository.save_graph(graph)
            return item
        raise GoalArtifactServiceError("grant_not_found")

    def record_usage(self, *, goal_id: str, usage: dict[str, Any]) -> dict[str, Any]:
        graph = self.get_goal_graph(goal_id)
        payload = dict(usage)
        payload.setdefault("schema", "source_artifact_usage.v1")
        payload["goal_id"] = goal_id
        errors = validate_source_artifact_usage_payload(payload)
        if errors:
            raise GoalArtifactServiceError("invalid_source_usage", "; ".join(errors))
        grant = self._find_grant(graph, str(payload.get("grant_id") or ""))
        if grant is None:
            raise GoalArtifactServiceError("missing_grant")
        active, reason = is_grant_active(grant)
        if not active:
            raise GoalArtifactServiceError(reason or "invalid_grant_state")
        graph["source_usages"].append(payload)
        self._upsert_edge(
            graph,
            edge_id=f"{grant['grant_id']}->{payload['usage_id']}",
            from_ref=f"grant:{grant['grant_id']}",
            to_ref=f"usage:{payload['usage_id']}",
            edge_kind="grant_to_usage",
        )
        graph["updated_at"] = _now_iso()
        self._repository.save_graph(graph)
        return payload

    def record_output_artifact(self, *, goal_id: str, output_artifact: dict[str, Any]) -> dict[str, Any]:
        graph = self.get_goal_graph(goal_id)
        payload = dict(output_artifact)
        payload.setdefault("schema", "goal_output_artifact.v1")
        payload["goal_id"] = goal_id
        payload.setdefault("input_usage_refs", [])
        errors = validate_goal_output_artifact_payload(payload)
        if errors:
            raise GoalArtifactServiceError("invalid_output_artifact", "; ".join(errors))
        for usage_id in payload.get("input_usage_refs", []):
            if not self._has_usage(graph, usage_id):
                raise GoalArtifactServiceError("usage_not_found", str(usage_id))
        graph["output_artifacts"].append(payload)
        for usage_id in payload.get("input_usage_refs", []):
            self._upsert_edge(
                graph,
                edge_id=f"{usage_id}->{payload['output_artifact_id']}",
                from_ref=f"usage:{usage_id}",
                to_ref=f"output:{payload['output_artifact_id']}",
                edge_kind="usage_to_output",
            )
        graph["updated_at"] = _now_iso()
        self._repository.save_graph(graph)
        return payload

    @staticmethod
    def _find_grant(graph: dict[str, Any], grant_id: str) -> dict[str, Any] | None:
        for item in graph.get("source_grants", []):
            if str(item.get("grant_id") or "") == grant_id:
                return item
        return None

    @staticmethod
    def _has_usage(graph: dict[str, Any], usage_id: str) -> bool:
        return any(str(item.get("usage_id") or "") == str(usage_id) for item in graph.get("source_usages", []))

    @staticmethod
    def _upsert_edge(graph: dict[str, Any], *, edge_id: str, from_ref: str, to_ref: str, edge_kind: str) -> None:
        for edge in graph.get("edges", []):
            if str(edge.get("edge_id") or "") == edge_id:
                return
        graph["edges"].append(
            {
                "edge_id": edge_id,
                "from_ref": from_ref,
                "to_ref": to_ref,
                "edge_kind": edge_kind,
            }
        )
