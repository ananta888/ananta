# Operator TUI Shared Sessions

This document explains how to use **OIDC + Device Key + Share Session** in the Operator TUI.

## Security model

1. **OIDC user identity** authenticates the human user.
2. **Device key fingerprint** identifies the concrete local TUI instance/device.
3. **Share session permissions** gate chat/view/cursor/artifact/control capabilities.
4. **Remote control stays disabled by default** (`remote_control=false`).

## Typical flow

1. Start TUI and open **Share / Teilnehmer**.
2. Run `:oidc login`.
3. Generate a device key with `:share key generate`.
4. Create session: `:share create <title>`.
5. Share invite code (`:share invite`) with participant.
6. Participant joins: `:share join <code>`.
7. Optionally enable view sharing: `:share view on`.

## Architecture

Hub relay is the MVP transport. Optional WebRTC signaling/data path can be used when available, but policy/permissions stay identical.

```mermaid
flowchart LR
  A[Owner TUI] -->|OIDC + Device Key| H[Ananta Hub]
  B[Participant TUI] -->|OIDC + Device Key| H
  A -->|Invite code| B
  H -->|Policy + Grants + Audit| H
  A <-->|optional signaling| W[WebRTC signaling]
  B <-->|optional signaling| W
```

## Join sequence

```mermaid
sequenceDiagram
  participant A as Owner TUI
  participant H as Hub
  participant B as Participant TUI
  A->>H: create share session
  H-->>A: session_id + invite_code
  A->>B: invite_code
  B->>H: join(session_id, invite_code, device_fingerprint)
  H-->>B: participant granted
  H-->>A: participant joined (audit)
```

## Chat sequence

```mermaid
sequenceDiagram
  participant A as Sender TUI
  participant H as Hub Relay
  participant B as Receiver TUI
  A->>H: POST /share-sessions/{id}/chat/messages
  H->>H: permission check(chat) + rate-limit + audit
  H-->>B: GET /share-sessions/{id}/chat/messages
```

## Shared view sequence

```mermaid
sequenceDiagram
  participant A as Owner TUI
  participant H as Hub Relay
  participant B as Viewer TUI
  A->>H: POST /share-sessions/{id}/view/push (encrypted frame)
  H->>H: permission check(view_tui) + size/rate limits + audit
  B->>H: GET /share-sessions/{id}/view/poll
  H-->>B: encrypted snapshot/deltas
```

## Troubleshooting (OIDC/Keycloak)

- `oidc_context_required`: token does not contain `sub`; re-login via `:oidc login`.
- `not_authenticated`: expired token; login again.
- `Hub-Login fehlgeschlagen`: verify endpoint and credentials.
- `session_not_active`: session expired or revoked; create a new invite.
- `view_tui_permission_required`: owner must enable `view_tui` permission.
