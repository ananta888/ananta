# OIDC Authentication

Ananta supports OIDC Authorization Code Flow with PKCE for browser-based access and production deployments.

## Enabling OIDC

Set the following environment variables:

```env
TERMINAL_OIDC_ENABLED=true
TERMINAL_OIDC_ISSUER=https://your-keycloak.example.com/realms/ananta
TERMINAL_OIDC_AUDIENCE=ananta-hub
TERMINAL_OIDC_CLIENT_ID=ananta-hub
```

To disable local username/password login in production:

```env
AUTH_MODE=oidc_bff
```

## OIDC flow

1. Browser navigates to `GET /auth/oidc/login`
2. Hub generates PKCE `code_verifier` + `code_challenge` and redirects to OIDC provider
3. User authenticates with provider; provider redirects to `GET /auth/oidc/callback?code=...&state=...`
4. Hub exchanges code for `id_token`, validates it (issuer, audience, expiry, nonce, signature)
5. Hub maps token claims to Ananta auth context and stores it in the session

## Claim mapping

| OIDC group/role          | Ananta role  | Terminal permissions                          |
|--------------------------|--------------|-----------------------------------------------|
| `ananta-admin`           | admin        | `terminal.worker.*`                           |
| `ananta-user`            | user         | `terminal.worker.*`                           |
| `ananta-viewer`          | viewer       | `terminal.worker.list`, `terminal.worker.read` |
| `ananta-terminal-hub`    | (additive)   | `terminal.hub.*`, `terminal.hub_as_worker.*`  |
| `ananta-terminal-worker` | (additive)   | `terminal.worker.*`                           |

Unknown groups grant **no** permissions.

## Keycloak example

1. Create realm `ananta`
2. Create client `ananta-hub` with:
   - `Standard Flow Enabled: true`
   - `Valid Redirect URIs: https://hub.example.com/auth/oidc/callback`
   - `PKCE Method: S256`
3. Create groups: `ananta-admin`, `ananta-user`, `ananta-viewer`
4. Add users to groups

### Local current Keycloak via compose overlay

Use the optional overlay `docker-compose.oidc-keycloak.yml` to run a local Keycloak for OIDC:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.oidc-keycloak.yml --profile oidc up -d
```

Default issuer used by this overlay:

```env
TERMINAL_OIDC_ISSUER=http://localhost:8081/realms/ananta
TERMINAL_OIDC_CLIENT_ID=ananta-hub
TERMINAL_OIDC_AUDIENCE=ananta-hub
```

The implementation path remains unchanged and uses `GET /auth/oidc/login` and `GET /auth/oidc/callback` in `agent/routes/auth_oidc.py`.

## Local dev fallback

When `TERMINAL_OIDC_ENABLED=false` (default), standard username/password login via `POST /login` remains active.

When `AUTH_MODE=oidc_bff` is set, local login is disabled even if OIDC is not yet configured — set this only when OIDC is fully configured and tested.
