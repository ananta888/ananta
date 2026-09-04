"""Read-only Hub-state projections for task, workflow, Git and release views."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest, require_id


class CollaborationFlowProjectionService:
    """Projects admitted events and never writes authoritative task/Git state."""

    def __init__(self, store: CollaborationWorkspaceStore) -> None:
        self._store = store

    def rebuild(
        self,
        tenant_id: str,
        workspace_id: str,
        *,
        principal_actor_id: str | None = None,
    ) -> dict[str, Any]:
        events = self._store.projection_events(tenant_id, workspace_id)
        if principal_actor_id is not None:
            events = [
                event
                for event in events
                if event.get("room_id") is None
                or self._store.room_visible(
                    tenant_id,
                    workspace_id,
                    event["room_id"],
                    principal_actor_id,
                )
            ]
        tasks: dict[str, dict[str, Any]] = {}
        workflows: dict[str, dict[str, Any]] = {}
        refs: dict[str, dict[str, Any]] = {}
        reviews: dict[str, dict[str, Any]] = {}
        artifacts: dict[str, dict[str, Any]] = {}
        for event in events:
            payload = event.get("payload") or {}
            if event["event_type"] == "task.projected":
                task_id = require_id(payload.get("task_id"), "task_id")
                tasks[task_id] = self._verified_projection(event, payload)
            elif event["event_type"] == "workflow.projected":
                workflow_id = require_id(payload.get("workflow_id"), "workflow_id")
                workflows[workflow_id] = self._verified_projection(event, payload)
            elif event["event_type"] == "git.projected":
                ref = require_id(payload.get("ref_id"), "git_ref_id")
                revision = payload.get("ref_revision")
                if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                    raise ValueError("collaboration_git_ref_revision_invalid")
                current = refs.get(ref)
                if current is not None and current["ref_revision"] >= revision:
                    continue
                refs[ref] = {
                    **self._verified_projection(event, payload),
                    "history_discontinuity": payload.get("change") == "force_push",
                }
            elif event["event_type"] in {"review.recorded", "decision.recorded"}:
                subject_digest = str(payload.get("subject_digest") or "")
                if len(subject_digest) != 64:
                    raise ValueError("collaboration_review_subject_digest_invalid")
                reviews[subject_digest] = self._verified_projection(event, payload)
            elif event["event_type"] == "artifact.linked":
                artifact_id = require_id(payload.get("artifact_id"), "artifact_id")
                artifacts[artifact_id] = {
                    **dict(payload),
                    "event_id": event["event_id"],
                    "workspace_sequence": event["sequence"],
                    "availability": "available" if payload.get("scan_status") == "clean" else "partial",
                }
        state = {
            "tasks": {key: tasks[key] for key in sorted(tasks)},
            "workflows": {key: workflows[key] for key in sorted(workflows)},
            "git_refs": {key: refs[key] for key in sorted(refs)},
            "reviews": {key: reviews[key] for key in sorted(reviews)},
            "artifacts": {key: artifacts[key] for key in sorted(artifacts)},
        }
        return {
            "schema": "ananta.collaboration-flow-projection.v1",
            "workspace_id": workspace_id,
            "checkpoint": int(events[-1]["sequence"]) if events else 0,
            "state": state,
            "state_digest": canonical_digest(state),
            "writes_authoritative_state": False,
            "worker_invoked": False,
        }

    def propose_release_notes(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        visible_event_ids: set[str],
    ) -> dict[str, Any]:
        events = [
            event
            for event in self._store.projection_events(tenant_id, workspace_id)
            if event["event_id"] in visible_event_ids
            and event["event_type"] in {"decision.recorded", "review.recorded", "task.projected", "git.projected"}
        ]
        entries = [
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "summary": str((event.get("payload") or {}).get("summary") or "verified change")[:512],
                "source_refs": event.get("source_refs") or [],
                "run_refs": event.get("run_refs") or [],
            }
            for event in events
        ]
        return {
            "schema": "ananta.collaboration-release-notes-proposal.v1",
            "workspace_id": workspace_id,
            "entries": entries,
            "proposal_digest": canonical_digest(entries),
            "published": False,
            "requires_hub_release_workflow": True,
        }

    @staticmethod
    def _verified_projection(event: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        if not event.get("source_refs") or len(event.get("run_refs") or []) != 1:
            raise ValueError("collaboration_projection_grounding_missing")
        return {
            **dict(payload),
            "event_id": event["event_id"],
            "workspace_sequence": event["sequence"],
            "verification_status": "hub_verified",
            "source_refs": list(event["source_refs"]),
            "run_refs": list(event["run_refs"]),
        }


__all__ = ["CollaborationFlowProjectionService"]
