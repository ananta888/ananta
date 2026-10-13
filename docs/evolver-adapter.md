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
        "health_path": null,
        "timeout_seconds": 30,
        "connect_timeout_seconds": 10,
        "read_timeout_seconds": 30,
        "max_response_bytes": 1048576,
        "retry_count": 1,
        "retry_backoff_seconds": 0.5,
        "allowed_hosts": ["evolver"],
        "force_analyze_only": true,
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
EVOLVER_HEALTH_PATH=/health
EVOLVER_TIMEOUT_SECONDS=30
EVOLVER_CONNECT_TIMEOUT_SECONDS=10
EVOLVER_READ_TIMEOUT_SECONDS=30
EVOLVER_MAX_RESPONSE_BYTES=1048576
EVOLVER_RETRY_COUNT=1
EVOLVER_RETRY_BACKOFF_SECONDS=0.5
EVOLVER_ALLOWED_HOSTS=evolver
EVOLVER_DEFAULT=1
```

Optional authentication can be configured without exposing values in provider
metadata:

```env
EVOLVER_BEARER_TOKEN=...
EVOLVER_HEADERS={"X-Evolver-Tenant":"team-a"}
```

Start the optional Evolver service profile when an Evolver image is available:

```bash
docker compose -f docker/old_way/docker-compose.base.yml -f docker/old_way/docker-compose.yml --profile evolution up
```

Set `EVOLVER_IMAGE` if the runtime image is hosted under a different name.

## Safety Boundaries

- Keep `evolution.apply_allowed=false` unless an explicit review and rollback
  process exists.
- Keep `evolution.require_review_before_apply=true` for all production-like
  environments.
- Use short HTTP timeouts. Provider outages must degrade discovery/analysis
  instead of blocking unrelated hub flows.
- Keep `force_analyze_only=true` for Evolver until Validate/Apply behavior is
  implemented and reviewed in the provider.
- Use `allowed_hosts`/`EVOLVER_ALLOWED_HOSTS` so an external provider URL must
  match an explicitly approved hostname before registration.
- Set separate connect/read timeouts and a bounded `max_response_bytes` limit
  for remote Evolver services.
- Keep retries conservative. Retry only transient failures such as timeouts,
  connection errors and 502/503-style HTTP responses.
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

Retries and health transitions are observable through
`evolution_provider_retries_total`, `evolution_provider_failures_total` and
`evolution_provider_health_total`.

API error responses for Evolution operations include a stable `data.error_code`
for client handling. External provider failures use codes such as
`provider_timeout`, `provider_connection_error`, `provider_http_error` and
`provider_invalid_response`. Analyze-only Evolver Validate/Apply calls return
`provider_operation_not_supported`.

## Response Contract

The adapter accepts one proposal source field per response: `proposals`,
`improvements`, `candidates` or `events`. Supplying more than one source is
treated as an ambiguous provider contract and fails before mapping.

Proposal type, risk and status values are normalized in the adapter:

- `gene` maps to generic `improvement`.
- `capsule` and `repair` map to generic `repair`.
- `gep_prompt` and `prompt` map to generic `prompt`.
- risk values such as `minimal` and `moderate` normalize to `low` and `medium`.
- status values such as `success`, `ok` and `done` normalize to `completed`.

The original Evolver kind and source field remain available as bounded
`provider_metadata`; the generic core model stays provider-neutral.

See `docs/evolution-rollout.md` for the phased rollout from disabled to
analyze-only, controlled review and future apply staging.

## Pinned runtime contract (SEH-037, partial)

The dedicated bridge image fetches the immutable v1.94.0 commit
[`b34ccbcd9a07dc252b080b80e0c587734554f004`](https://github.com/EvoMap/evolver/tree/b34ccbcd9a07dc252b080b80e0c587734554f004)
instead of cloning `main`. `services/evolver_bridge/runtime-contract.json` binds
version, declared SPDX license, exact LICENSE bytes, dependency lock and protocol.
Both bridge and upstream dependencies use `npm ci --ignore-scripts`; the image
runs as the non-root `node` user. Standard CA certificates are installed for
verified HTTPS fetches; no TLS verification bypass is used.

Build and `/health` validate the binding and refuse mismatched or missing code.
The health response exposes public runtime identity metadata; the Python adapter
preserves only its scalar allowlisted fields. Upgrades require a reviewed contract
change and compatibility testing. Pinning the current v2 entrypoint would silently
break the old CLI invocation: its index module exports the CLI package instead.
The upstream v1 license is package-declared `GPL-3.0-or-later`; this is recorded
metadata, not a separate release or legal-admission grant.

The deterministic dependency inventory in
`artifacts/domain/evolver-runtime.dependencies.v1.json` covers locked production
npm dependencies. It intentionally identifies optional packages and unknown
license metadata. OS/base-image dependencies and full image SBOM/notice export
remain outstanding; the inventory is not a complete container SBOM.

The analyze subprocess uses a closed environment, an isolated HOME/workspace,
explicit single `run` invocation and a bounded timeout. Credentials and external
EvoMap Hub settings are not inherited. Temporary workspace cleanup runs in
`finally`. Stdout results are explicitly labelled degraded compatibility output.
This preserves the old proposal-only HTTP contract while structured GEP and
project snapshot admission remain separate work (SEH-038/039).

Runtime-identity smoke, fully offline and headless:

```sh
ANANTA_EVOLVER_PINNED_IMAGE=<locally-built-image> python -m pytest \
  tests/evolver_bridge/test_runtime_container.py --timeout=45
```

The smoke checks real non-root/read-only runtime identity and adapter metadata.
It does not certify the full analyze/structured-GEP path or a production release.
Local build/test observations have no pre-reserved production Registry run and
remain technical observations. Full compatibility, rollback to a previously
admitted image digest and signed image/SBOM provenance remain open.

Runtime identity checks live in their own module (SRP); transport metadata is
projected through a narrow allowlist. The existing persistent sidecar subprocess
path still needs stronger per-run containment and enforced egress isolation;
clearing environment URLs is not a network sandbox. These preserved infrastructure
boundaries must be fixed before productive self-modification is enabled.

## Scoped Git workspaces (SEH-038, partial)

Every CLI invocation now receives a new Git repository. An optional
`context.constraints.repository_snapshot` uses this additive contract:

```json
{
  "schema": "ananta.evolver-workspace.v1",
  "origin_revision": "<40-character immutable origin commit>",
  "tenant_id": "<Hub-authorized tenant>",
  "project_id": "<Hub-authorized project>",
  "files": {"src/example.py": "<admitted UTF-8 text>"},
  "content_digest": "<Hub canonical digest of origin_revision, tenant_id, project_id, files>",
  "execution_revision": "<optional expected filtered-workspace commit>"
}
```

The file map is limited to 128 files and 512 KiB. Absolute paths, traversal,
Git internals, credential/secret directories and environment/key files fail
admission before execution. Files are created as regular text files; no host
path, active checkout, external Git database or symlink is mounted. The filtered
execution commit has deterministic author/date/content and is distinct from
the supplied origin revision. Context digests use the Hub's canonical JSON,
including Unicode escaping and sorted keys.

Without a snapshot, compatibility mode creates a **synthetic context-only**
repository; it never claims to have checked out or analyzed the real project.
Worker metadata always declares `evidence_status=unverified`; a checksum and
caller-provided revision are not Registry evidence. Source issuance remains
Hub-owned. The normal Python adapter continues withholding raw file maps until
a Hub-governed source-projector is implemented.

Workspace preparation is isolated in `workspace.js` (SRP). Setup/Git failures
and the analyze success/failure/timeout path clean up temporary state. Headless
Node tests perform real Git diff operations, reject unsafe paths and compare
independent workspaces; the real offline sidecar test exercises bounded analyze
and verifies no run directories remain. These are technical/test observations.

**Open:** registered immutable Hub source admission/projector, mandatory origin-
to-snapshot verification, full per-run containment/egress and structured GEP
compatibility. This partial implementation cannot alone authorize production
repository analysis or release promotion.
