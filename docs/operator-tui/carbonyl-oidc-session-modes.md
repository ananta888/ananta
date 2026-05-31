# Carbonyl OIDC Session Modes

The Operator TUI keeps OIDC token ownership in Python by default. Carbonyl may
render the provider page and receive browser cookies, but raw provider tokens
must not be passed into browser JavaScript, signaling, audit logs, or WebRTC
DataChannel messages.

## Modes

### Ananta-owned callback

This is the default mode. `OidcAuthController` creates the authorization URL
with PKCE, state, and nonce. `LoopbackCallbackServer` listens on
`127.0.0.1:<random>/callback`, receives the provider redirect, and hands the
callback URL back to the controller. The controller validates request expiry,
provider identity, callback host/path, state, and non-secret ID-token claims
when present.

The only WebRTC handoff values are:

- `subject_hash`: SHA-256 of provider id plus subject.
- `session_nonce`: fresh random nonce generated for the realtime session.

### Real browser session

This mode is explicitly opt-in and remains isolated by Carbonyl profile. It is
intended for providers that require a fuller browser session. The provider
session remains inside the selected Carbonyl profile. Raw tokens still stay out
of signaling and WebRTC payloads.

## Smoke Checks

Offline provider compatibility checks:

```bash
.venv/bin/python scripts/operator_tui_carbonyl_oidc_smoke.py --provider keycloak --mock
.venv/bin/python scripts/operator_tui_carbonyl_oidc_smoke.py --provider google --mock
```

Focused regression checks:

```bash
.venv/bin/python -m pytest tests/client_surfaces/operator_tui/auth/test_oidc_auth_controller.py tests/client_surfaces/operator_tui/auth/test_loopback_callback_server.py -q
```
