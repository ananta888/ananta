# Identity Architecture

Ananta has two independent user identities and one device-scoped Pair
identity. They must not be collapsed into one bearer-token domain.

| Sphere | Authority | Token use |
|---|---|---|
| `hub` | Ananta Hub login | Hub APIs; the Hub owns worker authorization and delegation |
| `oidc` | `keycloak.ananta.de` via OIDC/PKCE | Pair Dev and `webrtc.ananta.de` |
| `pair peer` | Derived from verified OIDC `(iss, sub)` plus the verified P-256 device-key fingerprint | E2EE addressing and WebRTC signaling |

Public Pair identity binding v2 deliberately separates the Keycloak account
principal (`oidc:...`) from the device peer (`peer:...`). Two computers may
therefore use the same Keycloak account in one Pair session, but they must have
distinct local P-256 keys. Reusing or copying the same private device key is
rejected as a reflected/self pairing.

The peer id is a selector, not an authenticator. Every v2 create/join attempt
uses a client-generated 256-bit membership capability that is persisted in
the browser tab before the request is sent. Follow-up requests carry the exact
peer id and capability together with the OIDC bearer. The Rendezvous stores
only domain-separated capability hashes. It never returns the plaintext
capability in responses, lists, URLs or logs. Existing v1 sessions keep their
account-scoped peer identifiers and are not silently upgraded; create a new
session to use same-account device pairing.

The public native-WebSocket signaling protocol is not implemented yet. Public
Pair sessions use authenticated HTTP polling at `webrtc.ananta.de`; Hub-owned
sessions use the Hub's `/api/webrtc/sessions/<id>/signal` boundary. Neither
path falls back to the other authority. A Hub token is not valid at the public
Rendezvous, and an OIDC token is not valid at the Hub. A future public
WebSocket adapter must use an explicit, short-lived credential exchange rather
than putting an OIDC bearer token, peer capability or nonce into a WebSocket
URL.

Workers do not accept a browser's Hub or Keycloak user token as an implicit
fallback. Browser operations targeting workers flow through the Hub, which
owns worker credentials and routing.

## Independent login

The default login page exposes both login methods:

- username/password establishes only a Hub session;
- Keycloak establishes only a Pair/WebRTC session.

Logging out from the Hub does not terminate an active Pair/WebRTC session.
Logging out from Keycloak disconnects WebRTC but does not invalidate the Hub
session. “Log out all” clears both.

The browser steps, Hub feature flag and ordinary audio/video failure codes are
documented in [Pair Dev: Audio und Video](user/pair-dev-audio-video.md).

The repository's local HTTPS Nginx edge is deliberately pinned to the public
`https://keycloak.ananta.de` issuer in its Content Security Policy. Its
Keycloak client contract admits the development origins
`http://localhost:4200` and `http://127.0.0.1:4200`, plus the local HTTPS edge
at `https://localhost` and `https://127.0.0.1`. A deployment that selects a
different profile issuer must add that exact origin to both the edge CSP and
the Keycloak client; arbitrary HTTPS origins are not an implicit fallback.

Hub access tokens and refresh tokens use:

- `ananta.user.token`
- encrypted `ananta.hub.refresh_token`

OIDC access tokens and refresh tokens use:

- `ananta.oidc.access_token`
- encrypted `ananta.oidc.refresh_token`

The refresh paths are deliberately separate. `UserAuthService.refreshToken()`
only calls the Hub. `OidcAuthService.refreshFromStorage()` only calls the OIDC
provider.

## Optional account linking and SSO

Account linking is opt-in and default-deny. Configure `OIDC_ENABLED=true` plus
`OIDC_ISSUER_URL`, `OIDC_JWKS_URL`, `OIDC_AUDIENCE`, and `OIDC_CLIENT_ID`.
The configured issuer must match the Pair profile issuer.

Linking requires both active identities:

1. the user authenticates to an existing Hub account;
2. the user authenticates to Keycloak;
3. the user explicitly selects “Hub- und Keycloak-Konto verknüpfen”;
4. `POST /auth/oidc/link` validates the OIDC token and persists the unique
   `(issuer, subject) → Hub username` mapping.

On later Keycloak logins, `POST /auth/oidc/exchange` may issue a normal
Hub-signed access/refresh-token pair only when this mapping exists. The Hub
continues to accept only Hub-signed JWTs on `@check_user_auth` endpoints.
Unlinked or invalid OIDC identities never fall back to an automatically
created Hub account.

`GET /auth/oidc/link` reports link status and `DELETE /auth/oidc/link` removes
the current Hub user's link.

## Network profile contract

`oidc.enabled` means Pair/WebRTC login is available.
`oidc.hub_link_enabled` means explicit Hub account linking is configured.
`oidc.bridge_active` remains as a temporary compatibility alias for older
`oidc.registration_allowed` means self-registration at the configured Keycloak
realm is available — see "Self-registration at the IdP" below.

frontends. Hub-link configuration never overwrites the Pair profile's issuer
or client ID.

## Self-registration at the IdP

Pair-Dev and WebRTC identities are managed in the configured Keycloak realm.
In addition to the existing login flow, users who do not yet have a Keycloak
account can self-register directly at the IdP.

### Configuration (default-deny)

1. **Hub side**: set `OIDC_REGISTRATION_ALLOWED=true` in the Hub environment.
   This single env-var is the source of truth for the registration gate.
2. **Keycloak side**: in the Keycloak admin console, open
   `Realm → Login → User registration` and enable `User registration`. Without
   this Keycloak-side flag, the button is visible but Keycloak rejects the
   registration. The Hub cannot enforce the Keycloak setting — it must be
   confirmed by the realm admin.

Both conditions must be satisfied for the frontend to render the
"Neues Konto bei Keycloak anlegen" button:

  - Hub env-var `OIDC_REGISTRATION_ALLOWED=true`
  - `oidc_is_configured()` returns true (OIDC fully wired with all required
    fields)

When either condition fails, the button is hidden everywhere it would
otherwise appear (`login.component`, `ai-snake-chat-panel`).

### URL flow

The button opens the Keycloak-standard self-registration page in a new tab:

    ${oidc.issuer}/login-actions/registration

There is no PKCE state, no callback handling, no silent flow. After the user
completes the Keycloak-native registration form, they return to the Ananta
login page and click "Bei Keycloak anmelden" to perform the standard OIDC
login.

This is deliberately a separate flow from the standard login: the user
explicitly navigates between the two, which prevents silent cross-flow state
leaks and matches the "explicit consent" security boundary used elsewhere.

### Frontend visibility (single source of truth)

`IdentityBridge.showRegistration` returns true iff:

  1. `oidc.registration_allowed` is set by the backend (i.e. both config
     conditions above hold)
  2. `oidc.issuer` and `oidc.client_id` are present (i.e. a Pair provider is
     configured)
  3. A `hub`-role agent is registered in `AgentDirectoryService` locally

Condition (3) is a frontend-side defensive invariant: there is no point in
showing a registration button if the device has no Hub to talk to after
login.

### Audit

`OIDC_REGISTRATION_ALLOWED=true` does not generate audit events on its own
(the flag is config, not a login attempt). The actual Keycloak
self-registration form is logged inside Keycloak itself and surfaces in its
own audit log. We do not mirror Keycloak-side events into the Hub audit log.

## Lifecycle

At application startup the identity initializer restores both token stores and
loads the active network profile. The route guard allows the application when
either identity is ready. A Hub-protected action that receives 401 routes to
`/login?sphere=hub`; Pair/WebRTC auth failures route to
`/login?sphere=oidc`.
