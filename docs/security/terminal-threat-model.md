# Terminal Sessions — Threat Model

## Scope

This document covers tmux-backed terminal sessions exposed through the Ananta Hub's REST/WSS API and CLI. It does **not** cover the embedded editor/TUI tool shortcuts (`ananta tmux edit`, `ananta tmux tool`), which have a separate security surface.

---

## Target classification

| Target type       | Default policy              | Risk class                         |
|-------------------|-----------------------------|------------------------------------|
| `worker`          | allow if policy grants      | `terminal_workspace_mutation`      |
| `hub`             | **deny**                    | `terminal_hub_runtime_access`      |
| `hub_as_worker`   | **deny**                    | `terminal_hub_runtime_access`      |

Hub and Hub-as-Worker terminal access is denied by default even for admin users. Both require explicit permission grants via `terminal.hub.*` or `terminal.hub_as_worker.*` permissions.

---

## Permission matrix

| Permission                        | worker | hub | hub_as_worker |
|-----------------------------------|--------|-----|---------------|
| `terminal.<type>.list`            | user   | admin (list only) | denied |
| `terminal.<type>.create`          | user   | denied | denied |
| `terminal.<type>.attach`          | user   | denied | denied |
| `terminal.<type>.read`            | user   | denied | denied |
| `terminal.<type>.write`           | user   | denied | denied |
| `terminal.<type>.kill`            | user   | denied | denied |

Granting `terminal.worker.*` does **not** imply any `terminal.hub.*` permission. Each target type is an independent permission surface.

---

## Hard denies

The following are permanently blocked regardless of policy configuration:

- `anonymous_terminal_access` — no unauthenticated terminal endpoints
- `raw_tmux_socket_exposure` — tmux socket paths are never returned to clients
- `unauthorized_cross_user_attach` — session ownership is checked on every read/write/attach
- `implicit_hub_terminal_via_worker_permission` — Worker permissions never cascade to Hub
- `workspace_path_traversal` — workspace paths are validated; `..` components are rejected
- `secret_path_access_without_explicit_policy` — `terminal.secret_path.access` is denied by default
- `unconfigured_host_mount_escape` — compose files do not mount `/` or Docker socket by default

---

## Attach token security

- Tokens are single-use: consuming a token invalidates it immediately
- TTL is configurable via `TERMINAL_ATTACH_TOKEN_TTL_SECONDS` (default 60s)
- Tokens are scoped to exactly one session and one user
- Tokens are generated with `secrets.token_urlsafe(32)` — 256 bits of entropy

---

## Session lifecycle

```
create → running → attached ↔ detached → killed
                                        → expired (idle or max lifetime)
                                        → failed
```

All lifecycle transitions are audit-logged via `TerminalEventDB`.

---

## Why admin does not bypass terminal policy

Admin role grants elevated permissions for goal, task, and worker management. Terminal access is a separate capability surface. An admin user can:

- List `terminal.hub` (by default)
- List/create/attach/read/write/kill `terminal.worker`

An admin user **cannot** by default:
- Create/attach/write Hub terminal sessions

This is intentional. Hub terminal access represents direct control over the orchestration plane and must be explicitly granted.

---

## Audit events

Every terminal operation emits a `TerminalEventDB` record:

| Event type                  | When                                          |
|-----------------------------|-----------------------------------------------|
| `session_create_requested`  | Before policy check on create                 |
| `session_created`           | After successful creation                     |
| `session_attach_requested`  | When attach token is issued                   |
| `session_attached`          | When WebSocket stream opens                   |
| `session_input`             | On every write operation                      |
| `session_output_read`       | On every REST output capture                  |
| `session_detached`          | When WebSocket stream closes                  |
| `session_kill_requested`    | Before kill policy check                      |
| `session_killed`            | After successful kill                         |
| `session_expired`           | On idle or max-lifetime expiry                |
| `policy_denied`             | On every denied operation                     |
| `gateway_disconnected`      | On unexpected WebSocket disconnect            |

---

## Container security notes

- `tmux` is installed in the image but `TERMINAL_FEATURE_ENABLED=false` by default
- No compose file exposes a raw terminal port
- No compose file mounts `/` or `/var/run/docker.sock` for terminal use
- Hub container uses `TERMINAL_HUB_TARGET_ENABLED=false` by default

---

## OIDC security notes

- Authorization Code Flow with PKCE — no implicit flow
- `issuer`, `audience`, `exp`, `iat`, `sub` are all validated
- Unknown OIDC groups grant no permissions
- Group → role mapping is deterministic and logged
- Local username/password login can be disabled via `AUTH_MODE=oidc_bff`
