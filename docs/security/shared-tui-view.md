# Shared TUI View Security

## Scope

Shared View provides **read-only** remote rendering of compact Ananta/TUI state
between authenticated, active session members. In a strict Pair session, both
members may send recipient-bound, end-to-end encrypted snapshots and deltas.
It does **not** grant execution, automatic navigation, or remote control rights.

## Security guarantees

1. View sharing is default-off and must be explicitly enabled (`view_tui=true`).
2. Strict Pair payloads require an authenticated active sender and recipient,
   the current security epoch, and mutual key confirmation.
3. Revoked/expired sessions are blocked.
4. Payload size and rate limits protect relay endpoints.
5. The relay receives opaque encrypted envelopes. Audit stores only metadata,
   hashes, the canonical session-owner digest, and a separate sender digest.
   Raw user IDs and plaintext snapshots are never written to the audit event.
6. The legacy compatibility relay remains owner-sender-only.

## Redaction and policy

- The compact protocol uses a closed field allowlist. It omits DOM content,
  text/form values, query parameters, URL fragments, and local-only panels.
- Current producers do not capture form or free-text content. The bounded
  route/tab/panel strings and separately permitted artifact identifiers are
  not scanned for token/password patterns; artifact identifiers remain `null`
  unless the separate `artifact_share` permission is active.
- The same client-side projection policy applies to direct WebRTC and the
  optional relay path.

## Permissions matrix

| Permission | Default | Meaning |
|---|---:|---|
| `chat` | true | Send/receive shared chat |
| `view_tui` | false | Send/receive compact snapshot/delta frames between active members |
| `remote_cursor` | false | Optional cursor/presence overlay only |
| `artifact_share` | false | Artifact exchange rights |
| `remote_control` | false | Mutating control path (not enabled in MVP) |

## Why remote control is disabled

Remote control has higher blast radius (command execution and state mutation).  
It requires a separate permission, dedicated UX confirmation, and stricter controls.
