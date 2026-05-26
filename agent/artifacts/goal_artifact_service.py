from __future__ import annotations

import hashlib
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

    def validate_and_record_context_usages(
        self,
        *,
        goal_id: str,
        artifact_refs: list[str],
        task_id: str | None,
        worker_id: str | None,
        context_hash: str | None,
    ) -> dict[str, list[str]]:
        graph = self.get_goal_graph(goal_id)
        source_usage_refs: list[str] = []
        artifact_grant_refs: list[str] = []
        denied_context_refs: list[str] = []
        hash_seed = str(context_hash or "")

        for artifact_ref in [str(item).strip() for item in list(artifact_refs or []) if str(item).strip()]:
            grant = self._find_active_grant_for_artifact(graph, artifact_ref)
            if grant is None:
                denied_context_refs.append(artifact_ref)
                continue
            usage_id = self._build_usage_id(
                goal_id=goal_id,
                grant_id=str(grant.get("grant_id") or ""),
                artifact_ref=artifact_ref,
                context_hash=hash_seed,
            )
            usage_payload = {
                "schema": "source_artifact_usage.v1",
                "usage_id": usage_id,
                "grant_id": str(grant.get("grant_id") or ""),
                "goal_id": goal_id,
                "task_id": str(task_id or "") or None,
                "worker_id": str(worker_id or "") or None,
                "artifact_ref": artifact_ref,
                "usage_kind": "embedded",
                "used_at": _now_iso(),
                "context_hash": hash_seed or "context-hash-missing",
                "policy_decision_ref": str(grant.get("policy_decision_ref") or ""),
            }
            existing = self._find_usage(graph, usage_id)
            if existing is None:
                self.record_usage(goal_id=goal_id, usage=usage_payload)
            source_usage_refs.append(usage_id)
            artifact_grant_refs.append(str(grant.get("grant_id") or ""))
        return {
            "artifact_grant_refs": sorted(set(artifact_grant_refs)),
            "source_usage_refs": sorted(set(source_usage_refs)),
            "denied_context_refs": sorted(set(denied_context_refs)),
        }

    def register_output_artifacts_from_refs(
        self,
        *,
        goal_id: str,
        task_id: str,
        worker_id: str | None,
        artifact_refs: list[dict[str, Any]],
        input_usage_refs: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        from worker.core.artifact_types import map_reference_kind_to_output_artifact_type

        created: list[dict[str, Any]] = []
        for index, ref in enumerate(list(artifact_refs or []), start=1):
            if not isinstance(ref, dict):
                continue
            artifact_type = map_reference_kind_to_output_artifact_type(str(ref.get("kind") or ""))
            if artifact_type is None:
                continue
            artifact_ref = str(
                ref.get("artifact_id")
                or ref.get("trace_bundle_ref")
                or ref.get("workspace_relative_path")
                or f"{task_id}:{index}"
            ).strip()
            output_id = self._build_output_id(task_id=task_id, artifact_ref=artifact_ref, index=index)
            content_hash = self._build_content_hash(ref)
            output_payload = {
                "schema": "goal_output_artifact.v1",
                "output_artifact_id": output_id,
                "goal_id": goal_id,
                "task_id": task_id,
                "worker_id": str(worker_id or "") or None,
                "artifact_type": artifact_type,
                "created_at": _now_iso(),
                "input_usage_refs": list(input_usage_refs or []),
                "artifact_ref": artifact_ref,
                "content_hash": content_hash,
                "status": "created",
                "provenance_summary": self._build_provenance_summary(
                    task_id=task_id,
                    worker_id=worker_id,
                    input_usage_refs=list(input_usage_refs or []),
                ),
            }
            created.append(self.record_output_artifact(goal_id=goal_id, output_artifact=output_payload))
        return created

    @staticmethod
    def _find_grant(graph: dict[str, Any], grant_id: str) -> dict[str, Any] | None:
        for item in graph.get("source_grants", []):
            if str(item.get("grant_id") or "") == grant_id:
                return item
        return None

    @staticmethod
    def _find_usage(graph: dict[str, Any], usage_id: str) -> dict[str, Any] | None:
        for item in graph.get("source_usages", []):
            if str(item.get("usage_id") or "") == str(usage_id):
                return item
        return None

    @staticmethod
    def _has_usage(graph: dict[str, Any], usage_id: str) -> bool:
        return any(str(item.get("usage_id") or "") == str(usage_id) for item in graph.get("source_usages", []))

    @staticmethod
    def _find_active_grant_for_artifact(graph: dict[str, Any], artifact_ref: str) -> dict[str, Any] | None:
        for item in graph.get("source_grants", []):
            if str(item.get("artifact_ref") or "") != artifact_ref:
                continue
            active, _reason = is_grant_active(item)
            if active:
                return item
        return None

    @staticmethod
    def _build_usage_id(*, goal_id: str, grant_id: str, artifact_ref: str, context_hash: str) -> str:
        digest = hashlib.sha1(f"{goal_id}:{grant_id}:{artifact_ref}:{context_hash}".encode("utf-8")).hexdigest()[:16]
        return f"usage-{digest}"

    @staticmethod
    def _build_output_id(*, task_id: str, artifact_ref: str, index: int) -> str:
        digest = hashlib.sha1(f"{task_id}:{artifact_ref}:{index}".encode("utf-8")).hexdigest()[:14]
        return f"out-{digest}"

    @staticmethod
    def _build_content_hash(ref: dict[str, Any]) -> str:
        payload = "|".join(
            [
                str(ref.get("artifact_id") or ""),
                str(ref.get("trace_bundle_ref") or ""),
                str(ref.get("workspace_relative_path") or ""),
                str(ref.get("kind") or ""),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_provenance_summary(*, task_id: str, worker_id: str | None, input_usage_refs: list[str]) -> str:
        if input_usage_refs:
            return (
                f"task={task_id}; worker={str(worker_id or 'unknown')}; "
                f"input_usage_refs={len(input_usage_refs)}"
            )
        return f"task={task_id}; worker={str(worker_id or 'unknown')}; input_usage_refs=none"

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
