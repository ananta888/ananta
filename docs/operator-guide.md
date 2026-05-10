# Ananta Governed Executor — Operator Guide

This guide covers deployment, configuration, monitoring, and troubleshooting of the
Ananta governed worker execution layer.

## Prerequisites

- Python 3.11+
- At least one local LLM provider running (Ollama recommended for development)
- Ananta Hub instance (or mock Hub for local development)

## Quick start (local development)

```bash
# Install dependencies
pip install -e ".[dev]"

# Run all worker tests
pytest tests/ -x -q

# Run security regression suite only
pytest tests/test_security_regression.py -v

# Run smoke tests
pytest tests/test_worker_smoke.py -v
```

## Configuration

### ExecutionEnvelope

All worker tasks require an `ExecutionEnvelope` signed by the Hub. Operators configure:

| Field | Description | Default |
|-------|-------------|---------|
| `capability_grant` | Allowed capability classes | required |
| `model_policy.cloud_allowed` | Allow cloud LLM providers | `false` |
| `model_policy.allowed_providers` | Explicit provider allowlist | `[]` (allow all local) |
| `tool_policy.allowed_tool_ids` | Allowed tool IDs | `[]` (allow all) |
| `denied_operations` | Explicitly blocked operations | `[]` |
| `max_runtime_seconds` | Hard timeout (seconds) | `300` |

### Provider registry

Configure local providers in `worker/core/provider_registry.py` or via Hub config:

```python
from worker.core.provider_registry import WorkerProviderRegistry, ProviderEntry, ProviderKind

registry = WorkerProviderRegistry()
registry.register(ProviderEntry(
    provider_id="my-ollama",
    kind=ProviderKind.local,
    base_url="http://localhost:11434",
    priority=10,
))
```

Default providers (registered automatically):
- `ollama` — http://localhost:11434
- `lmstudio` — http://localhost:1234
- `openai_compatible` — http://localhost:8080
- `local_mock` — in-process mock (testing only)

Cloud providers (`openai`, `anthropic`, `azure_openai`) are registered but blocked by
default (`cloud_allowed=False`).

### Feature flags

Feature flags are Hub-controlled. Apply them via `FeatureFlagRegistry.apply_hub_config()`:

```python
from worker.core.feature_flags import build_default_registry

flags = build_default_registry()
flags.apply_hub_config({
    "enable_scheduled_jobs": True,    # enable scheduled job processing
    "strict_tool_policy": True,       # enforce ToolPolicy.allowed_tool_ids strictly
})
```

Security flags are **on by default** and should never be disabled in production:
- `require_execution_envelope`
- `require_capability_snapshot`
- `require_artifact_first`
- `enable_context_scanner`
- `enable_adapter_trust_boundary`
- `enable_audit_emitter`
- `block_cloud_by_default`

## Migration stages

The worker supports three migration stages for transitioning from legacy bare-mode operation:

| Stage | Description | Flag state |
|-------|-------------|------------|
| `legacy` | Bare mode strings, no envelope | `require_execution_envelope=false`, `legacy_envelope_adapter_allowed=false` |
| `compatibility` | `LegacyEnvelopeAdapter` wraps bare modes | `require_execution_envelope=false`, `legacy_envelope_adapter_allowed=true` |
| `governed` | Full `ExecutionEnvelope` required | `require_execution_envelope=true` |

**Recommended migration path:**
1. Deploy with `compatibility` stage — existing integrations continue to work.
2. Update integrations to send full `ExecutionEnvelope`.
3. Flip `require_execution_envelope=true` to enforce governed stage.
4. Set `legacy_envelope_adapter_allowed=false` to remove fallback.

## Monitoring

### Audit events

`AuditEmitter` buffers events in memory; flush them to your persistent sink:

```python
events = audit_emitter.flush()
for event in events:
    your_audit_sink.write(event)
```

Auditable event types:
- `preflight_allow` / `preflight_denied` — task gate decisions
- `approval_required` / `approval_consumed` — approval lifecycle
- `policy_denied` — policy enforcement blocks
- `shell_execute`, `patch_apply`, `memory_write` — sensitive operations
- `subworker_spawn`, `cron_schedule` — resource creation
- `artifact_publish`, `provider_call`, `mcp_call` — external calls
- `context_blocked`, `injection_blocked` — security events
- `capability_snapshot_mismatch` — tamper detection

### Diagnostics endpoint

```python
from worker.core.diagnostics import WorkerDiagnosticsBuilder

builder = WorkerDiagnosticsBuilder()
diag = builder.build(
    worker_id="worker-001",
    version="1.2.0",
    runtime_mode="local",
    tool_registry=tool_registry,
    provider_registry=provider_registry,
    skill_registry=skill_registry,
    envelope=current_envelope,
)
# diag.as_dict() is safe to expose — no secrets
```

### Trace bundles

Every task produces a `TraceBundleV2` regardless of outcome. Store for audit and debugging:

```python
trace.finish(ExecutionOutcome.success)
bundle = trace.as_dict()
# bundle contains: execution_id, task_id, outcome, timestamps, capability_hash
# Raw prompts and responses are NEVER included
```

## Approval workflows

Operations in `CONFIRM_REQUIRED_CAPABILITIES` require an `ApprovalRef` before execution:
`patch_apply`, `shell_execute`, `memory_write`, `mcp_call`, `subworker_spawn`, `cron_schedule`

### Pre-approved (automated pipelines)

Include approval refs in the `ExecutionEnvelope`:

```python
ExecutionEnvelope(
    ...,
    approval_refs=[
        ApprovalRef(
            ref_id="approval-001",
            operation="shell_execute",
            granted_at=time.time(),
            granted_by="hub:pipeline-001",
        )
    ],
)
```

### Headless scheduled jobs

For unattended scheduled jobs, use `HeadlessApprovalPolicy`:

```python
from worker.core.scheduled_job import HeadlessApprovalPolicy, ApprovalMode

policy = HeadlessApprovalPolicy()

# Check before spawning:
result = policy.check_can_run_headless(contract, required_operations=["patch_apply"])
if not result.allowed:
    # emit blocked job_run_artifact and stop
```

## Security hardening checklist

- [ ] `cloud_allowed=False` in all production `ModelPolicy` instances
- [ ] `ContextScanner` enabled (flag `enable_context_scanner=true`)
- [ ] `AdapterTrustBoundary` enabled (flag `enable_adapter_trust_boundary=true`)
- [ ] Audit events flushed to durable sink on every task
- [ ] `ArtifactEnforcer` checked before accepting `WorkerResult`
- [ ] No raw secrets in logs — `OutputSanitizer` applied to all provider responses
- [ ] Subworker depth and fan-out limits configured (`max_depth=3`, `max_fanout=8`)
- [ ] All provider credentials in `CredentialStore`, not in environment variables or logs
- [ ] Scheduled jobs use `HeadlessApprovalPolicy`, not interactive approval

## Troubleshooting

### Task denied with `MISSING_CAPABILITY`

The requested operation is not in the `capability_grant`. Check the envelope's
`capability_grant.capabilities` against what the worker is attempting.

### Task denied with `PROVIDER_BLOCKED`

`cloud_allowed=False` and the selected provider is a cloud provider.
Either use a local provider or explicitly set `cloud_allowed=True` with Hub authorization.

### `capability_snapshot_mismatch`

The capability set was tampered between signing and execution.
Check for middleware that modifies envelopes. Reject the task and audit.

### Free-text result rejected

A capability required a structured artifact but none was produced.
Check that the adapter's `parse_response()` correctly extracts artifacts from the LLM output.

### `approval_missing` for headless job

The scheduled job contract uses `confirm_required` mode but has no matching
`pre_approved_ref_ids`. Add the ref ID to the contract or switch to `pre_approved` mode.

### Context injection blocked

`ContextScanner` detected a suspicious pattern in an incoming context block.
Review the context source. Blocked blocks are replaced by safe stubs — execution continues.
