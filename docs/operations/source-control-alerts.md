# Source Control alerts, delivery and safe recovery

## Boundary

The Hub owns Source Control health, policy, task admission and recovery
decisions. Workers only execute assignments issued by the Hub. Alert handling
must not create a worker-to-worker control path or grant a worker permission to
activate, roll back, disable or purge an index.

The bounded Prometheus rules are in
`config/monitoring/source-control-alerts.yml`. The default Alertmanager config
in `docker/alertmanager/alertmanager.yml` has no outbound receiver. This is
intentional: a deployment must inject its reviewed, secret-backed receiver
configuration rather than committing a webhook, token or address.

## Alert index

| Alert | Severity | Signal | Clear condition |
|---|---|---|---|
| `SourceControlOperationalAlarm` | critical | bounded `reason_code` is firing for 2 minutes | firing gauge is zero and Hub health is healthy for 2 minutes |
| `SourceControlHealthDegraded` | warning | aggregate health is degraded for 5 minutes | degraded gauge is zero for 5 minutes |
| `SourceControlMetricPipelineMissing` | critical | aggregate health metric is absent for 5 minutes | health metric is present for 5 minutes |

Metrics and alerts must never contain tenant, project, actor, source, path,
URL, credential, payload or content values. The only per-alarm dimension is
the bounded operational `reason_code`.

## Read-only triage and recovery

1. Confirm the alert through Prometheus and Alertmanager. Treat an absent
   metrics pipeline as an incident, not as a healthy state.
2. Read the authenticated Hub endpoint
   `/api/source-control/v1/health`. Do not query workers directly.
3. Record only alert name, bounded reason code, aggregate counters and trace
   reference. Do not attach source content or credentials.
4. For `stale_source_threshold`, validate the connector and request refresh
   through the Hub. Clear only after the canonical source revision is current.
5. For `authorization_failure_threshold`, stop repeated attempts, validate
   grant scope and rotate an affected credential through the approved secret
   provider. Never place credential material in an alert or ticket.
6. For `blocked_jobs`, inspect Hub-owned job and lease state. Retry or cancel
   only through the canonical Hub task API.
7. For `artifact_hash_drift`, keep the revision quarantined, rebuild from the
   admitted immutable source revision and activate only by CAS after digest
   verification.
8. For `storage_pressure`, stop new admission first. Purge only tombstoned,
   retention-eligible artifacts through the approved lifecycle action.
9. For `metrics_adapter_failure`, `audit_adapter_failure` or a missing metric,
   restore the adapter before clearing the incident. Never disable the
   fail-closed release gate to hide the alarm.
10. Close the incident only after the rule's clear condition holds and a
    healthy Hub snapshot is present.

## Isolated delivery drill

Run:

```bash
python scripts/run-source-control-alert-drill.py
```

The drill uses `SourceControlHealthMonitor`,
`SourceControlHealthMetricsPublisher` and
`PrometheusSourceControlMetrics`. It binds an ephemeral receiver exclusively
to `127.0.0.1`, submits Alertmanager-compatible `firing` and `resolved`
notifications, and writes redacted deterministic evidence to
`artifacts/test-gates/source-control-alert-delivery-drill.json`.

This drill proves the local health-to-metric transition, webhook schema,
delivery acceptance and recovery sequence. It does not claim that a production
Alertmanager receiver, paging provider or organizational escalation path was
tested. Production delivery remains fail-closed and unverified until a
deployment supplies and exercises a reviewed receiver without exposing its
secret configuration.
