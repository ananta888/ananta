# Identity Architecture

Ananta has two independent user identities and one derived signaling
identity. They must not be collapsed into one bearer-token domain.

| Sphere | Authority | Token use |
|---|---|---|
| `hub` | Ananta Hub login | Hub APIs; the Hub owns worker authorization and delegation |
| `oidc` | `keycloak.ananta.de` via OIDC/PKCE | Pair Dev and `webrtc.ananta.de` |
| `signaling` | Derived from `oidc` | WebRTC signaling |

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
frontends. Hub-link configuration never overwrites the Pair profile's issuer
or client ID.

## Lifecycle

At application startup the identity initializer restores both token stores and
loads the active network profile. The route guard allows the application when
either identity is ready. A Hub-protected action that receives 401 routes to
`/login?sphere=hub`; Pair/WebRTC auth failures route to
`/login?sphere=oidc`.
