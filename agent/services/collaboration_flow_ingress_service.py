"""Authenticated ingress adapters for canonical collaboration flow events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ananta_contracts.collaboration_workspace import WorkspaceEventV1, canonical_digest, require_id


class CollaborationIngressAuthenticator(Protocol):
    def authenticate(
        self,
        *,
        tenant_id: str,
        credentials: Mapping[str, Any],
        message_digest: str,
    ) -> Mapping[str, Any]: ...


class CollaborationEventSink(Protocol):
    def append_event(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        event: Mapping[str, Any],
    ) -> dict[str, Any]: ...


class CollaborationFlowIngressService:
    """Uses source-specific authentication and one canonical mapping implementation."""

    def __init__(
        self,
        sink: CollaborationEventSink,
        *,
        webhook_auth: CollaborationIngressAuthenticator,
        local_git_auth: CollaborationIngressAuthenticator,
        worker_auth: CollaborationIngressAuthenticator,
    ) -> None:
        self._sink = sink
        self._authenticators = {
            "webhook": webhook_auth,
            "local_git": local_git_auth,
            "worker": worker_auth,
        }

    def ingest_webhook(self, *, tenant_id: str, credentials: Mapping[str, Any], message: Mapping[str, Any]):
        return self._ingest("webhook", tenant_id=tenant_id, credentials=credentials, message=message)

    def ingest_local_git(self, *, tenant_id: str, credentials: Mapping[str, Any], message: Mapping[str, Any]):
        return self._ingest("local_git", tenant_id=tenant_id, credentials=credentials, message=message)

    def ingest_worker(self, *, tenant_id: str, credentials: Mapping[str, Any], message: Mapping[str, Any]):
        return self._ingest("worker", tenant_id=tenant_id, credentials=credentials, message=message)

    def _ingest(
        self,
        source: str,
        *,
        tenant_id: str,
        credentials: Mapping[str, Any],
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        digest = canonical_digest(message)
        decision = dict(
            self._authenticators[source].authenticate(
                tenant_id=tenant,
                credentials=credentials,
                message_digest=digest,
            )
        )
        if set(decision) != {"authenticated", "reason_code"} or decision["authenticated"] is not True:
            reason = require_id(decision.get("reason_code"), "ingress_reason_code")
            raise PermissionError(reason)
        envelope = self._canonical_event(source, message, digest)
        return self._sink.append_event(
            tenant_id=tenant,
            workspace_id=envelope["workspace_id"],
            principal_actor_id=envelope["actor_binding_id"],
            event=envelope,
        )

    @staticmethod
    def _canonical_event(source: str, message: Mapping[str, Any], message_digest: str) -> dict[str, Any]:
        required = {
            "external_event_id",
            "workspace_id",
            "room_id",
            "actor_binding_id",
            "kind",
            "subject_id",
            "revision",
            "state",
            "change",
            "head_sha",
            "summary",
            "artifact",
            "project_id",
            "task_id",
            "repository_revision",
            "evidence_scope",
            "source_refs",
            "run_refs",
            "occurred_at",
        }
        if set(message) != required:
            raise ValueError("collaboration_flow_ingress_fields_invalid")
        kind = str(message["kind"])
        subject_id = require_id(message["subject_id"], "flow_subject_id")
        common = {
            "project_id": require_id(message["project_id"], "project_id"),
            "task_id": require_id(message["task_id"], "task_id"),
            "repository_revision": str(message["repository_revision"] or "").strip().lower(),
            "evidence_scope": str(message["evidence_scope"] or "").strip(),
            "summary": str(message["summary"] or "").strip()[:512],
            "ingress_source": source,
            "ingress_message_digest": message_digest,
        }
        if kind == "git_ref":
            revision = message["revision"]
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise ValueError("collaboration_git_ref_revision_invalid")
            head = str(message["head_sha"] or "").lower()
            change = str(message["change"] or "")
            if len(head) not in {40, 64} or any(character not in "0123456789abcdef" for character in head):
                raise ValueError("collaboration_git_head_invalid")
            if change not in {"create", "update", "rename", "delete", "force_push"}:
                raise ValueError("collaboration_git_change_invalid")
            event_type = "git.projected"
            payload = {**common, "ref_id": subject_id, "ref_revision": revision, "head_sha": head, "change": change}
        elif kind in {"task_status", "workflow_status"}:
            event_type = "task.projected" if kind == "task_status" else "workflow.projected"
            key = "task_id" if kind == "task_status" else "workflow_id"
            payload = {**common, key: subject_id, "state": require_id(message["state"], "flow_state")}
        elif kind in {"patch", "log"}:
            artifact = message["artifact"]
            if not isinstance(artifact, Mapping):
                raise ValueError("collaboration_flow_artifact_required")
            payload = {**dict(artifact), **common, "artifact_kind": kind, "subject_id": subject_id}
            if {"content", "bytes", "local_path"}.intersection(payload):
                raise ValueError("collaboration_flow_artifact_inline_content_forbidden")
            event_type = "artifact.linked"
        else:
            raise ValueError("collaboration_flow_ingress_kind_invalid")
        source_refs = CollaborationFlowIngressService._refs(message["source_refs"], "source_ref")
        run_refs = CollaborationFlowIngressService._refs(message["run_refs"], "run_ref")
        external_id = require_id(message["external_event_id"], "external_event_id")
        workspace = require_id(message["workspace_id"], "workspace_id")
        actor = require_id(message["actor_binding_id"], "actor_binding_id")
        return WorkspaceEventV1.from_mapping(
            {
                "schema": WorkspaceEventV1.SCHEMA,
                "event_id": f"event-{canonical_digest([source, external_id])[:32]}",
                "workspace_id": workspace,
                "room_id": message["room_id"],
                "thread_id": None,
                "event_type": event_type,
                "actor_binding_id": actor,
                "idempotency_key": f"{source}-{external_id}",
                "correlation_id": f"flow-{canonical_digest([source, external_id])[:32]}",
                "causation_id": None,
                "visibility": "room" if message["room_id"] is not None else "workspace",
                "retention": "standard",
                "occurred_at": message["occurred_at"],
                "payload": payload,
                "payload_digest": canonical_digest(payload),
                "source_refs": source_refs,
                "run_refs": run_refs,
            }
        ).to_dict()

    @staticmethod
    def _refs(value: object, field: str) -> list[str]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError(f"collaboration_{field}s_invalid")
        return [require_id(item, field) for item in value]


__all__ = [
    "CollaborationEventSink",
    "CollaborationFlowIngressService",
    "CollaborationIngressAuthenticator",
]
