from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.repositories.semantic_contract_repository import SemanticPrincipal
from agent.services.semantic_contract_service import (
    HubContractSigner,
    SemanticContractService,
    SemanticContractServiceError,
)


class Repository:
    def __init__(self) -> None:
        self.created = 0
        self.audit_events: list[object] = []

    def require_membership(self, *_args, **_kwargs) -> None:
        return None

    def get_create_replay(self, *_args, **_kwargs):
        return None

    def create(self, _principal, *, payload, audit_event=None, **_kwargs):
        self.created += 1
        self.audit_events.append(audit_event)
        return SimpleNamespace(
            id=payload["contract_id"],
            session_id=payload["session_id"],
            room_id=payload.get("room_id"),
            epoch=payload["epoch"],
            revision=payload["revision"],
            digest=payload["contract_digest"],
            status="offered",
            profile=payload["profile"],
            security_mode=payload["security_mode"],
            consent_version=payload["consent_version"],
            policy_version=payload["policy_version"],
            negotiation_started_at_ms=_kwargs["negotiation_started_at_ms"],
            negotiation_round_count=_kwargs["negotiation_round_count"],
            negotiation_message_count=_kwargs["negotiation_message_count"],
            contract_payload=payload,
        ), False


class Negotiation:
    def decide(self, **_kwargs):
        return SimpleNamespace(
            reason_code="hub_confirmed",
            contract={
                "contract_id": "semantic-contract-a",
                "contract_digest": "a" * 64,
                "session_id": "session-a",
                "room_id": None,
                "epoch": 4,
                "revision": 1,
                "profile": "balanced",
                "quality_level": "medium",
                "security_mode": "trusted_compute",
                "consent_version": 2,
                "policy_version": "policy-v1",
                "delay_ms": 200,
                "roles": {},
                "task_types": [],
                "max_artifact_bytes": 1024,
                "deadline_ms": 1000,
                "expires_at_ms": 9_999_999,
            },
        )


class Audit:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def prepare_transition(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("audit down")
        return {"prepared": True}


def _service(repository: Repository, audit: Audit) -> SemanticContractService:
    return SemanticContractService(
        repository,  # type: ignore[arg-type]
        negotiation=Negotiation(),  # type: ignore[arg-type]
        signer=HubContractSigner(b"x" * 32),
        clock=lambda: 1.0,
        feature_enabled=lambda: True,
        audit=audit,
    )


def _create(service: SemanticContractService):
    return service.create_offer(
        SemanticPrincipal("tenant-a", "owner-a"),
        session_id="session-a",
        room_id=None,
        epoch=4,
        policy_version="policy-v1",
        consent_version=2,
        security_confirmed=True,
        fallback_healthy=True,
        proposal={"profile": "balanced"},
        advertisements=(),
        idempotency_key="contract-create-key",
    )


def test_contract_write_emits_one_content_free_authoritative_audit_command() -> None:
    repository = Repository()
    audit = Audit()
    result = _create(_service(repository, audit))
    assert result["contract_id"] == "semantic-contract-a"
    assert repository.created == 1
    assert repository.audit_events == [{"prepared": True}]
    assert audit.calls == [
        {
            "idempotency_key": "semantic-contract:offer:contract-create-key",
            "tenant_id": "tenant-a",
            "scope": "semantic-contract:session-a",
            "event_type": "semantic_contract",
            "transition": "offered",
            "reason_code": "hub_confirmed",
            "epoch": 4,
            "contract_ref": "a" * 64,
        }
    ]


def test_contract_write_fails_closed_when_durable_audit_is_unavailable() -> None:
    repository = Repository()
    with pytest.raises(SemanticContractServiceError) as error:
        _create(_service(repository, Audit(fail=True)))
    assert error.value.reason_code == "semantic_audit_unavailable"
    assert error.value.status_code == 503
    assert repository.created == 0
