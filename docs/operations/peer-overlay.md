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
| backup activation exceeds 3 s | restore primary bulk path | bounded failover error |
| missing/expired backup lease | keep route closed | Hub replan required |
| slow child or queue cap | isolate/drop that child's bounded queue | partial data degradation |
| Hub partition | freeze new publication, route and lease authority | bounded offline grace |
| disabled flag | reject plans, tickets and forwarding authorization | Hub/control fallback |

Every recovery remains automatic and Hub-authorized. Tests and production flows
must not pause for human approval. A human may choose configuration, but lack of
interaction always produces a deterministic safe state rather than a waiting
workflow.

Offline authority uses closed Hub profiles: `strict` permits at most 30 seconds,
`balanced` 60 seconds and `availability` 120 seconds. A caller may request less,
but can never extend the selected profile. During grace, existing delivery may
continue; new publications, route changes and peer-side lease renewal remain
disabled. Membership gaps trigger a Hub snapshot request rather than a peer
merge or vote.

Relay admission requires explicit consent and visibility, non-critical battery,
an unmetered non-constrained network, at least 25% observed capacity, at least
95% delivery, no more than 80% CPU load, 500 ms RTT, 5% loss or 2 MiB queued
send data. The candidate must remain eligible for 15 seconds before it can
become a parent. Neighbor-observed capacity caps self-reported capacity; the
health policy adds a 30-second post-failover cooldown.
Missing resource fields take conservative ineligible defaults; legacy clients
remain accepted as leaves but cannot silently acquire relay authority.

After bootstrap, SDP/ICE messages use the existing peer DataChannel when it is
ready. Every offer is bound to one unexpired Hub-accepted edge ticket and its
Hub-selected offerer, limited to 64 KiB and rejected if bearer credentials or
private keys occur. Missing or raced in-band transport falls back automatically
to the Hub rendezvous path. It never waits for operator input.

## Headless four-peer browser capacity gate

Run `python scripts/run_peer_mesh_browser_capacity_gate.py` from the repository
root. The Hub runner admits the versioned harness as synthetic test source,
reserves a bound `RUN_*` identity before execution, passes only that assignment
projection to Playwright and records the terminal result. Chromium and Firefox
each execute four isolated identities for audio-only, 720p camera and synthetic
screenshare profiles. The report is written to
`artifacts/test-gates/peer-mesh-browser-capacity.json`.

The gate is fully headless. Its real browser measurements are useful for local
resource policy calibration, but its source and run are deliberately classified
as synthetic test evidence. The Hub therefore rejects them for local, external
or production release promotion.

## Rollback

1. Set `ANANTA_PEER_OVERLAY_DATA_ENABLED=false` and restart/reload the Hub.
2. Confirm overview reports `data_overlay=disabled`.
3. Existing membership history may remain; plans and tickets can no longer be
   issued or consumed while disabled.
4. Confirm group media selects LiveKit E2EE, or `relay_control_only` if no safe
   media route is available.

Rollback requires no data migration. Do not delete the SQLite state during an
incident; it is the immutable audit trail for revision and ticket replay checks.
