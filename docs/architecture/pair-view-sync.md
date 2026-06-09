# Pair-Dev View-Sync — Architecture & Wiring

## Goal

Allow two users to work together on the same UI without
screen-sharing. Instead of streaming the rendered output, the
Owner and Participant share a minimal **view-state delta**:
which route they're on, which tab is active, which artifact is
open, where the cursor sits, and (when explicitly granted)
control over each other's navigation.

## Why not screen-sharing?

| Concern | Screen-sharing | View-state delta |
|---|---|---|
| Bandwidth | Many MB/s, varies with content | < 1 KB/change |
| Privacy | Renders everything, including chrome | Only the documented fields |
| Latency | Round-trip through the streaming server | Direct or one-hop via Hub |
| Works on low-bandwidth | No | Yes (Deltas < 1 KB) |
| Requires a session record | Yes | Yes (uses existing `share-sessions`) |

## Wire contract

The data flow is:

```
Local UI events
    ↓
SharedViewStateService (captures: route, tab, panel, artifact, scroll, cursor)
    ↓ debounce / throttle
ViewDeltaService (computes minimal delta, hashes)
    ↓ encrypts payload
PairViewSyncService (sends over WebRTC DataChannel OR Hub Relay)
    ↓
WebrtcTransportService (relay-aware: /view/push on Hub, DataChannel on P2P)
    ↓
hub_relay_backend: agent/routes/share_sessions.py::push_view_payload
   or
webrtc_datachannel_backend (no server hop)

On the receiver side the bytes flow in reverse through
the same services. Incoming envelopes are validated by
pair-view-sync.validators and applied to local state via
the receiver's SharedViewStateService.updatePartial().
```

### Field contract (v1)

Synced fields (`shared-view-state.service.ts`):

- `route`, `queryParams`, `activeSurface`, `activeTab`, `activePanel`
- `activeArtifactId`, `activeArtifactHash`, `activeFilePath`, `activeSymbolId`
- `scroll`, `cursor`, `selection`, `zoom`
- `collapsedSections`

Out of scope (deliberately): chat text (uses existing chat path),
artifacts content (uses existing artifact API), task state (uses
existing task stream), LLM tokens (private by design).

### Permission keys

| UI label        | Backend key   | Default | Requires explicit grant |
|-----------------|---------------|---------|--------------------------|
| Chat            | `chat`        | true    | no                       |
| TUI-Ansicht     | `view_tui`    | true    | no                       |
| Remote-Cursor   | `cursor`      | false   | no                       |
| Steuerung       | `control`     | false   | **yes**                  |
| Artefakte sehen | `artifact_view` | true  | no                       |
| Annotationen    | `annotation`   | false  | **yes**                  |

## Control default-deny (T12)

A control grant is **never** implied by view_tui, cursor, or
artifact_view. The handshake is:

1. Partner sends a `control` message with `kind='request'`.
2. Owner's `PairViewSyncService` checks `share.currentPermissions().control`.
3. If `true`, the service generates an opaque grant_token and
   sends a `control` message with `kind='grant'`.
4. The Partner can now issue control actions.
5. Either side sends `kind='revoke'` to invalidate the grant.

The grant_token is session-scoped, never persisted, and
verified against the local `controlGrantToken` field. The
backend enforces the same default-deny via the share-session
permissions dict.

## Transport selection

`WebrtcTransportService.mode$` is either `'webrtc'` or
`'hub_relay'`. View-sync envelopes use the same transport
selection logic as chat:

- `webrtc`: `WebrtcSessionService.sendDc('view_payload', envelope)`
  → no server hop, low latency, requires successful WebRTC
  signalling.
- `hub_relay`: `POST /share-sessions/{id}/view/push` with
  the backend-compatible `RelayEnvelope` body. The Hub Relay
  keeps the last 10 messages per session and serves them via
  `GET /view/poll?since=…`.

The Hub Relay response now carries `view_messages` (flat
list of RelayEnvelopes) and `view_cursor` (last message_id)
in addition to the legacy `data.frames` shape.

## Service layout

```
pair-view-sync.types.ts        — type contracts
permission-labels.ts           — UI label ↔ backend key
pair-view-sync.validators.ts   — type guards (path-whitelist, prototype guard)
shared-view-state.service.ts   — captures UI state, computes viewHash
view-delta.service.ts          — diffs states, applies deltas
pair-view-sync.service.ts      — send debounce + apply; control handshake
pair-view-sync-panel.component — UI dialog
```

## What is NOT in scope (yet)

- Cursor shadow rendering (the cursor payload is captured and
  forwarded, but the rendering layer is left to a future
  presence service).
- Live presence cursors for the snake/AI-room.
- Editing artifacts live (annotation is one-way).
- Multi-participant (n>2) view-sync.
- Auth/identity of the receiver (current: any joined participant
  with `view_tui=true`).

## Failure modes & how the contract handles them

| Failure | Symptom | Behaviour |
|---|---|---|
| `baseHash` mismatch on delta | Two clients raced; deltas out of order | Receiver requests snapshot via `snapshot_request` |
| Encrypted payload too large (>256 KB) | Snapshot bigger than _VIEW_PAYLOAD_MAX_BYTES | Sender drops; receiver stays on previous state |
| Path not in whitelist | Malicious/old client | Validators reject; `appliesRejected++` |
| Control request without permission | User toggled `control=false` | `controlDenied++`; no grant issued |
| WebRTC drops mid-session | Both clients fall back to Hub Relay | `mode$` switches; same envelopes travel over relay |
| Unbind/rebind sequence | Receiver re-applies same delta | viewHash dedup; seq counter advances |
