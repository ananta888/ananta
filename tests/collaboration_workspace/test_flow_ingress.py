from __future__ import annotations

import pytest

from agent.services.collaboration_flow_ingress_service import CollaborationFlowIngressService


class Authentication:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    def authenticate(self, *, tenant_id, credentials, message_digest):
        assert credentials == {"credential": "opaque"}
        self.calls.append((tenant_id, message_digest))
        return {
            "authenticated": self.allowed,
            "reason_code": "ingress_authenticated" if self.allowed else "ingress_signature_invalid",
        }


class Sink:
    def __init__(self) -> None:
        self.events = []

    def append_event(self, **values):
        self.events.append(values)
        return values["event"]


def _message(kind: str = "git_ref"):
    return {
        "external_event_id": "delivery-a",
        "workspace_id": "workspace-a",
        "room_id": "room-a",
        "actor_binding_id": "service-git-a",
        "kind": kind,
        "subject_id": "branch-main",
        "revision": 3,
        "state": "succeeded",
        "change": "force_push",
        "head_sha": "a" * 40,
        "summary": "Updated main",
        "artifact": None,
        "project_id": "project-a",
        "task_id": "task-a",
        "repository_revision": "b" * 40,
        "evidence_scope": "local",
        "source_refs": ["SRC_registered"],
        "run_refs": ["RUN_registered"],
        "occurred_at": 100.0,
    }


def test_three_ingress_types_share_canonical_mapping_but_authenticate_separately() -> None:
    sink = Sink()
    authenticators = [Authentication(), Authentication(), Authentication()]
    ingress = CollaborationFlowIngressService(
        sink,
        webhook_auth=authenticators[0],
        local_git_auth=authenticators[1],
        worker_auth=authenticators[2],
    )
    events = [
        ingress.ingest_webhook(tenant_id="tenant-a", credentials={"credential": "opaque"}, message=_message()),
        ingress.ingest_local_git(tenant_id="tenant-a", credentials={"credential": "opaque"}, message=_message()),
        ingress.ingest_worker(tenant_id="tenant-a", credentials={"credential": "opaque"}, message=_message()),
    ]
    assert [event["payload"]["ingress_source"] for event in events] == ["webhook", "local_git", "worker"]
    assert all(event["event_type"] == "git.projected" for event in events)
    assert all(len(authenticator.calls) == 1 for authenticator in authenticators)


def test_patch_and_log_must_be_digest_bound_artifact_references() -> None:
    sink = Sink()
    auth = Authentication()
    ingress = CollaborationFlowIngressService(sink, webhook_auth=auth, local_git_auth=auth, worker_auth=auth)
    invalid = _message("patch")
    invalid["artifact"] = {"content": "large patch"}
    with pytest.raises(ValueError, match="inline_content_forbidden"):
        ingress.ingest_webhook(tenant_id="tenant-a", credentials={"credential": "opaque"}, message=invalid)


def test_failed_source_authentication_never_reaches_sink() -> None:
    sink = Sink()
    denied = Authentication(False)
    ingress = CollaborationFlowIngressService(
        sink, webhook_auth=denied, local_git_auth=Authentication(), worker_auth=Authentication()
    )
    with pytest.raises(PermissionError, match="ingress_signature_invalid"):
        ingress.ingest_webhook(tenant_id="tenant-a", credentials={"credential": "opaque"}, message=_message())
    assert sink.events == []
