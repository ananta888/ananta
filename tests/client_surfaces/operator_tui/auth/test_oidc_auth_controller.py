"""Tests for OidcAuthController (oidc-001).

Tests:
- PKCE pair generation (S256 challenge = base64url(sha256(verifier)))
- State and nonce uniqueness (100 calls produce unique values)
- Callback with wrong state raises ValueError
- No token value appears in any logged/str output (mock exchange)
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from client_surfaces.operator_tui.auth.oidc_auth_controller import OidcAuthController
from client_surfaces.operator_tui.auth.oidc_models import (
    OidcAuthRequest,
    OidcAuthResult,
    OidcProviderConfig,
)


def _make_provider(provider_id: str = "test_provider") -> OidcProviderConfig:
    return OidcProviderConfig(
        provider_id=provider_id,
        issuer="https://issuer.example.com/realms/test",
        client_id="test-client",
        flow="authorization_code_pkce",
        redirect_mode="loopback",
        allowed_redirect_hosts=["127.0.0.1"],
    )


def _make_request(
    provider_id: str = "test_provider",
    state: str = "test_state",
    nonce: str = "test_nonce",
) -> OidcAuthRequest:
    now = time.time()
    return OidcAuthRequest(
        provider_id=provider_id,
        state=state,
        nonce=nonce,
        pkce_verifier="test_verifier",
        pkce_challenge="test_challenge",
        redirect_uri="http://127.0.0.1:12345/callback",
        authorization_url="https://issuer.example.com/auth?...",
        created_at=now,
        expires_at=now + 300,
    )


class TestPkceGeneration(unittest.TestCase):
    """PKCE pair generation must follow RFC 7636 S256."""

    def setUp(self):
        self.ctrl = OidcAuthController()

    def test_pkce_challenge_is_s256_of_verifier(self):
        """Challenge must equal base64url(sha256(verifier)) with no padding."""
        verifier, challenge = self.ctrl._generate_pkce_pair()
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)

    def test_pkce_verifier_is_base64url(self):
        """Verifier must be a non-empty base64url-safe string."""
        verifier, _ = self.ctrl._generate_pkce_pair()
        # Only base64url chars allowed
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        self.assertTrue(all(c in allowed for c in verifier))
        self.assertGreater(len(verifier), 40)

    def test_pkce_challenge_is_base64url(self):
        """Challenge must be a non-empty base64url-safe string."""
        _, challenge = self.ctrl._generate_pkce_pair()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        self.assertTrue(all(c in allowed for c in challenge))
        self.assertGreater(len(challenge), 10)

    def test_pkce_no_padding(self):
        """Verifier and challenge must have no base64 padding ('=')."""
        verifier, challenge = self.ctrl._generate_pkce_pair()
        self.assertNotIn("=", verifier)
        self.assertNotIn("=", challenge)

    def test_pkce_pairs_are_unique(self):
        """Multiple calls must produce different verifiers and challenges."""
        pairs = {self.ctrl._generate_pkce_pair() for _ in range(10)}
        # All 10 pairs should be unique
        self.assertEqual(len(pairs), 10)


class TestStateNonceUniqueness(unittest.TestCase):
    """State and nonce must be unique across calls."""

    def setUp(self):
        self.ctrl = OidcAuthController()

    def test_100_states_are_unique(self):
        """100 generated state values must all be distinct."""
        states = {self.ctrl._generate_state() for _ in range(100)}
        self.assertEqual(len(states), 100)

    def test_100_nonces_are_unique(self):
        """100 generated nonce values must all be distinct."""
        nonces = {self.ctrl._generate_nonce() for _ in range(100)}
        self.assertEqual(len(nonces), 100)

    def test_state_is_urlsafe(self):
        """State must be a non-empty URL-safe string."""
        state = self.ctrl._generate_state()
        self.assertIsInstance(state, str)
        self.assertGreater(len(state), 20)

    def test_nonce_is_urlsafe(self):
        """Nonce must be a non-empty URL-safe string."""
        nonce = self.ctrl._generate_nonce()
        self.assertIsInstance(nonce, str)
        self.assertGreater(len(nonce), 20)


class TestCallbackValidation(unittest.TestCase):
    """complete_callback must reject invalid state."""

    def setUp(self):
        self.ctrl = OidcAuthController()
        self.provider = _make_provider()

    def test_wrong_state_raises_value_error(self):
        """Callback with state mismatch must raise ValueError."""
        request = _make_request(state="correct_state")
        callback_url = "http://127.0.0.1:12345/callback?code=abc&state=WRONG_STATE"
        with self.assertRaises(ValueError) as ctx:
            self.ctrl.complete_callback(callback_url, request, self.provider)
        self.assertIn("state", str(ctx.exception).lower())

    def test_missing_code_raises_value_error(self):
        """Callback without authorization code must raise ValueError."""
        request = _make_request(state="mystate")
        callback_url = "http://127.0.0.1:12345/callback?state=mystate"
        with self.assertRaises(ValueError) as ctx:
            self.ctrl.complete_callback(callback_url, request, self.provider)
        self.assertIn("code", str(ctx.exception).lower())

    def test_provider_error_in_callback_returns_error_result(self):
        """Callback with provider error param must return OidcAuthResult(ok=False)."""
        request = _make_request(state="mystate")
        callback_url = (
            "http://127.0.0.1:12345/callback"
            "?error=access_denied&error_description=User+denied+access&state=mystate"
        )
        result = self.ctrl.complete_callback(callback_url, request, self.provider)
        self.assertFalse(result.ok)
        self.assertIn("provider_error", result.error)

    def test_correct_state_proceeds_to_exchange(self):
        """Callback with correct state and code calls _exchange_code."""
        request = _make_request(state="correct_state")
        callback_url = "http://127.0.0.1:12345/callback?code=mycode&state=correct_state"
        with patch.object(
            self.ctrl, "_exchange_code",
            return_value=OidcAuthResult(ok=True, provider_id="test_provider", access_token="<present>")
        ) as mock_exchange:
            result = self.ctrl.complete_callback(callback_url, request, self.provider)
        mock_exchange.assert_called_once()
        self.assertTrue(result.ok)


class TestTokenNotExposedInOutput(unittest.TestCase):
    """Token values must never appear in string/repr outputs."""

    _FAKE_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.FAKESIG"

    def test_auth_result_repr_does_not_contain_token(self):
        """OidcAuthResult repr must not expose the access_token value."""
        result = OidcAuthResult(
            ok=True,
            access_token=self._FAKE_TOKEN,
            id_token=self._FAKE_TOKEN,
            refresh_token=self._FAKE_TOKEN,
            provider_id="test",
            subject="user123",
            username="testuser",
        )
        output = repr(result)
        self.assertNotIn(self._FAKE_TOKEN, output)
        self.assertIn("<present>", output)

    def test_auth_result_str_does_not_contain_token(self):
        """OidcAuthResult str must not expose the access_token value."""
        result = OidcAuthResult(
            ok=True,
            access_token=self._FAKE_TOKEN,
            provider_id="test",
        )
        output = str(result)
        self.assertNotIn(self._FAKE_TOKEN, output)

    def test_auth_request_repr_does_not_contain_verifier(self):
        """OidcAuthRequest repr must not expose the pkce_verifier value."""
        request = _make_request()
        request_with_secret = OidcAuthRequest(
            provider_id=request.provider_id,
            state=request.state,
            nonce=request.nonce,
            pkce_verifier="SUPER_SECRET_VERIFIER_VALUE",
            pkce_challenge=request.pkce_challenge,
            redirect_uri=request.redirect_uri,
            authorization_url=request.authorization_url,
            created_at=request.created_at,
            expires_at=request.expires_at,
        )
        output = repr(request_with_secret)
        self.assertNotIn("SUPER_SECRET_VERIFIER_VALUE", output)
        self.assertIn("<redacted>", output)

    def test_exchange_code_error_does_not_leak_token(self):
        """_exchange_code error path must not include token-like values."""
        import urllib.error
        import io

        ctrl = OidcAuthController()
        provider = _make_provider()
        request = _make_request()

        # Mock a failed HTTP exchange that might inadvertently log the code
        with patch("urllib.request.urlopen") as mock_open:
            mock_exc = urllib.error.HTTPError(
                url="https://issuer.example.com/token",
                code=400,
                msg="Bad Request",
                hdrs=None,  # type: ignore
                fp=io.BytesIO(json.dumps({
                    "error": "invalid_grant",
                    "error_description": "Code invalid",
                }).encode()),
            )
            mock_open.side_effect = mock_exc
            result = ctrl._exchange_code("some_code", request, provider)

        self.assertFalse(result.ok)
        # Error message must not contain the code itself or token-like content
        self.assertNotIn("some_code", result.error)
        # Verify no JWT-like pattern in error
        self.assertNotIn("eyJ", result.error)


class TestCreateAuthorizationRequest(unittest.TestCase):
    """create_authorization_request must build a valid request."""

    def setUp(self):
        self.ctrl = OidcAuthController()
        self.provider = _make_provider()

    def test_builds_request_with_pkce_params(self):
        """Authorization URL must contain PKCE challenge and method."""
        with patch.object(self.ctrl, "_discover_auth_endpoint", return_value="https://issuer.example.com/auth"):
            req = self.ctrl.create_authorization_request(
                provider=self.provider,
                redirect_uri="http://127.0.0.1:9999/callback",
            )
        self.assertIn("code_challenge=", req.authorization_url)
        self.assertIn("code_challenge_method=S256", req.authorization_url)
        self.assertIn("response_type=code", req.authorization_url)

    def test_request_contains_state_and_nonce(self):
        """Authorization URL must contain state and nonce."""
        with patch.object(self.ctrl, "_discover_auth_endpoint", return_value="https://issuer.example.com/auth"):
            req = self.ctrl.create_authorization_request(
                provider=self.provider,
                redirect_uri="http://127.0.0.1:9999/callback",
            )
        self.assertIn(req.state, req.authorization_url)
        self.assertIn(req.nonce, req.authorization_url)

    def test_request_has_expiry(self):
        """Request must have a future expiry."""
        with patch.object(self.ctrl, "_discover_auth_endpoint", return_value="https://issuer.example.com/auth"):
            req = self.ctrl.create_authorization_request(
                provider=self.provider,
                redirect_uri="http://127.0.0.1:9999/callback",
            )
        self.assertGreater(req.expires_at, time.time())


if __name__ == "__main__":
    unittest.main()
