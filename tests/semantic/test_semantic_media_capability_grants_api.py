from __future__ import annotations

import time

import jwt
from flask import Flask

from agent.config import settings
from agent.repositories.semantic_contract_repository import SemanticPrincipal
from agent.repositories.semantic_media_capability_grant_repository import (
    InMemorySemanticMediaCapabilityGrantRepository,
)
from agent.routes import semantic_media_contracts as routes
from agent.services.semantic_media_permission_service import SemanticMediaPermissionService
from agent.services.user_session_tokens import issue_user_access_token


class _ShareAuthority:
    def __init__(self) -> None:
        self.permissions = {
            "chat": True,
            "view_tui": True,
            "remote_cursor": True,
            "artifact_share": True,
            "remote_control": False,
        }

    def get_session(self, session_id: str):
        if session_id != "session-a":
            return None
        return {
            "id": session_id,
            "owner_user_id": "owner-a",
            "permissions": dict(self.permissions),
            "expires_at": time.time() + 3600,
            "revoked_at": None,
        }

    def get_participants(self, session_id: str):
        return [
            {
                "user_id": "peer-a",
                "permissions": dict(self.permissions),
                "revoked_at": None,
            }
        ] if session_id == "session-a" else []


class _EpochAuthority:
    @staticmethod
    def current_epoch(scope_type: str, scope_id: str) -> int | None:
        return 1 if scope_type == "session" and scope_id == "session-a" else None


class _ContractAuthority:
    def __init__(self) -> None:
        self.calls: list[tuple[SemanticPrincipal, dict]] = []

    def establish_membership(self, *args, **kwargs) -> None:
        return None

    def create_offer(self, principal: SemanticPrincipal, **kwargs):
        self.calls.append((principal, kwargs))
        return {"contract_id": "contract-a", "revision": 1}


def _token(subject: str, tenant_id: str = "owner-a") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "tenant_id": tenant_id,
            "role": "admin",
            "iat": now,
            "exp": now + 600,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _setup(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SEMANTIC_COMPUTE_SECURITY_CONFIRMED=True)
    app.register_blueprint(routes.semantic_media_contracts_bp)
    share = _ShareAuthority()
    contracts = _ContractAuthority()
    permissions = SemanticMediaPermissionService(
        b"c" * 32,
        repository=InMemorySemanticMediaCapabilityGrantRepository(),
    )
    app.extensions["semantic_media_permission_service"] = permissions
    monkeypatch.setattr(routes, "get_share_session_service", lambda: share)
    monkeypatch.setattr(routes, "get_webrtc_epoch_service", lambda: _EpochAuthority())
    monkeypatch.setattr(routes, "get_semantic_contract_service", lambda: contracts)
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = (
        f"Bearer {issue_user_access_token(username='owner-a', role='admin')}"
    )
    return client, share, contracts


def _grant_body(capability: str = "publish") -> dict[str, object]:
    return {
        "session_id": "session-a",
        "epoch": 1,
        "subject_id": "owner-a",
        "subject_role": "participant",
        "capability": capability,
        "scope_kind": "session",
        "scope_id": "session-a",
        "direction": "egress",
        "data_type": "application/vnd.ananta.semantic-media-control+json",
        "purpose": "semantic_media_control",
        "expires_at_ms": int(time.time() * 1000) + 60_000,
    }


def _offer() -> dict[str, object]:
    return {
        "session_id": "session-a",
        "epoch": 1,
        "policy_version": "policy-v1",
        "consent_version": 1,
        "proposal": {"profile": "balanced", "delay_ms": 5_000},
    }


def test_production_route_requires_current_purpose_bound_grant_and_revoke_is_immediate(
    monkeypatch,
) -> None:
    client, _share, contracts = _setup(monkeypatch)
    missing = client.post(
        "/v1/semantic-media/contracts",
        json=_offer(),
        headers={"Idempotency-Key": "offer-without-grant"},
    )
    assert missing.status_code == 403
    assert missing.json["error"]["code"] == "capability_grant_required"

    grant_body = _grant_body()
    issued = client.post(
        "/v1/semantic-media/capability-grants",
        json=grant_body,
        headers={"Idempotency-Key": "grant-publish-owner-1"},
    )
    assert issued.status_code == 201
    grant_id = issued.json["grant"]["grant_id"]
    replay = client.post(
        "/v1/semantic-media/capability-grants",
        json=grant_body,
        headers={"Idempotency-Key": "grant-publish-owner-1"},
    )
    assert replay.status_code == 201
    assert replay.json["grant"] == issued.json["grant"]
    conflict = client.post(
        "/v1/semantic-media/capability-grants",
        json={**grant_body, "purpose": "different_purpose"},
        headers={"Idempotency-Key": "grant-publish-owner-1"},
    )
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "capability_grant_id_conflict"
    accepted = client.post(
        "/v1/semantic-media/contracts",
        json=_offer(),
        headers={
            "Idempotency-Key": "offer-with-valid-grant",
            "X-Semantic-Capability-Grant": grant_id,
        },
    )
    assert accepted.status_code == 201
    assert len(contracts.calls) == 1

    revoked = client.post(f"/v1/semantic-media/capability-grants/{grant_id}/revoke")
    assert revoked.status_code == 200
    assert revoked.json["grant"]["revocation_version"] == 1
    rejected = client.post(
        "/v1/semantic-media/contracts",
        json=_offer(),
        headers={
            "Idempotency-Key": "offer-after-revoke",
            "X-Semantic-Capability-Grant": grant_id,
        },
    )
    assert rejected.status_code == 403
    assert rejected.json["error"]["code"] == "capability_revoked"
    assert len(contracts.calls) == 1


def test_issue_endpoint_attenuates_share_rights_and_never_accepts_client_authority(
    monkeypatch,
) -> None:
    client, _share, _contracts = _setup(monkeypatch)
    denied_compute = client.post(
        "/v1/semantic-media/capability-grants",
        json=_grant_body("compute"),
        headers={"Idempotency-Key": "grant-denied-compute-1"},
    )
    assert denied_compute.status_code == 403
    assert denied_compute.json["error"]["code"] == "capability_escalation_denied"
    denied_training = client.post(
        "/v1/semantic-media/capability-grants",
        json=_grant_body("training_admission"),
        headers={"Idempotency-Key": "grant-denied-training-1"},
    )
    assert denied_training.status_code == 403
    assert denied_training.json["error"]["code"] == "capability_escalation_denied"
    injected = client.post(
        "/v1/semantic-media/capability-grants",
        json={**_grant_body(), "authorised_capabilities": ["training_admission"]},
        headers={"Idempotency-Key": "grant-injected-rights-1"},
    )
    assert injected.status_code == 400
    assert injected.json["error"]["code"] == "unknown_field"


def test_participant_cannot_issue_and_wrong_subject_cannot_use_owner_grant(monkeypatch) -> None:
    client, _share, _contracts = _setup(monkeypatch)
    issued = client.post(
        "/v1/semantic-media/capability-grants",
        json=_grant_body(),
        headers={"Idempotency-Key": "grant-owner-for-peer-test"},
    )
    assert issued.status_code == 201
    grant_id = issued.json["grant"]["grant_id"]

    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {_token('peer-a')}"
    issue_attempt = client.post(
        "/v1/semantic-media/capability-grants",
        json={**_grant_body(), "subject_id": "peer-a"},
        headers={"Idempotency-Key": "grant-peer-escalation-1"},
    )
    assert issue_attempt.status_code == 403
    assert issue_attempt.json["error"]["code"] == "capability_issue_denied"
    use_attempt = client.post(
        "/v1/semantic-media/contracts",
        json=_offer(),
        headers={
            "Idempotency-Key": "peer-uses-owner-grant",
            "X-Semantic-Capability-Grant": grant_id,
        },
    )
    assert use_attempt.status_code == 403
    assert use_attempt.json["error"]["code"] == "subject_mismatch"
