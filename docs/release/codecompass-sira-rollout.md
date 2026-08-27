# CodeCompass SIRA rollout and rollback

Default is `CODECOMPASS_SIRA_MODE=off`.

1. Build and verify enrichment/statistics while online expansion remains off.
2. Enable `shadow`; baseline remains authoritative. Observe quality, p95,
   tokens, cost, rejection reasons and snapshot mismatch for at least one agreed
   window.
3. Enable a small `on_demand` or `preferred` canary only after bound benchmark,
   security and non-regression gates pass.
4. Expand preferred traffic gradually. `required` is reserved for workflows
   whose contract explicitly accepts typed unavailability.

Automatic stop conditions include any scope/security regression, mixed snapshot,
exact-query regression, unexplained SourceRef loss, error-rate breach, p95/token
budget breach or quality below the bound gate. Paper/README results are not a
rollout gate.

The primary kill switch is `CODECOMPASS_SIRA_MODE=off`. The reranker has the
separate `CODECOMPASS_SIRA_RERANKER_ENABLED=0` switch. Offline generation jobs
must be stopped through the Hub queue/policy; disabling them does not remove the
last verified index. Roll back by selecting the previous active base/delta
pointer and existing Hybrid profile. No rebuild is required.

Shadow/canary telemetry must not duplicate query or path contents. Index/model
incompatibility, partial deltas and model outage are acceptance tests before
preferred rollout. Owner approval and observation windows are deployment-local
decisions and must be recorded with the release evidence.
