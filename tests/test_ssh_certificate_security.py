from __future__ import annotations

import time

from agent.services.ssh_certificate_issuer import _KNOWN_NONCES, _check_nonce, _validate_oidc_token_claims
from agent.services.ssh_principal_mapper import SshPrincipalMapper


def test_nonce_replay_rejected():
    _KNOWN_NONCES.clear()
    assert _check_nonce("n-1") is True
    assert _check_nonce("n-1") is False


def test_unknown_worker_group_rejected_by_principal_mapper():
    mapper = SshPrincipalMapper()
    result = mapper.map(
        user_ctx={"sub": "u1", "groups": ["unknown-group"], "terminal_permissions": []},
        target_type="worker",
    )
    assert result.allowed is False
    assert result.reason_code == "ssh_principal_mapper_no_worker_permission"


def test_expired_claims_are_rejected():
    now = int(time.time())
    ok, reason = _validate_oidc_token_claims(
        {
            "iss": "http://issuer",
            "aud": "ananta-hub",
            "sub": "u1",
            "exp": now - 1,
            "iat": now - 20,
        },
        expected_issuer="http://issuer",
        expected_audience="ananta-hub",
    )
    assert ok is False
    assert reason == "ssh_cert_issuer_token_expired"
