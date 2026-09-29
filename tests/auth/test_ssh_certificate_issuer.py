from __future__ import annotations

import time

from agent.services import ssh_certificate_issuer as sci


def _claims(*, issuer: str, audience: str, nonce: str = "nonce-1") -> dict:
    now = int(time.time())
    return {
        "iss": issuer,
        "aud": audience,
        "sub": "user-1",
        "exp": now + 300,
        "iat": now - 5,
        "nonce": nonce,
        "groups": ["ananta-terminal-worker"],
    }


def test_validate_claims_rejects_expired_token():
    now = int(time.time())
    ok, reason = sci._validate_oidc_token_claims(
        {
            "iss": "i",
            "aud": "a",
            "sub": "u",
            "exp": now - 1,
            "iat": now - 10,
        },
        expected_issuer="i",
        expected_audience="a",
    )
    assert ok is False
    assert reason == "ssh_cert_issuer_token_expired"


def test_issue_denied_when_native_ssh_disabled(monkeypatch):
    monkeypatch.setattr(sci.settings, "native_ssh_enabled", False)
    issuer = sci.SshCertificateIssuer()
    result = issuer.issue(id_token_claims={}, target_type="worker", target_id="alpha")
    assert result.allowed is False
    assert result.reason_code == "ssh_cert_issuer_native_ssh_disabled"


def test_issue_fails_closed_when_policy_unavailable(monkeypatch):
    monkeypatch.setattr(sci.settings, "native_ssh_enabled", True)
    monkeypatch.setattr(sci.settings, "terminal_oidc_issuer", "http://issuer")
    monkeypatch.setattr(sci.settings, "terminal_oidc_client_id", "ananta-hub")
    monkeypatch.setattr(sci.settings, "terminal_oidc_audience", "ananta-hub")

    class _Policy:
        def evaluate(self, **kwargs):
            raise RuntimeError("policy-down")

    monkeypatch.setattr(sci, "get_terminal_policy_service", lambda: _Policy())

    issuer = sci.SshCertificateIssuer()
    result = issuer.issue(
        id_token_claims=_claims(issuer="http://issuer", audience="ananta-hub", nonce="nonce-policy"),
        target_type="worker",
        target_id="alpha",
    )
    assert result.allowed is False
    assert result.reason_code == "ssh_cert_issuer_policy_unavailable"
