# CodeCompass SIRA rollout and rollback

Default is `CODECOMPASS_SIRA_MODE=off`.

`CodeCompassSiraRolloutService` is the Hub-owned state machine. A verified
benchmark report that passes the server-owned policy starts `shadow`
automatically. Redacted observations advance the persisted state through
`shadow -> canary -> preferred` after the configured minimum counts. Canary
assignment hashes the Hub scope plus request ID into a stable basis-point
bucket, so retries receive the same selection and no client can request the
candidate path. Shadow is always non-effecting.

The controller's `retrieval_profile()` projection is the sole Hub-owned input
that selects the profiled Worker channel. It carries a typed rollout decision.
The Worker rejects a profiled SIRA call without that decision, forces `shadow`
to return the baseline, and treats the static `off` setting as a final local
kill switch even if a stale Hub projection says `preferred`.

Any security, scope or exact-query regression, incompatible index, partial
delta, unavailable model, failed request, quality regression or latency/token/
cost breach switches the scope to `off` atomically. State and observation IDs
are persisted in SQLite; repeated observations are idempotent. A policy digest
change invalidates the prior rollout fail-closed. The static
`CODECOMPASS_SIRA_MODE=off` and
`CODECOMPASS_SIRA_ONLINE_EXPANSION_ENABLED=false` independently remove online
SIRA from the hot path. `CODECOMPASS_SIRA_OFFLINE_ENRICHMENT_ENABLED=false`
rejects new enrichment/sync/compaction operations without preventing an online
rollback, while `CODECOMPASS_SIRA_RERANKER_ENABLED=false` disables only the
reranker. This permits offline index preparation while online selection remains
off.

Automatic stop conditions include any scope/security regression, mixed snapshot,
exact-query regression, unexplained SourceRef loss, error-rate breach, p95/token
budget breach or quality below the bound gate. Paper/README results are not a
rollout gate.

The primary kill switch is `CODECOMPASS_SIRA_MODE=off`. The reranker has the
separate `CODECOMPASS_SIRA_RERANKER_ENABLED=0` switch. Offline generation jobs
must be stopped through the Hub queue/policy; disabling them does not remove the
last verified index. Roll back by selecting the previous active base/delta
pointer and existing Hybrid profile. No rebuild is required.

Shadow/canary telemetry must not contain query or path contents. It consists
only of observation ID, stage, success, quality delta, latency, tokens, cost and
closed regression/compatibility flags. Index/model incompatibility, partial
deltas and model outage are automatic acceptance tests before preferred
rollout. No human approval or interactive test step is required. Real deployed
evidence still needs actually supplied source/run identifiers and is never
manufactured from the deterministic fixture.
