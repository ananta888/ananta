# SFU broadcast dashboard, alerts and read-only triage

## Boundary

This document covers `SFB-OBS-006` dashboard and alert triage. It does not
activate broadcast, mutate a room, drain a node, revoke membership, toggle a
kill switch or perform rollback. Executable incident, game-day and rollback
procedures belong to `SFB-GATE-010`.

The Grafana dashboard is
`config/monitoring/dashboards/sfu-broadcast.json`. Prometheus rules are in
`config/monitoring/sfu-broadcast-alerts.yml`. Both consume only metrics
registered by `config/sfu_broadcast_observability_catalog.json`.

## Privacy and cardinality invariants

- Queries use complete aggregate windows only.
- Export suppression and `min_cohort_size=10` happen before dashboard and alert
  evaluation. Missing small cohorts are never converted to zero.
- Queries have no tenant, room, node, participant, publication, receiver,
  address or device variables.
- Grouping is limited to Prometheus histogram `le` and closed catalog labels
  such as `drop_reason`, `resource` and `capacity_state`.
- Dashboard and alert labels contain no payload, transcript, key, token,
  credential, SDP, ICE, IP address or original identifier.
- The dashboard refresh interval is one minute and its lookback is bounded to
  six hours by default.

## Alert index and clear conditions

| Alert | Severity | Owner | Threshold source | Clear condition | Future runbook |
|---|---|---|---|---|---|
| `SfuBroadcastJoinLatencyP95High` | critical | media-platform | default SLO join p95 | p95 at or below 2000 ms for 15 min | `SFB-GATE-010/RB-SFB-JOIN` |
| `SfuBroadcastRouteConvergenceP95High` | critical | media-platform | default SLO route p95 | p95 at or below 1000 ms for 15 min | `SFB-GATE-010/RB-SFB-ROUTE` |
| `SfuBroadcastRekeyP99High` | critical | media-security | catalog rekey bucket | p99 at or below 2000 ms for 10 min | `SFB-GATE-010/RB-SFB-REKEY` |
| `SfuBroadcastLayerTransitionP99High` | warning | media-platform | catalog layer bucket | p99 at or below 5000 ms for 15 min | `SFB-GATE-010/RB-SFB-LAYER` |
| `SfuBroadcastDrainP99High` | warning | runtime-platform | default cleanup timeout | p99 at or below 15000 ms for 15 min | `SFB-GATE-010/RB-SFB-DRAIN` |
| `SfuBroadcastQueuePressureHigh` | warning | media-platform | catalog queue bucket | depth below 128 for 10 min | `SFB-GATE-010/RB-SFB-QUEUE` |
| `SfuBroadcastDropsHigh` | warning | media-platform | catalog drop bucket | five-minute increase at or below 32 for 10 min | `SFB-GATE-010/RB-SFB-DROP` |
| `SfuBroadcastEgressP95High` | warning | media-platform | catalog egress bucket | p95 at or below 10 MB/s for 15 min | `SFB-GATE-010/RB-SFB-EGRESS` |
| `SfuBroadcastTurnP95High` | warning | network-platform | catalog TURN bucket | p95 at or below 10 MB/s for 15 min | `SFB-GATE-010/RB-SFB-TURN` |
| `SfuBroadcastFailoverP99High` | critical | runtime-platform | catalog failover bucket | p99 at or below 30000 ms for 10 min | `SFB-GATE-010/RB-SFB-FAILOVER` |
| `SfuBroadcastCapacityStop` | critical | runtime-platform | default stop ratio | utilization at or below 0.85 for 5 min | `SFB-GATE-010/RB-SFB-CAPACITY` |

## Read-only triage sequence

1. Confirm that the alert remains firing for its configured duration. Do not
   infer health from an absent series because the cohort may be suppressed.
2. Open the authenticated Angular route `/sfu-broadcast-operations` and select
   an authorized tenant, region or room scope.
3. Record only the stable reason code, aggregate state, gate state, bounded
   bucket and alert window. Do not copy diagnostic pseudonyms into tickets.
4. Correlate join, route, rekey, layer, queue/drop, egress/TURN, drain,
   failover and capacity panels. Do not query unregistered labels.
5. Escalate with the future `SFB-GATE-010` runbook identifier. Until that gate
   provides an executable procedure, do not improvise drain, revoke,
   kill-switch or rollback commands from a dashboard.
6. Close only after the configured clear condition has held for the full
   duration. Record missing browser or Grafana evidence as unverified.

## Operator command surface

The Angular surface keeps read and command paths separate. It never derives a
command room reference from a diagnostic pseudonym. Start, stop and preference
commands require a freshly entered room reference, expected CAS version,
checkbox acknowledgement and the typed phrase `FREIGEBEN`. The UI sends one
idempotency key and displays no effective transition before the Hub responds.

The Hub remains authoritative for RBAC, admission, consent, parent readiness,
capacity, kill switch, current version and final effective state. A rejected or
unknown response is fail-closed and must not be overridden in the browser.

## Explicit coverage gaps

The current OBS-001 catalog does not register dedicated node-health,
node-flap, reservation or privacy-scan metrics. Dashboard text panels expose
`sfu_observability_metric_not_registered`; no substitute query or invented
metric name is used. Consequently full OBS-006 acceptance and Grafana runtime
evidence remain blocked until those content-free metrics are registered,
instrumented and proven with valid source/run evidence.
