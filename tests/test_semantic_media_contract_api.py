from __future__ import annotations

import time

from flask import Flask

from agent.bootstrap.semantic_media_services import initialize_semantic_media_services
from agent.routes import semantic_media_contracts as routes
from agent.services.semantic_contract_service import SemanticContractServiceError
from agent.services.semantic_media_permission_service import SemanticMediaPermissionService
from agent.services.user_session_tokens import issue_user_access_token


class FakeService:
    def __init__(self):
        self.calls = []
        self.replayed = False
        self.error: SemanticContractServiceError | None = None
        self.execution = FakeExecutionService()

    def establish_membership(self, *args, **kwargs): pass

    def create_offer(self, principal, **kwargs):
        self.calls.append(("create", principal, kwargs))
        if self.error:
            raise self.error
        return {"contract_id": "contract-a", "revision": 1, "idempotent_replay": self.replayed}

    def mutate(self, principal, **kwargs):
        self.calls.append(("mutate", principal, kwargs))
        if self.error:
            raise self.error
        return {"contract_id": kwargs["contract_id"], "revision": kwargs["expected_revision"] + 1}

    def detail(self, principal, **kwargs):
        self.calls.append(("detail", principal, kwargs))
        if self.error:
            raise self.error
        return {
            "contract_id": kwargs["contract_id"],
            "revision": 1,
            "status": "active",
            "digest": "a" * 64,
            "profile": "balanced",
            "delay_ms": 5_000,
        }

    def list(self, principal, **kwargs):
        self.calls.append(("list", principal, kwargs))
        if self.error:
            raise self.error
        return {"items": [], "offset": kwargs["offset"], "limit": kwargs["limit"], "next_offset": None}


class FakeExecutionService:
    def __init__(self):
        self.calls = []

    def register_candidate_key(self, principal, **kwargs):
        self.calls.append(("key", principal, kwargs))
        return {"key_id": kwargs["key_id"], "authoritative_source": "hub"}

    def advertise_candidate(self, principal, **kwargs):
        self.calls.append(("advertise", principal, kwargs))
        return {"advertisement_id": kwargs["advertisement"]["advertisement_id"], "authoritative": False}

    def list_candidate_claims(self, principal, **kwargs):
        self.calls.append(("claims", principal, kwargs))
        return {"items": [], "authoritative": False}

    def schedule(self, principal, **kwargs):
        self.calls.append(("schedule", principal, kwargs))
        return {"leases": [], "authoritative_source": "hub"}

    def list_leases(self, principal, **kwargs):
        self.calls.append(("leases", principal, kwargs))
        return {"items": [], "authoritative_source": "hub"}


class _ExplicitPermissionTestPort(SemanticMediaPermissionService):
    """Route-focused test port; production composition uses the persisted service."""

    def __init__(self) -> None:
        pass

    def require_grant_id(self, grant_id: str, **context):
        assert grant_id == "test-purpose-bound-grant"
        assert context["tenant_id"] == "owner-a"
        assert context["subject_id"] == "owner-a"
        assert context["scope_id"] == "session-a"
        assert context["epoch"] == 1
        return object()


def setup(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SEMANTIC_COMPUTE_SECURITY_CONFIRMED=True)
    app.register_blueprint(routes.semantic_media_contracts_bp)
    app.extensions["semantic_media_permission_service"] = _ExplicitPermissionTestPort()
    fake = FakeService()
    monkeypatch.setattr(routes, "get_semantic_contract_service", lambda: fake)
    monkeypatch.setattr(routes, "get_semantic_compute_execution_service", lambda: fake.execution)
    monkeypatch.setattr(routes, "_establish_membership", lambda principal, body: None)
    token = issue_user_access_token(username="owner-a", role="admin")
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    client.environ_base["HTTP_X_SEMANTIC_CAPABILITY_GRANT"] = "test-purpose-bound-grant"
    return client, fake


def create_body():
    return {
        "session_id": "session-a", "epoch": 1, "policy_version": "policy-v1",
        "consent_version": 1,
        "proposal": {"profile": "balanced", "delay_ms": 5_000},
    }


def test_capability_composition_is_required_and_bootstrap_installs_it(monkeypatch) -> None:
    token = issue_user_access_token(username="owner-a", role="admin")

    missing_app = Flask(__name__)
    missing_app.config.update(TESTING=True, SEMANTIC_COMPUTE_SECURITY_CONFIRMED=True)
    missing_app.register_blueprint(routes.semantic_media_contracts_bp)
    monkeypatch.setattr(routes, "_establish_membership", lambda principal, body: None)
    missing_client = missing_app.test_client()
    missing_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    unavailable = missing_client.post(
        "/v1/semantic-media/contracts", json=create_body(),
        headers={"Idempotency-Key": "missing-composition-1"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json["error"]["code"] == "capability_service_unavailable"

    bootstrapped = Flask(__name__)
    bootstrapped.secret_key = "semantic-compute-bootstrap-test-secret"
    bootstrapped.config.update(TESTING=True)
    initialize_semantic_media_services(bootstrapped)
    bootstrapped.register_blueprint(routes.semantic_media_contracts_bp)
    client = bootstrapped.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    denied = client.post(
        "/v1/semantic-media/contracts", json=create_body(),
        headers={"Idempotency-Key": "bootstrapped-missing-grant-1"},
    )
    assert denied.status_code == 403
    assert denied.json["error"]["code"] == "capability_grant_required"


def test_create_offer_supports_legacy_optional_fields_and_idempotent_replay(monkeypatch) -> None:
    client, fake = setup(monkeypatch)
    response = client.post(
        "/v1/semantic-media/contracts", json=create_body(),
        headers={"Idempotency-Key": "create-key-0001"},
    )
    assert response.status_code == 201
    assert fake.calls[0][2]["advertisements"] == []
    fake.replayed = True
    replay = client.post(
        "/v1/semantic-media/contracts/offers", json=create_body(),
        headers={"Idempotency-Key": "create-key-0001"},
    )
    assert replay.status_code == 201
    assert replay.json["contract"]["idempotent_replay"] is True


def test_unknown_fields_missing_idempotency_and_request_size_fail_closed(monkeypatch) -> None:
    client, _ = setup(monkeypatch)
    unknown = client.post(
        "/v1/semantic-media/contracts", json={**create_body(), "scheduler_winner": "browser"},
        headers={"Idempotency-Key": "create-key-0001"},
    )
    assert unknown.status_code == 400 and unknown.json["error"]["code"] == "unknown_field"
    missing = client.post("/v1/semantic-media/contracts", json=create_body())
    assert missing.status_code == 400 and missing.json["error"]["code"] == "idempotency_key_invalid"
    oversized = client.post(
        "/v1/semantic-media/contracts", data=b"{" + b" " * (129 * 1024) + b"}",
        content_type="application/json", headers={"Idempotency-Key": "create-key-0001"},
    )
    assert oversized.status_code == 413


def test_mutations_require_revision_and_propagate_stale_and_revoke_race(monkeypatch) -> None:
    client, fake = setup(monkeypatch)
    body = {"session_id": "session-a", "epoch": 1, "consent_version": 1}
    missing = client.post(
        "/v1/semantic-media/contracts/contract-a/revoke", json=body,
        headers={"Idempotency-Key": "revoke-key-0001"},
    )
    assert missing.status_code == 428
    response = client.post(
        "/v1/semantic-media/contracts/contract-a/revoke", json=body,
        headers={"Idempotency-Key": "revoke-key-0001", "If-Match": '"2"'},
    )
    assert response.status_code == 200
    assert fake.calls[-1][2]["expected_revision"] == 2
    fake.error = SemanticContractServiceError("stale_revision", status_code=412)
    stale = client.post(
        "/v1/semantic-media/contracts/contract-a/revoke", json=body,
        headers={"Idempotency-Key": "revoke-key-0002", "If-Match": '"2"'},
    )
    assert stale.status_code == 412 and stale.json["error"]["code"] == "stale_revision"


def test_detail_list_pagination_and_foreign_resource_not_found(monkeypatch) -> None:
    client, fake = setup(monkeypatch)
    detail = client.get("/v1/semantic-media/contracts/contract-a?session_id=session-a&epoch=1")
    assert detail.status_code == 200
    page = client.get("/v1/semantic-media/contracts?session_id=session-a&epoch=1&limit=100&offset=2")
    assert page.status_code == 200 and fake.calls[-1][2]["limit"] == 100
    assert client.get("/v1/semantic-media/contracts?session_id=session-a&epoch=1&limit=101").status_code == 400
    fake.error = SemanticContractServiceError("contract_not_found", status_code=404)
    hidden = client.get("/v1/semantic-media/contracts/foreign?session_id=session-a&epoch=1")
    assert hidden.status_code == 404


def test_candidate_registration_advertisement_and_claims_are_authenticated_hub_apis(monkeypatch) -> None:
    client, fake = setup(monkeypatch)
    key = client.post("/v1/semantic-media/compute/candidate-keys", json={
        "session_id": "session-a",
        "epoch": 1,
        "key_id": "cap-" + "a" * 32,
        "public_key_b64": "A" * 43 + "=",
        "expires_at_ms": int(time.time() * 1000) + 60_000,
    })
    assert key.status_code == 201
    advertisement = {
        "schema": "ananta.semantic-capability-advertisement.v1",
        "advertisement_id": "capability-a",
        "session_id": "session-a",
        "epoch": 1,
        "sender_id": "owner-a",
        "algorithms": ["heuristic-visual-v1"],
        "roles": ["executor"],
        "task_types": ["visual_extract"],
        "resource_profile": {
            "cpu": "medium", "memory": "medium", "gpu": "unknown",
            "codec": "unknown", "battery": "mains", "network": "normal",
        },
        "measurements_expires_at_ms": int(time.time() * 1000) + 50_000,
        "expires_at_ms": int(time.time() * 1000) + 50_000,
        "max_delay_ms": 5_000,
        "max_artifact_bytes": 65_536,
        "signature": {"algorithm": "ed25519", "key_id": "cap-" + "a" * 32, "value": "A" * 88},
    }
    advertised = client.post("/v1/semantic-media/compute/capabilities", json=advertisement)
    claims = client.get("/v1/semantic-media/compute/capabilities?session_id=session-a&epoch=1")
    assert advertised.status_code == 201 and advertised.json["capability"]["authoritative"] is False
    assert claims.status_code == 200 and claims.json["capabilities"]["authoritative"] is False
    assert [item[0] for item in fake.execution.calls] == ["key", "advertise", "claims"]


def test_schedule_and_lease_reads_require_revision_and_return_only_hub_projection(monkeypatch) -> None:
    client, fake = setup(monkeypatch)
    body = {
        "session_id": "session-a",
        "epoch": 1,
        "expected_revision": 2,
        "task_type": "visual_extract",
        "audience": "owner-a",
        "sequence_start": 0,
        "sequence_end": 0,
        "resource_budget": {"cpu_ms": 1_000, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        "deadline_epoch_ms": int(time.time() * 1000) + 5_000,
        "validator_count": 0,
        "hot_standby": False,
    }
    scheduled = client.post(
        "/v1/semantic-media/contracts/contract-a/schedule",
        json=body,
        headers={"Idempotency-Key": "schedule-key-0001", "If-Match": '"2"'},
    )
    leases = client.get("/v1/semantic-media/contracts/contract-a/leases?session_id=session-a&epoch=1")
    assert scheduled.status_code == 201 and scheduled.json["schedule"]["authoritative_source"] == "hub"
    assert leases.status_code == 200 and leases.json["leases"]["authoritative_source"] == "hub"
    assert fake.execution.calls[0][2]["expected_revision"] == 2


def test_explanations_are_redacted_and_suggestions_cannot_mutate_authority(monkeypatch) -> None:
    client, _fake = setup(monkeypatch)
    explanation = client.get(
        "/v1/semantic-media/contracts/contract-a/explanation"
        "?session_id=session-a&epoch=1&expected_revision=1&expected_digest=" + "a" * 64
    )
    assert explanation.status_code == 200
    assert explanation.json["explanation"]["authoritative_source"] == "hub"
    assert "media" not in explanation.json["explanation"]
    suggestion_body = {
        "session_id": "session-a", "epoch": 1, "expected_revision": 1,
        "expected_digest": "a" * 64,
        "suggestion": {"profile": "conservative", "rationale": "Reduce load"},
    }
    suggestion = client.post("/v1/semantic-media/contracts/contract-a/suggestions", json=suggestion_body)
    assert suggestion.status_code == 200
    assert suggestion.json["suggestion"]["authoritative"] is False
    forbidden = client.post(
        "/v1/semantic-media/contracts/contract-a/suggestions",
        json={**suggestion_body, "suggestion": {"lease": "grant"}},
    )
    assert forbidden.status_code == 400
    assert forbidden.json["error"]["code"] == "suggestion_authority_field_forbidden"
