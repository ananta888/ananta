from __future__ import annotations

from typing import Any

from agent.artifacts.artifact_access_policy import ArtifactAccessPolicy
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.repository_registry import get_repository_registry
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


class ArtifactCandidateService:
    def __init__(
        self,
        *,
        source_registry: SourceRegistry | None = None,
        source_snapshots: SourceSnapshotStore | None = None,
        goal_artifact_service: GoalArtifactService | None = None,
        policy: ArtifactAccessPolicy | None = None,
    ) -> None:
        self._sources = source_registry or SourceRegistry()
        self._snapshots = source_snapshots or SourceSnapshotStore()
        self._goal_artifacts = goal_artifact_service or GoalArtifactService()
        self._policy = policy or ArtifactAccessPolicy()

    def list_candidates(
        self,
        *,
        goal_id: str,
        artifact_type: str | None = None,
        sensitivity: str | None = None,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        candidates.extend(self._source_candidates(goal_id=goal_id))
        candidates.extend(self._goal_output_candidates(goal_id=goal_id))
        candidates.extend(self._uploaded_artifact_candidates(goal_id=goal_id))
        return self._apply_filters(
            candidates,
            artifact_type=artifact_type,
            sensitivity=sensitivity,
            source_id=source_id,
        )

    def _source_candidates(self, *, goal_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for descriptor in self._sources.list_sources(include_disabled=False):
            source_id = str(descriptor.get("source_id") or "").strip()
            if not source_id:
                continue
            latest = self._snapshots.latest_indexed_snapshot(source_id=source_id)
            snapshot_ref = str((latest or {}).get("snapshot_id") or "latest")
            artifact_ref = f"sources:{source_id}:{snapshot_ref}"
            boundary = "public" if str(descriptor.get("trust_level") or "").startswith("official_") else "project_private"
            sensitivity = "public" if boundary == "public" else "internal"
            policy_decision = self._policy.evaluate(
                goal_id=goal_id,
                artifact_sensitivity=sensitivity,
                requested_usage="use_as_context",
                worker_kind="general",
                provider_location="local",
                data_boundary=boundary,
                allow_approved_cloud=False,
            ).as_dict()
            rows.append(
                {
                    "artifact_ref": artifact_ref,
                    "artifact_type": "source_snapshot",
                    "origin": "source_registry",
                    "source_id": source_id,
                    "sensitivity": sensitivity,
                    "suggested_usages": ["read", "quote", "summarize", "use_as_context"],
                    "default_policy_decision": policy_decision["decision"],
                    "policy_decision_ref": policy_decision["policy_decision_ref"],
                    "requires_approval": policy_decision["decision"] != "allow",
                }
            )
        return rows

    def _goal_output_candidates(self, *, goal_id: str) -> list[dict[str, Any]]:
        graph = self._goal_artifacts.get_goal_graph(goal_id)
        rows: list[dict[str, Any]] = []
        for output in list(graph.get("output_artifacts") or []):
            artifact_ref = str(output.get("artifact_ref") or "").strip()
            if not artifact_ref:
                continue
            policy_decision = self._policy.evaluate(
                goal_id=goal_id,
                artifact_sensitivity="internal",
                requested_usage="read",
                worker_kind="general",
                provider_location="local",
                data_boundary="project_private",
                allow_approved_cloud=False,
            ).as_dict()
            rows.append(
                {
                    "artifact_ref": artifact_ref,
                    "artifact_type": "goal_output",
                    "origin": "goal_outputs",
                    "source_id": str(output.get("goal_id") or ""),
                    "sensitivity": "internal",
                    "suggested_usages": ["read", "summarize", "transform", "use_as_context"],
                    "default_policy_decision": policy_decision["decision"],
                    "policy_decision_ref": policy_decision["policy_decision_ref"],
                    "requires_approval": policy_decision["decision"] != "allow",
                }
            )
        return rows

    def _uploaded_artifact_candidates(self, *, goal_id: str) -> list[dict[str, Any]]:
        repo = get_repository_registry().artifact_repo
        rows: list[dict[str, Any]] = []
        for artifact in repo.get_all():
            artifact_id = str(getattr(artifact, "id", "") or "").strip()
            if not artifact_id:
                continue
            policy_decision = self._policy.evaluate(
                goal_id=goal_id,
                artifact_sensitivity="internal",
                requested_usage="read",
                worker_kind="general",
                provider_location="local",
                data_boundary="project_private",
                allow_approved_cloud=False,
            ).as_dict()
            rows.append(
                {
                    "artifact_ref": f"artifact:{artifact_id}",
                    "artifact_type": "uploaded_artifact",
                    "origin": "artifact_repo",
                    "source_id": "",
                    "sensitivity": "internal",
                    "suggested_usages": ["read", "summarize", "transform"],
                    "default_policy_decision": policy_decision["decision"],
                    "policy_decision_ref": policy_decision["policy_decision_ref"],
                    "requires_approval": policy_decision["decision"] != "allow",
                }
            )
        return rows

    @staticmethod
    def _apply_filters(
        rows: list[dict[str, Any]],
        *,
        artifact_type: str | None,
        sensitivity: str | None,
        source_id: str | None,
    ) -> list[dict[str, Any]]:
        type_filter = str(artifact_type or "").strip().lower()
        sensitivity_filter = str(sensitivity or "").strip().lower()
        source_filter = str(source_id or "").strip().lower()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if type_filter and str(row.get("artifact_type") or "").strip().lower() != type_filter:
                continue
            if sensitivity_filter and str(row.get("sensitivity") or "").strip().lower() != sensitivity_filter:
                continue
            if source_filter and str(row.get("source_id") or "").strip().lower() != source_filter:
                continue
            filtered.append(row)
        return filtered
