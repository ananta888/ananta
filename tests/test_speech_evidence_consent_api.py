from __future__ import annotations

from flask import Flask

from agent.routes import speech_evidence_consents as routes
from agent.services.user_session_tokens import issue_user_access_token
from ananta_contracts.speech_evidence_governance import SpeechEvidenceConsent
from tests.speech_evidence_support import consent_payload


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        self.current = SpeechEvidenceConsent.from_mapping(consent_payload("api"))

    def grant(self, principal, raw):
        self.calls.append(("grant", principal, dict(raw)))
        return self.current

    def get(self, principal, consent_id):
        self.calls.append(("get", principal, {"consent_id": consent_id}))
        return self.current

    def reduce(self, principal, raw, **kwargs):
        self.calls.append(("reduce", principal, {**kwargs, "raw": dict(raw)}))
        return self.current

    def renew(self, principal, raw, **kwargs):
        self.calls.append(("renew", principal, {**kwargs, "raw": dict(raw)}))
        return self.current

    def revoke(self, principal, consent_id, **kwargs):
        self.calls.append(("revoke", principal, {**kwargs, "consent_id": consent_id}))
        return self.current


class FakeRevocationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def revoke_consent(self, principal, consent_id, **kwargs):
        self.calls.append({"principal": principal, "consent_id": consent_id, **kwargs})

        class Result:
            @staticmethod
            def public_dict():
                return {
                    "consent_id": consent_id,
                    "scanned_count": 2,
                    "revoked_count": 2,
                    "replayed_count": 0,
                    "unresolved_count": 0,
                    "truncated": False,
                    "reason_codes": [],
                }

        return Result()

def _setup(monkeypatch):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(routes.speech_evidence_consents_bp)
    fake = FakeService()
    revocation = FakeRevocationService()
    monkeypatch.setattr(routes, "get_speech_evidence_consent_service", lambda: fake)
    monkeypatch.setattr(routes, "get_speech_evidence_revocation_service", lambda: revocation)
    token = issue_user_access_token(username="owner-api", role="admin")
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client, fake, revocation


def test_grant_and_get_are_authenticated_and_closed(monkeypatch) -> None:
    client, fake, _revocation = _setup(monkeypatch)
    granted = client.post(
        "/v1/voice/speech-evidence-consents",
        json=consent_payload("api"),
        headers={"Idempotency-Key": "speech-consent-create"},
    )
    assert granted.status_code == 201
    assert granted.json["data"]["consent"]["grants"]["training"] is False
    assert fake.calls[-1][0] == "grant"
    fetched = client.get(
        "/v1/voice/speech-evidence-consents/consent-api"
    )
    assert fetched.status_code == 200 and fake.calls[-1][0] == "get"


def test_mutations_require_if_match_idempotency_and_exact_shape(monkeypatch) -> None:
    client, fake, revocation = _setup(monkeypatch)
    consent = consent_payload("api")
    missing = client.post(
        "/v1/voice/speech-evidence-consents/consent-api/reduce",
        json={"consent": consent},
        headers={"Idempotency-Key": "speech-consent-reduce"},
    )
    assert missing.status_code == 428
    reduced = client.post(
        "/v1/voice/speech-evidence-consents/consent-api/reduce",
        json={"consent": consent},
        headers={
            "Idempotency-Key": "speech-consent-reduce",
            "If-Match": 'W/"1"',
        },
    )
    assert reduced.status_code == 200
    assert fake.calls[-1][0] == "reduce"
    assert fake.calls[-1][2]["expected_version"] == 1
    invalid = client.post(
        "/v1/voice/speech-evidence-consents/consent-api/revoke",
        json={"ai_snake_grant": True},
        headers={
            "Idempotency-Key": "speech-consent-revoke",
            "If-Match": '"1"',
        },
    )
    assert invalid.status_code == 422
    revoked = client.post(
        "/v1/voice/speech-evidence-consents/consent-api/revoke",
        json={},
        headers={
            "Idempotency-Key": "speech-consent-revoke",
            "If-Match": '"1"',
        },
    )
    assert revoked.status_code == 200
    assert revoked.json["data"]["revocation"]["revoked_count"] == 2
    assert revocation.calls[-1]["expected_consent_version"] == fake.current.consent_version


def test_path_payload_mismatch_and_oversize_fail_closed(monkeypatch) -> None:
    client, _fake, _revocation = _setup(monkeypatch)
    mismatch = client.post(
        "/v1/voice/speech-evidence-consents/other/reduce",
        json={"consent": consent_payload("api")},
        headers={
            "Idempotency-Key": "speech-consent-reduce",
            "If-Match": '"1"',
        },
    )
    assert mismatch.status_code == 409
    oversized = client.post(
        "/v1/voice/speech-evidence-consents",
        data=b"{" + b" " * (65 * 1024) + b"}",
        content_type="application/json",
        headers={"Idempotency-Key": "speech-consent-create"},
    )
    assert oversized.status_code == 413
