"""Unit tests for the OIDC validator (Welle 3).

Strategy: stand up a local HTTP server that serves a fake JWKS,
point a PyJWKClient at it, sign tokens with a local RSA key, and
verify validate_oidc_token returns the expected claims or None.

This exercises the real PyJWT+JWKS code path without needing a live
Keycloak instance.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agent.services import oidc_settings, oidc_validator


# --- helpers --------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keys():
    """Generate a stable RSA keypair for the module."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # kid is arbitrary; we use a stable string so we can construct the JWKS by hand.
    return {"kid": "test-kid-1", "private_pem": private_pem, "public_pem": public_pem}


def _jwk_for(public_pem: bytes, kid: str) -> dict[str, Any]:
    """Convert a PEM RSA public key into a minimal JWK (kty=RSA, use=sig)."""
    pub = serialization.load_pem_public_key(public_pem)
    numbers = pub.public_numbers()  # type: ignore[union-attr]
    import base64

    def _b64u(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


@pytest.fixture(scope="module")
def jwks_server(rsa_keys):
    """Tiny HTTP server that returns the test JWKS at /jwks."""
    jwks_payload = json.dumps({"keys": [_jwk_for(rsa_keys["public_pem"], rsa_keys["kid"])]}).encode("utf-8")

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/jwks":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(jwks_payload)))
                self.end_headers()
                self.wfile.write(jwks_payload)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args, **_kwargs):
            return  # silence stderr

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/jwks"
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def enabled_oidc_config(jwks_server, rsa_keys):
    """Configure OIDC for the duration of one test."""
    fields = {
        "oidc_enabled": True,
        "oidc_issuer_url": "https://issuer.test/realm/x",
        "oidc_jwks_url": jwks_server,
        "oidc_audience": "ananta-hub",
        "oidc_client_id": "ananta-frontend",
        "oidc_jwks_cache_seconds": 60,
        "oidc_allowed_algorithms": "RS256",
    }
    saved = {}
    for k, v in fields.items():
        saved[k] = getattr(oidc_settings.settings, k)
        setattr(oidc_settings.settings, k, v)
    oidc_validator._clear_jwks_cache()
    yield
    for k, v in saved.items():
        setattr(oidc_settings.settings, k, v)
    oidc_validator._clear_jwks_cache()


def _make_token(rsa_keys, *, claims: dict[str, Any], audience: str | None = None, issuer: str | None = None) -> str:
    payload = dict(claims)
    if audience is not None:
        payload["aud"] = audience
    if issuer is not None:
        payload["iss"] = issuer
    return jwt.encode(
        payload,
        rsa_keys["private_pem"],
        algorithm="RS256",
        headers={"kid": rsa_keys["kid"]},
    )


# --- tests ----------------------------------------------------------------

def test_disabled_returns_none(enabled_oidc_config, rsa_keys):
    oidc_settings.settings.oidc_enabled = False
    assert oidc_settings.oidc_is_configured() is False
    token = _make_token(
        rsa_keys,
        claims={"sub": "u1", "iat": int(time.time()), "exp": int(time.time()) + 60},
        audience="ananta-hub",
        issuer="https://issuer.test/realm/x",
    )
    assert oidc_validator.validate_oidc_token(token) is None


def test_valid_token_returns_claims(enabled_oidc_config, rsa_keys):
    now = int(time.time())
    token = _make_token(
        rsa_keys,
        claims={"sub": "user-42", "iat": now, "exp": now + 60},
        audience="ananta-hub",
        issuer="https://issuer.test/realm/x",
    )
    claims = oidc_validator.validate_oidc_token(token)
    assert claims is not None
    assert claims["sub"] == "user-42"
    assert claims["aud"] == "ananta-hub"
    assert claims["iss"] == "https://issuer.test/realm/x"


def test_wrong_audience_rejected(enabled_oidc_config, rsa_keys):
    now = int(time.time())
    token = _make_token(
        rsa_keys,
        claims={"sub": "user-42", "iat": now, "exp": now + 60},
        audience="someone-else",
        issuer="https://issuer.test/realm/x",
    )
    assert oidc_validator.validate_oidc_token(token) is None


def test_wrong_issuer_rejected(enabled_oidc_config, rsa_keys):
    now = int(time.time())
    token = _make_token(
        rsa_keys,
        claims={"sub": "user-42", "iat": now, "exp": now + 60},
        audience="ananta-hub",
        issuer="https://attacker.example/realm/x",
    )
    assert oidc_validator.validate_oidc_token(token) is None


def test_expired_token_rejected(enabled_oidc_config, rsa_keys):
    past = int(time.time()) - 3600
    token = _make_token(
        rsa_keys,
        claims={"sub": "user-42", "iat": past - 60, "exp": past + 1},
        audience="ananta-hub",
        issuer="https://issuer.test/realm/x",
    )
    assert oidc_validator.validate_oidc_token(token) is None


def test_missing_required_claim_rejected(enabled_oidc_config, rsa_keys):
    """`sub` is required by OIDC — token without sub must be rejected."""
    now = int(time.time())
    token = _make_token(
        rsa_keys,
        claims={"iat": now, "exp": now + 60},  # no sub!
        audience="ananta-hub",
        issuer="https://issuer.test/realm/x",
    )
    assert oidc_validator.validate_oidc_token(token) is None


def test_garbage_token_returns_none(enabled_oidc_config, rsa_keys):
    assert oidc_validator.validate_oidc_token("not-a-jwt") is None
    assert oidc_validator.validate_oidc_token("a.b.c") is None
    assert oidc_validator.validate_oidc_token("") is None


def test_partial_config_returns_none_not_crash(jwks_server, rsa_keys):
    """Default-deny: OIDC enabled but missing fields → validate_oidc_token returns None
    instead of silently falling back to the secret-key path.
    """
    saved = {}
    for k in [
        "oidc_enabled",
        "oidc_issuer_url",
        "oidc_jwks_url",
        "oidc_audience",
        "oidc_client_id",
    ]:
        saved[k] = getattr(oidc_settings.settings, k)
    try:
        oidc_settings.settings.oidc_enabled = True
        oidc_settings.settings.oidc_issuer_url = "https://issuer.test"
        oidc_settings.settings.oidc_jwks_url = jwks_server
        oidc_settings.settings.oidc_audience = ""  # missing!
        oidc_settings.settings.oidc_client_id = "x"
        cfg = oidc_settings.get_oidc_config()
        assert cfg.enabled is True
        token = _make_token(
            rsa_keys,
            claims={"sub": "u", "iat": int(time.time()), "exp": int(time.time()) + 60},
            audience="ananta-hub",
            issuer="https://issuer.test",
        )
        assert oidc_validator.validate_oidc_token(token, cfg) is None
    finally:
        for k, v in saved.items():
            setattr(oidc_settings.settings, k, v)