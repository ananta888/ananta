from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock

from client_surfaces.operator_tui.auth.oidc_browser_session_controller import (
    OidcBrowserSessionController,
    _callback_url_from_payload,
)
from client_surfaces.operator_tui.auth.oidc_models import OidcAuthRequest, OidcAuthResult, OidcProviderConfig


def _provider() -> OidcProviderConfig:
    return OidcProviderConfig(
        provider_id="keycloak",
        issuer="https://issuer.example.com/realms/test",
        client_id="client",
        flow="authorization_code_pkce",
        redirect_mode="loopback",
        allowed_redirect_hosts=["127.0.0.1"],
    )


def _request() -> OidcAuthRequest:
    now = time.time()
    return OidcAuthRequest(
        provider_id="keycloak",
        state="state-1",
        nonce="nonce-1",
        pkce_verifier="verifier",
        pkce_challenge="challenge",
        redirect_uri="http://127.0.0.1:10000/callback",
        authorization_url="https://issuer.example.com/auth",
        created_at=now,
        expires_at=now + 300,
    )


class TestOidcBrowserSessionController(unittest.TestCase):
    def test_start_wires_loopback_auth_request_and_browser_profile(self):
        auth = MagicMock()
        callback = MagicMock()
        browser = MagicMock()
        callback.start.return_value = "http://127.0.0.1:10000/callback"
        auth.create_authorization_request.return_value = _request()
        browser.open_oidc_provider_session.return_value = object()

        ctrl = OidcBrowserSessionController(
            auth_controller=auth,
            callback_server=callback,
            browser_controller=browser,
        )
        session = ctrl.start_ananta_owned_callback(_provider(), cols=100, rows=30)

        self.assertEqual(session.status, "browser_active")
        callback.start.assert_called_once()
        auth.create_authorization_request.assert_called_once()
        browser.open_oidc_provider_session.assert_called_once_with(
            "https://issuer.example.com/auth",
            provider_id="keycloak",
            cols=100,
            rows=30,
            ephemeral_profile=True,
        )

    def test_complete_callback_uses_active_request(self):
        auth = MagicMock()
        callback = MagicMock()
        browser = MagicMock()
        callback.start.return_value = "http://127.0.0.1:10000/callback"
        auth.create_authorization_request.return_value = _request()
        auth.complete_callback.return_value = OidcAuthResult(ok=True, provider_id="keycloak", subject="sub")

        ctrl = OidcBrowserSessionController(
            auth_controller=auth,
            callback_server=callback,
            browser_controller=browser,
        )
        ctrl.start_ananta_owned_callback(_provider(), cols=100, rows=30)
        result = ctrl.complete_callback({"code": "code-1", "state": "state-1"})

        self.assertTrue(result.ok)
        callback_url = auth.complete_callback.call_args.args[0]
        self.assertIn("code=code-1", callback_url)
        self.assertIn("state=state-1", callback_url)

    def test_callback_payload_error_url(self):
        url = _callback_url_from_payload(
            "http://127.0.0.1:10000/callback",
            {"error": "access_denied", "error_description": "no", "state": "state-1"},
        )
        self.assertIn("error=access_denied", url)
        self.assertIn("state=state-1", url)


if __name__ == "__main__":
    unittest.main()
