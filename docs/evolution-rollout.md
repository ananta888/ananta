# Evolution Provider Rollout

Evolution starts in a fail-closed, proposal-first mode. The hub remains the
control plane for planning, trigger decisions, provider selection, validation
and future apply decisions.

## Phase 0: Disabled

- `evolution.enabled=false`
- No manual or automatic analysis should run.
- Use this mode for environments where provider connectivity or data handling
  has not been reviewed.

## Phase 1: Analyze Only

Recommended first production-like phase:

```json
{
  "evolution": {
    "enabled": true,
    "analyze_only": true,
    "manual_triggers_enabled": true,
    "auto_triggers_enabled": false,
    "validate_allowed": true,
    "apply_allowed": false,
    "require_review_before_apply": true
  }
}
```

Operators can call REST or MCP analysis tools. Runs and proposals are persisted,
audited and exposed through the task Evolution read model. Provider raw payloads
and Evolution context signals are redacted before storage or provider handoff.

For Evolver, keep the provider override restricted during this phase:

```json
{
  "evolution": {
    "provider_overrides": {
      "evolver": {
        "enabled": true,
        "base_url": "http://evolver:8080",
        "health_path": "/health",
        "allowed_hosts": ["evolver"],
        "force_analyze_only": true,
        "connect_timeout_seconds": 10,
        "read_timeout_seconds": 30,
        "max_response_bytes": 1048576,
        "retry_count": 1,
        "retry_backoff_seconds": 0.5
      }
    }
  }
}
```

In this mode Validate and Apply calls for Evolver remain blocked even when
global Evolution policy later permits those operations for other providers.

## Phase 2: Controlled Review

- Keep `apply_allowed=false`.
- Enable `auto_triggers_enabled=true` only after the failed-task E2E path is
  verified in the target environment.
- Use `validate_allowed=true` for provider or policy preflight checks.
- Keep Evolver `force_analyze_only=true`; only providers that explicitly expose
  and implement Validate should receive validation calls.
- Monitor `evolution_analyses_total`, `evolution_proposals_total` and
  `evolution_validations_total` for spikes and provider-specific failures.
- Monitor `evolution_provider_failures_total`,
  `evolution_provider_retries_total` and `evolution_provider_health_total`
  before increasing retry count or enabling automatic triggers.

## Phase 3: Validate / Apply Staging

Apply is present as an explicit second-stage endpoint and service method, but
it is disabled by default:

```json
{
  "evolution": {
    "apply_allowed": false,
    "require_review_before_apply": true
  }
}
```

Only enable `apply_allowed=true` in a reviewed staging environment where:

- the provider supports the `apply` capability,
- proposals have a review/approval workflow,
- rollback or artifact recovery is documented,
- audit logs and metrics are monitored,
- container and workspace boundaries are explicit.

Even when `apply_allowed=true`, `require_review_before_apply=true` blocks
review-required proposals until a later approval model clears that requirement.

## Phase 4: Future Controlled Apply

Future controlled apply should add:

- explicit approval records,
- artifact and patch provenance,
- rollback metadata,
- policy decisions tied to proposal risk,
- E2E tests for failed apply and rollback paths.

Do not introduce worker-to-worker orchestration for apply. Workers may execute
delegated work, but the hub owns the task queue, policy decision and audit path.
