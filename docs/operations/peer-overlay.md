# Peer overlay operations and automatic recovery

The peer data overlay is default-off. Enable it only for an explicit canary by
setting `ANANTA_PEER_OVERLAY_DATA_ENABLED=true` on the Hub. Peer media overlay
stays no-go; larger group media must use LiveKit E2EE.

## Content-free overview

`GET /api/peer-overlay/overview?tenant_id=...&room_id=...` reports current
membership and publication plans, route epochs, the media no-go, fallback and
whether the data path is disabled, enabled or active. Never copy tickets,
signatures, ICE data or application payloads into operator logs.

## Automatic actions

| Detection | Automatic action | User-visible state |
| --- | --- | --- |
| SFU unavailable above safe mesh size | refuse false direct-group success | `relay_control_only` |
| stale membership/plan revision | reject mutation | refresh Hub snapshot |
| expired/stale/forged ticket | reject edge | retry through Hub |
| single relay complaint | keep primary | degraded observation, no ban |
| quorum drop/delay complaint | rate-limited backup ticket | switching data parent |
| missing/expired backup lease | keep route closed | Hub replan required |
| slow child or queue cap | isolate/drop that child's bounded queue | partial data degradation |
| Hub partition | freeze new publication, route and lease authority | bounded offline grace |
| disabled flag | reject plans, tickets and forwarding authorization | Hub/control fallback |

Every recovery remains automatic and Hub-authorized. Tests and production flows
must not pause for human approval. A human may choose configuration, but lack of
interaction always produces a deterministic safe state rather than a waiting
workflow.

## Rollback

1. Set `ANANTA_PEER_OVERLAY_DATA_ENABLED=false` and restart/reload the Hub.
2. Confirm overview reports `data_overlay=disabled`.
3. Existing membership history may remain; plans and tickets can no longer be
   issued or consumed while disabled.
4. Confirm group media selects LiveKit E2EE, or `relay_control_only` if no safe
   media route is available.

Rollback requires no data migration. Do not delete the SQLite state during an
incident; it is the immutable audit trail for revision and ticket replay checks.
