# Identity Architecture (3-Spheres Model)

Ananta v3.1+ uses a **three-sphere identity model** with **explicit bridge
rules** instead of a single monolithic "User" concept.

## Spheres

| Sphere    | Source                | Storage                              | Used for                                  |
|-----------|----------------------|--------------------------------------|-------------------------------------------|
| `hub`     | Direct hub login     | `ananta.user.token` + encrypted RT   | Hub-API calls, worker routing             |
| `oidc`    | Keycloak (OIDC/PKCE) | `ananta.oidc.access_token` + enc RT  | Public-ananta profile, signaling nonce    |
| `signaling` | derived from OIDC   | (none — pure function of OIDC)       | WebRTC signaling auth via OIDC nonce      |

## Storage Layout

All identity keys live under the `ananta.<sphere>.<field>` namespace.
The full layout is defined in `frontend-angular/src/app/services/identity/identity-storage-layout.ts`.

Refresh tokens are **always** encrypted with AES-GCM in IndexedDB before
being written to localStorage. The access token is stored in plain localStorage
because it is short-lived (≤ 1 hour) and required on every API call.

## Identity Sources

Each sphere is represented by an `IdentitySource`:

- `HubIdentitySource` — `frontend-angular/src/app/services/identity/hub-identity-source.ts`
- `OidcIdentitySource` — `frontend-angular/src/app/services/identity/oidc-identity-source.ts`
- `signaling` — derived source owned by `IdentityRegistry`

Each source emits an `IdentitySnapshot` with status `absent | authenticating | ready | expired`.

## Bridge Rules

Cross-sphere token exchange is **declarative**. The single rule today:

- `public-ananta.oidc-to-hub` — exchanges an OIDC access token for a Hub access
  token via `POST {hub}/auth/oidc/exchange`.

Rules are in `frontend-angular/src/app/services/identity/identity-bridge.config.ts`.
`IdentityBridge.mode()` returns `'oidc-bridge'` (rule active) or `'hub-direct'`
(direct hub login), which the LoginComponent uses to show only the relevant UI.

## IdentityRegistry

`IdentityRegistry` owns all three sources and:

- `restoreAllFromStorage()` — restores snapshots from storage at app boot
- `isAuthenticated$` — observable combining hub + oidc ready-status
- `logoutAll()` — logs out every sphere and hard-disconnects WebRTC
- When hub or oidc transitions to `absent`, the registry forces
  `WebrtcSignalingService.hardDisconnect()` so no peer connection carries
  credentials from a revoked identity.

## Lifecycle

1. `main.ts` registers `identityRestoreInitializer` (APP_INITIALIZER)
2. On app boot → `IdentityRegistry.restoreAllFromStorage()` runs synchronously
   for both hub and oidc
3. `identityGuard` (CanActivateFn on every protected route) checks
   `registry.isAuthenticated` — redirects to `/login` if absent
4. Login flow: store tokens via `HubIdentitySource.onAuthenticated` /
   `OidcIdentitySource.onAuthenticated`. The proactive-refresh timer starts
   60s before `exp`.
5. `IdentityRegistry.logoutAll()` clears all spheres + signaling

## Hard Disconnect

`WebrtcSignalingService.hardDisconnect()` is **irreversible**:

- Cancels reconnect timer
- Kills Hub-Relay poll
- Closes WebSocket with code 1000 / reason `identity revoked`
- Clears sessionId, signalingUrl, useHubRelay flag
- Idempotent — safe to call twice

This is wired in `IdentityRegistry` so that when hub OR oidc becomes absent,
WebRTC is torn down immediately.

## OnPush & NG0100

`AppComponent` uses `ChangeDetectionStrategy.OnPush`. To avoid NG0100 on auth
state changes, `headerUser` is a `computed` signal backed by `_userPayload`
and `_isLoggedIn` signals. A `token$` subscription in `ngOnInit` keeps these
signals in sync with `UserAuthService`.

## Test Coverage

| Layer        | Tests | File                                      |
|--------------|-------|-------------------------------------------|
| Types        | 7     | `identity.types.spec.ts`                  |
| Snapshot     | 18    | `identity-snapshot.spec.ts`               |
| Storage      | 7     | `identity-storage-layout.spec.ts`         |
| Hub source   | 11    | `hub-identity-source.spec.ts`             |
| OIDC source  | 9     | `oidc-identity-source.spec.ts`            |
| Bridge       | 10    | `identity-bridge.spec.ts`                 |
| Registry     | 13    | `identity-registry.spec.ts`               |
| Guard        | 3     | `identity.guard.spec.ts`                  |
| Initializer  | 3     | `identity-restore.initializer.spec.ts`    |
| Signaling    | 6     | `webrtc-signaling.service.spec.ts`        |

**Total: 87 identity-related unit tests, all green.**

## Default Behaviour on 401 (no SSO Bridge configured)

Without an active bridge rule (`identity-bridge.config.ts` empty, or
`oidc.enabled = false` in the profile), **each sphere requires its own login**:

| Sphere    | 401 from Hub on hub-sphere endpoint | UI behaviour               |
|-----------|-------------------------------------|----------------------------|
| `hub`     | Yes                                 | Show Hub login mask        |
| `oidc`    | Yes                                 | Show OIDC login mask       |
| `signaling` | Yes (derived, falls through)     | Show OIDC login mask       |

**Silent fallbacks are forbidden by policy** (see AGENTS.md: default-deny,
no implicit trust between components). The auth interceptor MUST show a
login mask on 401 — never retry with empty/stale tokens.

This explains the historical "401 UNAUTHORIZED on `/share-sessions`"
message: when the user is on a profile where OIDC is enabled but Hub
login has not happened yet, Hub rejects the request and the frontend
shows the Hub login mask. After login the share-panel works.

## Enabling the Hub↔OIDC SSO Bridge (opt-in)

The bridge is **opt-in** and must be configured explicitly. Two required
steps:

1. **Profile config** — set `oidc.enabled = true` in `profiles.yaml`
   together with `oidc.issuer_url`, `oidc.client_id`, `oidc.audience`.
2. **Frontend** — declare the bridge rule in `identity-bridge.config.ts`
   (`oidc-to-hub` → `POST {hub}/auth/oidc/exchange`).

Only then will a single Keycloak login produce both the OIDC token
(for webrtc.ananta.de) and the Hub JWT (for `/share-sessions` etc.).

## Migration Notes

- Public-ananta profile: users authenticate via Keycloak → bridge exchange → Hub JWT.
  RT is stored encrypted; OIDC RT and Hub RT live in **separate** keys
  (no collision with the legacy single-key model).
- Local/enterprise profile: users log in directly to the Hub. OIDC flow is
  not shown in the UI for these profiles.
- Existing `UserAuthService.token$` consumers continue to work — Hub's
  ready snapshot drives the same BehaviorSubject.