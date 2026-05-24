# Terminal Gateway — HTTPS/WSS Access

## Overview

Ananta exposes browser-based terminal access over WSS using short-lived scoped attach tokens. The tmux socket is never exposed directly — all I/O is proxied through the Hub.

## Flow

```
Browser / CLI
  │
  ├─ POST /terminal/sessions         → creates tmux session, returns session_id
  ├─ POST /terminal/sessions/{id}/attach-token  → returns short-lived token (60s TTL)
  └─ WSS /ws/terminal/session?attach_token=<token>
         │
         ├─ token validated (single-use, scoped to session + user)
         ├─ tmux output polled and sent to browser
         └─ browser input forwarded to tmux (unless read_only)
```

## Attach token properties

- Single-use: consumed on first WSS connection
- Scoped: bound to exactly one `session_id` and one `user_id`
- Short TTL: default 60 seconds (`TERMINAL_ATTACH_TOKEN_TTL_SECONDS`)
- 256 bits of entropy (`secrets.token_urlsafe(32)`)
- Never sent in URL path — passed as query param over WSS (encrypted in production TLS)

## WSS endpoint

```
WSS /ws/terminal/session?attach_token=<token>
```

### Server-sent message types

| Type               | When                                              |
|--------------------|---------------------------------------------------|
| `ready`            | After successful token validation and attach       |
| `output`           | Terminal output chunk (secrets redacted)          |
| `error`            | Auth failure, session gone, or timeout            |

`ready` payload:
```json
{
  "session_id": "...",
  "target_type": "worker",
  "target_id": "...",
  "read_only": false,
  "tmux_session": "ananta-worker-abc123"
}
```

### Client-sent message types

| Type     | When               | Fields                     |
|----------|--------------------|----------------------------|
| `input`  | User keystrokes    | `data: string`             |
| `resize` | Terminal resize    | `cols: number, rows: number` |

Read-only sessions silently drop input.

## Session lifecycle via WSS

```
token issued → WSS connect → ready → output streaming ↔ input
                                   → disconnected (client close)
                                   → error (timeout / tmux gone)
```

On disconnect or error, session status transitions to `detached`. The tmux session stays running — reconnect via a new attach token.

## Security notes

- Gateway disconnect emits `gateway_disconnected` audit event
- Terminal output is redacted for known secret patterns before forwarding
- tmux socket path is never sent to the client
- A user cannot attach to another user's session by guessing `session_id` — the token validates both
- Expired tokens reject the connection before WebSocket upgrade completes

## Production setup

Behind a reverse proxy (nginx, Caddy):

```nginx
location /ws/terminal/ {
    proxy_pass http://hub:5000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

Ensure TLS terminates at the proxy — WSS must only be accessible over HTTPS in production.
