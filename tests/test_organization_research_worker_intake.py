from __future__ import annotations

import base64
import copy
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from agent.config import settings
from agent.db_models import ContextBundleDB, TaskDB
from agent.services.organization_research_delegation_policy_service import (
    build_authoritative_research_context_policy,
)
from agent.services.organization_research_dispatch_capability_service import (
    OrganizationResearchDispatchCapabilityError,
    OrganizationResearchDispatchCapabilityIssuer,
    OrganizationResearchDispatchCapabilityVerifier,
)
from agent.services.organization_research_worker_intake_service import (
    OrganizationResearchWorkerIntakeError,
    OrganizationResearchWorkerIntakeService,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519SigningKeyRing,
)

_WORKER_URL = "http://research-worker:5000"
_HUB_SIGNING_KEY_RING = Ed25519SigningKeyRing(
    {"hub-research-test": base64.b64encode(b"h" * 32)},
    active_key_id="hub-research-test",
)


def _issuer(
    *,
    key_ring: Ed25519SigningKeyRing = _HUB_SIGNING_KEY_RING,
) -> OrganizationResearchDispatchCapabilityIssuer:
    return OrganizationResearchDispatchCapabilityIssuer(key_ring)


def _verifier() -> OrganizationResearchDispatchCapabilityVerifier:
    return OrganizationResearchDispatchCapabilityVerifier(
        _HUB_SIGNING_KEY_RING.verification_key_ring()
    )


def _origin_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        id="catalog-context-origin",
        retrieval_run_id="catalog-retrieval-origin",
        task_id="research-parent-1",
        bundle_type="worker_execution_context",
        context_text="[SRC_0001] authoritative evidence",
        chunks=[
            {
                "engine": "organization_source_catalog",
                "source": "docs/evidence.md",
                "content": "authoritative evidence",
                "metadata": {"source_id": "SRC_0001"},
            }
        ],
        token_estimate=9,
        bundle_metadata={
            "schema": "organization_source_catalog_context.v1",
            "authority": "hub",
            "llm_scope": "local_only",
            "catalog_task_id": "catalog-task-1",
            "catalog_id": "catalog-1",
            "catalog_hash": "a" * 64,
        },
    )


def _unsigned_payload() -> dict:
    bundle = _origin_bundle()
    source_policy = build_authoritative_research_context_policy(
        bundle=bundle,
        catalog_task_id="catalog-task-1",
        source_catalog_id="catalog-1",
        source_catalog_hash="a" * 64,
    )
    destination = {
        "schema": "organization_research_destination_binding.v1",
        "destination_id": "destination-1",
        "destination_digest": "d" * 64,
        "worker_url": _WORKER_URL,
        "worker_id": "research-worker",
        "worker_kind": "worker",
        "runtime_target_id": "runtime-target-1",
        "runtime_id": "runtime-1",
        "runtime_kind": "docker_container",
        "provider_id": "codex",
        "model_id": "local-model",
        "model_class": "code",
        "provider_location": "local_container",
        "data_residency": "local",
        "llm_scope": "local_only",
        "binding_digest": "b" * 64,
    }
    assignment = {
        "schema": "organization_category_research_assignment_binding.v1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "organization_id": "organization-1",
        "goal_id": "goal-1",
        "unit_id": "unit-1",
        "team_id": "team-1",
        "role_slot_id": "slot-1",
        "assignment_id": "organization-assignment-1",
        "agent_url": _WORKER_URL,
        "required_capabilities": [
            "planning",
            "research",
            "source_analysis",
        ],
        "effective_policy_hash": "e" * 64,
        "binding_digest": "f" * 64,
    }
    worker_context = {
        "context_bundle_id": bundle.id,
        "llm_scope": "local_only",
        "source_context_policy": source_policy,
        "source_context_bundle_manifest": {
            "schema": "organization_research_context_manifest.v1",
            "id": bundle.id,
            "retrieval_run_id": bundle.retrieval_run_id,
            "task_id": bundle.task_id,
            "bundle_type": bundle.bundle_type,
        },
        "research_destination_binding": destination,
        "planning_research_assignment": assignment,
        "task_proposal_binding": {
            "schema": "worker_task_proposal_binding.v1",
            "organization_id": "organization-1",
            "unit_id": "unit-1",
            "team_id": "team-1",
            "role_slot_id": "slot-1",
            "assignment_id": "research-subtask-1",
            "dispatch_lease_id": "worker-job-1",
            "worker_id": _WORKER_URL,
        },
        "context": {
            "context_text": bundle.context_text,
            "chunks": bundle.chunks,
            "token_estimate": bundle.token_estimate,
            "bundle_metadata": bundle.bundle_metadata,
        },
        "allowed_tools": [],
        "expected_output_schema": {
            "schema_ref": "todos/todo.schema.json"
        },
    }
    return {
        "id": "research-subtask-1",
        "title": "Delegated Organization research",
        "description": "Produce the Category Todo.",
        "parent_task_id": bundle.task_id,
        "priority": "High",
        "team_id": "team-1",
        "goal_id": "goal-1",
        "goal_trace_id": "trace-1",
        "task_kind": "planning_research",
        "retrieval_intent": "authoritative_source_catalog",
        "required_context_scope": "exact_task_context_bundle",
        "preferred_bundle_mode": "authoritative",
        "required_capabilities": [
            "planning",
            "research",
            "source_analysis",
        ],
        "context_bundle_id": bundle.id,
        "worker_execution_context": worker_context,
        "callback_url": "http://hub:5000/tasks/research-parent-1/callback",
        "callback_token": "callback-capability",
        "assignment_id": "research-subtask-1",
        "dispatch_lease_id": "worker-job-1",
        "source": "agent",
        "created_by": "hub",
        "context_bundle_policy": {
            "mode": "authoritative_source_catalog_bundle",
            "llm_scope": "local_only",
            "research_destination_binding": destination,
        },
    }


def _signed_payload() -> dict:
    payload = _unsigned_payload()
    policy = payload["worker_execution_context"]["source_context_policy"]
    destination = payload["worker_execution_context"][
        "research_destination_binding"
    ]
    payload["hub_dispatch_capability"] = (
        _issuer().issue(
            payload=payload,
            worker_url=_WORKER_URL,
            source_context_bundle_digest=policy[
                "context_bundle_digest"
            ],
            destination_binding_digest=destination["binding_digest"],
            worker_job_id="worker-job-1",
        )
    )
    return payload


@pytest.fixture()
def intake_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[ContextBundleDB.__table__, TaskDB.__table__],
    )
    return engine


def test_dispatch_capability_is_worker_payload_job_and_assignment_bound():
    payload = _signed_payload()
    service = _verifier()

    claims = service.verify(
        payload["hub_dispatch_capability"],
        payload=payload,
        worker_url=_WORKER_URL,
    )

    assert claims["worker_job_id"] == "worker-job-1"
    assert claims["organization_assignment_id"] == (
        "organization-assignment-1"
    )
    assert claims["context_bundle_id"] == "catalog-context-origin"
    assert claims["destination_binding_digest"] == "b" * 64

    tampered = copy.deepcopy(payload)
    tampered["description"] = "tampered"
    with pytest.raises(
        OrganizationResearchDispatchCapabilityError,
        match="organization_research_dispatch_payload_mismatch",
    ):
        service.verify(
            payload["hub_dispatch_capability"],
            payload=tampered,
            worker_url=_WORKER_URL,
        )


def test_worker_intake_materializes_task_bound_context_and_replays_exactly(
    intake_engine,
):
    payload = _signed_payload()
    service = OrganizationResearchWorkerIntakeService(
        capability_verifier=_verifier(),
        session_factory=lambda: Session(intake_engine)
    )

    first = service.admit(
        payload,
        worker_url=_WORKER_URL,
    )
    replay = service.admit(
        payload,
        worker_url=_WORKER_URL,
    )

    assert first["accepted"] is True
    assert first["replayed"] is False
    assert replay["replayed"] is True
    with Session(intake_engine) as session:
        task = session.get(TaskDB, "research-subtask-1")
        assert task is not None
        assert task.parent_task_id == "research-parent-1"
        assert task.current_worker_job_id == "worker-job-1"
        assert task.context_bundle_id == first["context_bundle_id"]
        assert "context" not in task.worker_execution_context
        admission = task.worker_execution_context[
            "hub_research_dispatch_admission"
        ]
        assert admission["organization_assignment_id"] == (
            "organization-assignment-1"
        )
        assert admission["destination_binding_digest"] == "b" * 64
        clone = session.get(ContextBundleDB, task.context_bundle_id)
        assert clone is not None
        assert clone.task_id == task.id
        assert clone.context_text == (
            "[SRC_0001] authoritative evidence"
        )
        assert clone.bundle_metadata["hub_research_dispatch"][
            "origin_context_bundle_id"
        ] == "catalog-context-origin"


def test_worker_intake_recomputes_origin_context_digest(intake_engine):
    payload = _unsigned_payload()
    payload["worker_execution_context"]["context"][
        "context_text"
    ] = "content changed before signing"
    policy = payload["worker_execution_context"]["source_context_policy"]
    destination = payload["worker_execution_context"][
        "research_destination_binding"
    ]
    payload["hub_dispatch_capability"] = (
        _issuer().issue(
            payload=payload,
            worker_url=_WORKER_URL,
            source_context_bundle_digest=policy[
                "context_bundle_digest"
            ],
            destination_binding_digest=destination["binding_digest"],
            worker_job_id="worker-job-1",
        )
    )
    service = OrganizationResearchWorkerIntakeService(
        capability_verifier=_verifier(),
        session_factory=lambda: Session(intake_engine)
    )

    with pytest.raises(
        OrganizationResearchWorkerIntakeError,
        match="organization_research_dispatch_context_digest_mismatch",
    ):
        service.admit(
            payload,
            worker_url=_WORKER_URL,
        )

    with Session(intake_engine) as session:
        assert session.get(TaskDB, "research-subtask-1") is None


def test_worker_service_credential_cannot_mint_hub_dispatch_authority(
    intake_engine,
):
    payload = _unsigned_payload()
    policy = payload["worker_execution_context"]["source_context_policy"]
    destination = payload["worker_execution_context"][
        "research_destination_binding"
    ]
    rogue_key_ring = Ed25519SigningKeyRing(
        {"worker-controlled": base64.b64encode(b"w" * 32)},
        active_key_id="worker-controlled",
    )
    payload["hub_dispatch_capability"] = _issuer(
        key_ring=rogue_key_ring
    ).issue(
        payload=payload,
        worker_url=_WORKER_URL,
        source_context_bundle_digest=policy["context_bundle_digest"],
        destination_binding_digest=destination["binding_digest"],
        worker_job_id="worker-job-1",
    )
    service = OrganizationResearchWorkerIntakeService(
        capability_verifier=_verifier(),
        session_factory=lambda: Session(intake_engine)
    )

    with pytest.raises(
        OrganizationResearchWorkerIntakeError,
        match=(
            "organization_research_dispatch_capability_signature_invalid"
        ),
    ):
        service.admit(
            payload,
            worker_url=_WORKER_URL,
        )


def test_worker_intake_requires_public_verification_keyring(
    monkeypatch,
):
    monkeypatch.delenv(
        "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE",
        raising=False,
    )
    service = OrganizationResearchWorkerIntakeService()

    with pytest.raises(
        OrganizationResearchWorkerIntakeError,
        match=(
            "organization_research_dispatch_verification_keyring_required"
        ),
    ) as exc_info:
        service.admit(
            _signed_payload(),
            worker_url=_WORKER_URL,
        )

    assert exc_info.value.status_code == 503


def test_internal_research_intake_rejects_hub_role(
    client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "role", "hub")

    response = client.post(
        "/internal/tasks/organization-planning-research",
        json={},
        headers={
            "Authorization": (
                "Bearer test-agent-token-with-sufficient-length-1234567890"
            )
        },
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == (
        "organization_research_worker_role_required"
    )


def test_internal_research_intake_requires_service_identity(
    client,
    auth_header,
):
    response = client.post(
        "/internal/tasks/organization-planning-research",
        json={},
        headers=auth_header,
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == (
        "workflow_service_auth_required"
    )


def test_internal_research_intake_passes_only_canonical_worker_identity(
    client,
    monkeypatch,
):
    from agent.routes.tasks import organization_research_intake

    calls = []

    class Intake:
        def admit(self, payload, *, worker_url):
            calls.append(
                {
                    "payload": dict(payload),
                    "worker_url": worker_url,
                }
            )
            return {
                "accepted": True,
                "replayed": False,
                "task_id": "research-subtask-1",
                "context_bundle_id": "delegated-context-1",
            }

    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(settings, "agent_url", _WORKER_URL)
    monkeypatch.setattr(
        organization_research_intake,
        "get_organization_research_worker_intake_service",
        lambda: Intake(),
    )

    response = client.post(
        "/internal/tasks/organization-planning-research",
        json={"id": "research-subtask-1"},
        headers={
            "Authorization": (
                "Bearer test-agent-token-with-sufficient-length-1234567890"
            )
        },
    )

    assert response.status_code == 202
    assert calls == [
        {
            "payload": {"id": "research-subtask-1"},
            "worker_url": _WORKER_URL,
        }
    ]


def test_internal_research_intake_rejects_missing_canonical_worker_identity(
    client,
    monkeypatch,
):
    from agent.routes.tasks import organization_research_intake

    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(settings, "agent_url", "")
    monkeypatch.setattr(
        organization_research_intake,
        "get_organization_research_worker_intake_service",
        lambda: pytest.fail("intake must not run without AGENT_URL"),
    )

    response = client.post(
        "/internal/tasks/organization-planning-research",
        json={"id": "research-subtask-1"},
        headers={
            "Authorization": (
                "Bearer test-agent-token-with-sufficient-length-1234567890"
            ),
            "Host": "spoofed-worker:5000",
        },
    )

    assert response.status_code == 503
    assert response.get_json()["data"]["reason_code"] == (
        "organization_research_worker_identity_unavailable"
    )


def test_public_tasks_still_reject_reserved_research_context(client):
    response = client.post(
        "/tasks",
        json={
            "title": "untrusted",
            "task_kind": "planning_research",
            "context_bundle_id": "forged-context",
            "worker_execution_context": {
                "context_bundle_id": "forged-context",
                "context": {"context_text": "forged"},
            },
        },
        headers={
            "Authorization": (
                "Bearer test-agent-token-with-sufficient-length-1234567890"
            )
        },
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == (
        "context_bundle_reserved_ingress_forbidden"
    )
