# Native SSH Terminal Access (OIDC + Short-Lived Certificates)

Ananta supports native SSH terminal access as a separate mode from browser terminal access.

- Web mode: OIDC + HTTPS/WSS terminal gateway.
- Native SSH mode: OIDC identity + short-lived SSH certificate + forced Ananta wrapper.
- Local dev mode: local tmux only.

## Security model

- tmux itself does not implement OIDC. OIDC must be enforced by Ananta auth flow or SSH certificate issuance.
- Raw tmux sockets must never be exposed remotely.
- SSH certificate possession does not bypass `TerminalPolicyService`.
- Hub and `hub_as_worker` access remain separate high-risk permissions.

## Recommended production path

1. User authenticates with OIDC (Authorization Code + PKCE).
2. SSH certificate issuer binds OIDC identity to short-lived SSH certificate.
3. `sshd` validates certificate via `TrustedUserCAKeys`.
4. `ForceCommand` calls `ananta-ssh-terminal-wrapper`.
5. Wrapper validates target/workspace and calls terminal policy checks before tmux.

## Keycloak with existing OpenID Connect implementation

Ananta uses the existing OIDC endpoints in `agent/routes/auth_oidc.py`:

- `GET /auth/oidc/login`
- `GET /auth/oidc/callback`
- `GET /auth/oidc/userinfo`
- `POST /auth/oidc/logout`

Keycloak must expose a realm issuer URL that matches `TERMINAL_OIDC_ISSUER`:

`http://localhost:8081/realms/ananta`

Required env vars:

```env
TERMINAL_OIDC_ENABLED=true
TERMINAL_OIDC_ISSUER=http://localhost:8081/realms/ananta
TERMINAL_OIDC_AUDIENCE=ananta-hub
TERMINAL_OIDC_CLIENT_ID=ananta-hub
```

## Compose overlay for current Keycloak

Use the dedicated overlay:

```bash
docker compose \
  -f docker-compose.base.yml \
  -f docker-compose.yml \
  -f docker-compose.oidc-keycloak.yml \
  --profile oidc up -d
```

This starts Keycloak `26.6.2` and wires Ananta hub OIDC settings to the existing implementation.

## OpenSSH hardening references

- `deploy/examples/sshd_config.ananta.example`
- `deploy/examples/ananta-authorized-principals.example`
