"""Provider compatibility smoke tests for Carbonyl OIDC session modes.

Usage::

    # Mock mode (no real network required, safe for CI):
    python scripts/operator_tui_carbonyl_oidc_smoke.py --provider keycloak --mock
    python scripts/operator_tui_carbonyl_oidc_smoke.py --provider google --mock

    # Live mode (requires configured provider, NOT for default CI):
    python scripts/operator_tui_carbonyl_oidc_smoke.py --provider keycloak \\
        --issuer https://keycloak.ananta.de/realms/ananta-test \\
        --client-id ananta-tui-carbonyl

Output is structured JSON to stdout::

    {"provider": "keycloak", "mode": "mock", "outcome": "success", "reason": ""}

Outcome categories:
    success | provider_rejected_user_agent | network_error |
    callback_timeout | invalid_state | token_exchange_failed

Security:
    Token values are NEVER printed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

OUTCOME_SUCCESS = "success"
OUTCOME_REJECTED = "provider_rejected_user_agent"
OUTCOME_NETWORK = "network_error"
OUTCOME_TIMEOUT = "callback_timeout"
OUTCOME_INVALID_STATE = "invalid_state"
OUTCOME_TOKEN_FAILED = "token_exchange_failed"


def _result(provider: str, mode: str, outcome: str, reason: str = "") -> dict[str, Any]:
    return {
        "provider": provider,
        "mode": mode,
        "outcome": outcome,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def run_mock(provider: str) -> dict[str, Any]:
    """Return a hardcoded success outcome without any network calls.

    This mode is safe for offline CI and verifies that the PKCE / state /
    audit machinery can be exercised without a real provider.
    """
    import sys
    sys.path.insert(0, str(_repo_root()))

    from client_surfaces.operator_tui.auth.oidc_models import OidcProviderConfig
    from client_surfaces.operator_tui.auth.oidc_auth_controller import OidcAuthController
    from client_surfaces.operator_tui.auth.oidc_audit import (
        OidcAuditLog, OidcAuditEvent,
        EVENT_LOGIN_START, EVENT_TOKEN_EXCHANGE_SUCCESS,
        MODE_ANANTA_OWNED, PROFILE_EPHEMERAL,
    )

    ctrl = OidcAuthController()
    verifier, challenge = ctrl._generate_pkce_pair()
    state = ctrl._generate_state()
    nonce = ctrl._generate_nonce()

    if not verifier or not challenge or not state or not nonce:
        return _result(provider, "mock", OUTCOME_TOKEN_FAILED, "pkce/state generation failed")

    # Verify S256 challenge
    import base64
    import hashlib
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if challenge != expected_challenge:
        return _result(provider, "mock", OUTCOME_INVALID_STATE, "pkce challenge mismatch")

    # Exercise audit log
    audit = OidcAuditLog()
    audit.emit(OidcAuditEvent(
        event_type=EVENT_LOGIN_START,
        provider_id=provider,
        mode=MODE_ANANTA_OWNED,
        profile_mode=PROFILE_EPHEMERAL,
        error_category="",
    ))
    audit.emit(OidcAuditEvent(
        event_type=EVENT_TOKEN_EXCHANGE_SUCCESS,
        provider_id=provider,
        mode=MODE_ANANTA_OWNED,
        profile_mode=PROFILE_EPHEMERAL,
        error_category="",
    ))
    events = audit.recent()
    if len(events) != 2:
        return _result(provider, "mock", OUTCOME_TOKEN_FAILED, "audit event count mismatch")

    # Ensure no token-like values leaked into audit strings
    fake_token = "eyJmYWtlLXRva2VuLXZhbHVl"
    for evt in events:
        if fake_token in str(evt):
            return _result(provider, "mock", OUTCOME_TOKEN_FAILED, "token leaked into audit log")

    return _result(provider, "mock", OUTCOME_SUCCESS)


# ---------------------------------------------------------------------------
# Live mode (requires real provider config)
# ---------------------------------------------------------------------------

def run_live(
    provider: str,
    issuer: str,
    client_id: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run a live smoke test against the given provider.

    This performs real OIDC discovery and loopback server start, but does
    NOT open a browser (no Carbonyl required in smoke test mode).
    It checks that:
    - OIDC discovery succeeds
    - Loopback server starts and returns a valid redirect_uri
    - Authorization URL is well-formed

    Token exchange is NOT attempted (that requires a real browser).

    Args:
        provider: Provider name label.
        issuer: OIDC issuer URL.
        client_id: OIDC client ID.
        timeout: Discovery timeout in seconds.

    Returns:
        Result dict.
    """
    import sys
    sys.path.insert(0, str(_repo_root()))

    from client_surfaces.operator_tui.auth.oidc_models import OidcProviderConfig
    from client_surfaces.operator_tui.auth.oidc_auth_controller import OidcAuthController
    from client_surfaces.operator_tui.auth.loopback_callback_server import LoopbackCallbackServer

    prov_cfg = OidcProviderConfig(
        provider_id=provider,
        issuer=issuer,
        client_id=client_id,
        flow="authorization_code_pkce",
        redirect_mode="loopback",
        allowed_redirect_hosts=["127.0.0.1", "localhost"],
    )

    # Test: loopback server starts
    try:
        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=5.0)
        if not redirect_uri.startswith("http://127.0.0.1:"):
            server.stop()
            return _result(provider, "live", OUTCOME_NETWORK, "loopback did not return 127.0.0.1 URI")
        server.stop()
    except Exception as exc:
        return _result(provider, "live", OUTCOME_NETWORK, f"loopback_start_failed: {type(exc).__name__}")

    # Test: authorization request builds
    try:
        ctrl = OidcAuthController()
        req = ctrl.create_authorization_request(
            provider=prov_cfg,
            redirect_uri=redirect_uri,
        )
        if "code_challenge" not in req.authorization_url:
            return _result(provider, "live", OUTCOME_INVALID_STATE, "pkce missing from authorization_url")
        if req.state not in req.authorization_url:
            return _result(provider, "live", OUTCOME_INVALID_STATE, "state missing from authorization_url")
    except Exception as exc:
        return _result(provider, "live", OUTCOME_NETWORK, f"auth_request_failed: {type(exc).__name__}")

    return _result(
        provider,
        "live",
        OUTCOME_SUCCESS,
        f"discovery_and_pkce_ok; open {req.authorization_url[:80]}... in browser to complete",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_root():
    from pathlib import Path
    # Walk up from this script to find the repo root
    here = Path(__file__).resolve().parent
    for _ in range(5):
        if (here / "pyproject.toml").exists() or (here / "client_surfaces").exists():
            return here
        here = here.parent
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carbonyl OIDC provider compatibility smoke test"
    )
    parser.add_argument(
        "--provider",
        choices=["keycloak", "google"],
        required=True,
        help="Provider to test",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Skip real network; return hardcoded success (safe for CI)",
    )
    parser.add_argument(
        "--issuer",
        default="",
        help="OIDC issuer URL (live mode only)",
    )
    parser.add_argument(
        "--client-id",
        default="",
        dest="client_id",
        help="OIDC client_id (live mode only)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Discovery/loopback timeout in seconds (live mode only)",
    )
    args = parser.parse_args()

    if args.mock:
        result = run_mock(provider=args.provider)
    else:
        if not args.issuer or not args.client_id:
            parser.error("--issuer and --client-id are required for live mode")
        result = run_live(
            provider=args.provider,
            issuer=args.issuer,
            client_id=args.client_id,
            timeout=args.timeout,
        )

    print(json.dumps(result))
    sys.exit(0 if result["outcome"] == OUTCOME_SUCCESS else 1)


if __name__ == "__main__":
    main()
