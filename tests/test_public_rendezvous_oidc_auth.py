"""Focused security tests for the standalone public rendezvous OIDC verifier."""

from __future__ import annotations

import base64
import importlib
import sys
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture()
def public_oidc_auth(monkeypatch):
    service_dir = Path(__file__).resolve().parents[1] / "public-rendezvous" / "rendezvous"
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv(
        "RENDEZVOUS_SECURITY_SIGNING_SECRET",
        "test-only-public-rendezvous-signing-secret-32-bytes",
    )
    for module_name in ("config", "oidc_auth"):
        sys.modules.pop(module_name, None)
    module = importlib.import_module("oidc_auth")
    module._jwks_cache.clear()
    module._jwks_fetched_at.clear()
    yield module
    module._jwks_cache.clear()
    module._jwks_fetched_at.clear()


@pytest.fixture(scope="module")
def rsa_signing_material() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def encode_integer(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    public_jwk = {
        "kty": "RSA",
        "kid": "public-rendezvous-test-key",
        "use": "sig",
        "alg": "RS256",
        "n": encode_integer(public_numbers.n),
        "e": encode_integer(public_numbers.e),
    }
    return private_key, public_jwk


def _token(private_key: Any, claims: dict[str, Any]) -> str:
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        claims,
        private_pem,
        algorithm="RS256",
        headers={"kid": "public-rendezvous-test-key"},
    )


@pytest.mark.parametrize("missing_claim", ["exp", "iss", "sub", "aud"])
def test_verify_bearer_token_requires_security_claims(
    public_oidc_auth,
    rsa_signing_material,
    monkeypatch,
    missing_claim,
):
    private_key, public_jwk = rsa_signing_material
    issuer = public_oidc_auth.cfg.OIDC_ISSUER
    claims = {
        "exp": int(time.time()) + 60,
        "iss": issuer,
        "sub": "stable-subject",
        "aud": public_oidc_auth.cfg.OIDC_AUDIENCE,
    }
    claims.pop(missing_claim)
    monkeypatch.setattr(public_oidc_auth, "_fetch_jwks", lambda _issuer: {"keys": [public_jwk]})

    with pytest.raises(ValueError, match="Invalid token"):
        public_oidc_auth.verify_bearer_token(f"Bearer {_token(private_key, claims)}")


def test_verify_bearer_token_accepts_complete_claim_set(
    public_oidc_auth,
    rsa_signing_material,
    monkeypatch,
):
    private_key, public_jwk = rsa_signing_material
    issuer = public_oidc_auth.cfg.OIDC_ISSUER
    claims = {
        "exp": int(time.time()) + 60,
        "iss": issuer,
        "sub": "stable-subject",
        "aud": public_oidc_auth.cfg.OIDC_AUDIENCE,
    }
    monkeypatch.setattr(public_oidc_auth, "_fetch_jwks", lambda _issuer: {"keys": [public_jwk]})

    context = public_oidc_auth.verify_bearer_token(f"Bearer {_token(private_key, claims)}")

    assert context.sub == "stable-subject"
    assert context.issuer == issuer


def test_jwks_refresh_failure_uses_only_bounded_stale_cache(public_oidc_auth, monkeypatch):
    issuer = public_oidc_auth.cfg.OIDC_ISSUER
    cached_jwks = {"keys": [{"kid": "cached"}]}
    public_oidc_auth._jwks_cache[issuer] = cached_jwks
    public_oidc_auth._jwks_fetched_at[issuer] = 100.0
    monkeypatch.setattr(public_oidc_auth.cfg, "OIDC_JWKS_TTL", 10)
    monkeypatch.setattr(public_oidc_auth.cfg, "OIDC_JWKS_MAX_AGE_SECONDS", 30)

    monotonic_now = iter((111.0, 111.0, 131.0, 131.0))
    monkeypatch.setattr(public_oidc_auth.time, "monotonic", lambda: next(monotonic_now))

    def failed_refresh(*_args, **_kwargs):
        raise OSError("identity provider unavailable")

    monkeypatch.setattr(public_oidc_auth.requests, "get", failed_refresh)

    assert public_oidc_auth._fetch_jwks(issuer) is cached_jwks
    with pytest.raises(ValueError, match="JWKS not available"):
        public_oidc_auth._fetch_jwks(issuer)


def test_jwks_hard_max_age_overrides_long_normal_ttl(public_oidc_auth, monkeypatch):
    issuer = public_oidc_auth.cfg.OIDC_ISSUER
    public_oidc_auth._jwks_cache[issuer] = {"keys": [{"kid": "cached"}]}
    public_oidc_auth._jwks_fetched_at[issuer] = 100.0
    monkeypatch.setattr(public_oidc_auth.cfg, "OIDC_JWKS_TTL", 3_600)
    monkeypatch.setattr(public_oidc_auth.cfg, "OIDC_JWKS_MAX_AGE_SECONDS", 30)
    monkeypatch.setattr(public_oidc_auth.time, "monotonic", lambda: 131.0)
    monkeypatch.setattr(
        public_oidc_auth.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refresh failed")),
    )

    with pytest.raises(ValueError, match="JWKS not available"):
        public_oidc_auth._fetch_jwks(issuer)
