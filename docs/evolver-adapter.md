# Evolver Adapter

The Evolver integration is an optional Evolution provider plugin. It preserves
the hub-worker architecture: Ananta remains the hub-side control plane, while
Evolver is treated as an external analysis service behind the generic
`EvolutionEngine` SPI.

## Runtime Model

- The adapter lives in `plugins/evolver_adapter/`.
- The hub loads the plugin through `AGENT_PLUGIN_DIRS`/`AGENT_PLUGINS`.
- The adapter calls Evolver over HTTP and maps responses into generic
  `EvolutionResult` and `EvolutionProposal` objects.
- Provider-specific Evolver fields stay in `provider_metadata` and
  `raw_payload`; the core schema does not depend on Gene, Capsule or GEP terms.
- Validate and Apply remain capability- and policy-controlled. The first
  supported integration mode is analyze/proposal only.

## Configuration

The built-in defaults keep Evolver disabled:

```json
{
  "evolution": {
    "provider_overrides": {
      "evolver": {
        "enabled": false,
        "provider_name": "evolver",
        "base_url": null,
        "analyze_path": "/evolution/analyze",
        "timeout_seconds": 30,
        "default": false,
        "replace": true,
        "version": "unknown"
      }
    }
  }
}
```

For Compose deployments, enable it with environment variables on the hub:

```env
EVOLVER_ENABLED=1
EVOLVER_BASE_URL=http://evolver:8080
EVOLVER_ANALYZE_PATH=/evolution/analyze
EVOLVER_TIMEOUT_SECONDS=30
EVOLVER_DEFAULT=1
```

Start the optional Evolver service profile when an Evolver image is available:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml --profile evolution up
```

Set `EVOLVER_IMAGE` if the runtime image is hosted under a different name.

## Safety Boundaries

- Keep `evolution.apply_allowed=false` unless an explicit review and rollback
  process exists.
- Keep `evolution.require_review_before_apply=true` for all production-like
  environments.
- Use short HTTP timeouts. Provider outages must degrade discovery/analysis
  instead of blocking unrelated hub flows.
- Do not mount the Ananta workspace into the Evolver container unless a later
  reviewed Apply mode explicitly requires it.
- Avoid sharing secrets with Evolver. If provider authentication becomes
  necessary, pass a provider-specific token and redact it before persistence.
- Keep Evolver network access scoped to the Compose network or an explicit
  service URL. Do not use worker-to-worker routes.

## Failure Behavior

If the Evolver plugin is disabled or misconfigured, the hub starts normally and
the provider is not registered. If the provider is registered but unavailable,
the registry health endpoint reports degraded/unavailable state and analysis
requests fail through the normal Evolution service error path.
