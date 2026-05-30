# Shared TUI View Security

## Scope

Shared View provides **read-only** remote rendering of an owner TUI.  
It does **not** grant execution or remote control rights.

## Security guarantees

1. View sharing is default-off and must be explicitly enabled (`view_tui=true`).
2. Only active session participants can poll view frames.
3. Revoked/expired sessions are blocked.
4. Payload size and rate limits protect relay endpoints.
5. Audit stores metadata/hashes, never full plaintext snapshots.

## Redaction and policy

- Sensitive fields (tokens/password-like patterns) are redacted before sharing.
- Notes and local-only panels are not shared by default.
- Same policy applies for relay and optional WebRTC transport.

## Permissions matrix

| Permission | Default | Meaning |
|---|---:|---|
| `chat` | true | Send/receive shared chat |
| `view_tui` | false | Receive owner snapshot/delta frames |
| `remote_cursor` | false | Optional cursor/presence overlay only |
| `artifact_share` | false | Artifact exchange rights |
| `remote_control` | false | Mutating control path (not enabled in MVP) |

## Why remote control is disabled

Remote control has higher blast radius (command execution and state mutation).  
It requires a separate permission, dedicated UX confirmation, and stricter controls.
