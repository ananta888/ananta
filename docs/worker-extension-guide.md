# Worker Extension Implementation Guide

This guide explains how to extend or integrate with the Ananta governed worker execution layer.
It covers the execution contract, security invariants, and skeleton code for common extension patterns.

## Architecture overview

The governed worker follows a strict Hub/Worker split:

- **Hub (Control Plane)**: decides capabilities, provider, context scope, approval requirements.
  Creates and signs the `ExecutionEnvelope`.
- **Worker (Execution Plane)**: executes only what the envelope permits.
  Never grants itself additional authority.

```
Hub                          Worker
 │                            │
 ├─ build ExecutionEnvelope   │
 ├─ sign capability snapshot  │
 └─────────────────────────►  │
                              ├─ PreflightGate.check()
                              ├─ ContextScanner.scan()
                              ├─ Execute within envelope
                              ├─ Produce artifacts
                              ├─ ArtifactEnforcer.check()
                              └─ Return WorkerResult
```

## ExecutionEnvelope

Every task starts with an `ExecutionEnvelope`. Workers must never create envelopes themselves.

```python
from worker.core.execution_envelope import (
    ExecutionEnvelope, CapabilityGrant, ModelPolicy, ToolPolicy, ApprovalRef
)

# Hub creates this:
envelope = ExecutionEnvelope(
    task_id="task-abc",
    actor_ref="hub:my-hub",
    capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
    context_envelope_ref="ctx:123",
    audit_correlation_id="audit:xyz",
    model_policy=ModelPolicy(
        cloud_allowed=False,
        allowed_providers=["ollama", "lmstudio"],
    ),
    tool_policy=ToolPolicy(allowed_tool_ids=["read_file", "list_files"]),
)
```

Capability snapshot hash is computed automatically and must be verified before execution.

## Preflight check

Always run `PreflightGate.check()` before any work. It is fail-closed: unknown = denied.

```python
from worker.core.preflight import PreflightGate

gate = PreflightGate()
result = gate.check(
    envelope,
    provider_id="ollama",
    tool_id="read_file",
    operation="planning",
    task_kind="task",
)
if not result.allowed:
    return WorkerResult.denied(
        reason_code=result.reason_code,
        detail=result.detail,
        trace_bundle=...,
    )
```

## Adding a new capability

1. Add the capability name to `KNOWN_CAPABILITY_CLASSES` in `execution_envelope.py`.
2. If it requires approval, add it to `CONFIRM_REQUIRED_CAPABILITIES`.
3. Add it to `CAPABILITY_ARTIFACT_MAP` in `artifact_enforcer.py` if it must produce an artifact.
4. Declare it in `capability-vocabulary.md` with risk class and side effects.

Example — adding `diagram_generate`:

```python
# execution_envelope.py
KNOWN_CAPABILITY_CLASSES = frozenset({
    ...,
    "diagram_generate",   # produces SVG/PNG, no side effects
})

# artifact_enforcer.py
CAPABILITY_ARTIFACT_MAP["diagram_generate"] = ["verification_artifact"]

# capability-vocabulary.md — add entry:
# | diagram_generate | medium | none | diagram_artifact |
```

## Adding a new external adapter

External adapters (Hermes, OpenCode, MCP) must implement the same three-method contract:

```python
from worker.core.execution_envelope import ExecutionEnvelope
from worker.core.external_adapters import AdapterResult

class MyAdapter:
    def check_policy(self, envelope: ExecutionEnvelope) -> tuple[bool, str]:
        """Return (True, "") if policy satisfied; (False, reason_code) otherwise."""
        if not envelope.has_capability("my_capability"):
            return False, "missing_capability"
        return True, ""

    def prepare_context(self, blocks, *, cloud_allowed: bool):
        """Strip sensitive context blocks when cloud_allowed=False."""
        from worker.core.context_resolver import ContextSensitivity, CLOUD_BLOCKED_SENSITIVITIES
        allowed, redacted = [], []
        for block in blocks:
            if not cloud_allowed and block.sensitivity in CLOUD_BLOCKED_SENSITIVITIES:
                redacted.append(block.origin_id)
            else:
                allowed.append(block)
        return allowed, redacted

    def parse_response(self, raw_text: str, *, task_id: str) -> AdapterResult:
        """Sanitize output and extract artifacts."""
        from worker.core.sanitizer import sanitize
        clean = sanitize(raw_text)
        # parse artifacts from clean output...
        return AdapterResult(allowed=True, sanitized_output=clean, artifacts=[])
```

Security requirements for adapters:
- Always call `check_policy()` before processing responses.
- Always sanitize output through `OutputSanitizer` before returning.
- Reject success responses without structured artifacts (use `AdapterTrustBoundary`).
- Never write directly to the file system — return `PatchArtifact` for file changes.

## Registering a new tool

```python
from worker.core.tool_registry import WorkerToolEntry, ResourceLimits

entry = WorkerToolEntry(
    tool_id="search_index",
    kind="local",
    capability_classes=["research"],
    risk_class="low",
    description="Search the project's search index",
    input_schema={"query": "string", "max_results": "int"},
    output_schema={"results": "list[SearchResult]"},
    side_effects=[],  # read-only
    resource_limits=ResourceLimits(
        timeout_seconds=10,
        max_output_chars=4096,
        max_artifact_bytes=0,
        max_files_touched=0,
    ),
)
registry.register(entry)
```

## Artifact-first result contract

Every capability in `CAPABILITY_ARTIFACT_MAP` must produce at least one matching artifact.
Free-text-only results are rejected.

```python
from worker.core.artifact_enforcer import ArtifactEnforcer

enforcer = ArtifactEnforcer()
result = enforcer.check(
    capabilities_used=["planning"],
    artifacts=[{"kind": "plan_artifact", "artifact_id": "plan-001"}],
    summary="Plan plan-001: Fix the config parser",
)
if not result.compliant:
    raise RuntimeError(f"Artifact enforcement failed: {result.violations}")
```

## Producing a WorkerResult

```python
from worker.core.execution_envelope import WorkerResult, WorkerResultStatus

result = WorkerResult(
    status=WorkerResultStatus.success,
    summary=enforcer.build_summary_with_refs("Task complete", artifacts),
    artifacts=artifacts,
    trace_bundle=trace.as_dict(),
)
```

For failures use the factory methods:

```python
WorkerResult.denied(reason_code="MISSING_CAPABILITY", detail="...", trace_bundle=...)
WorkerResult.needs_approval(operation="shell_execute", trace_bundle=...)
WorkerResult.invalid(detail="Schema validation failed", trace_bundle=...)
```

## Audit events

Emit audit events for all sensitive steps. All events require `correlation_id`.

```python
from worker.core.diagnostics import AuditEmitter

emitter = AuditEmitter()
emitter.emit_preflight("allow", correlation_id=envelope.audit_correlation_id,
                       reason_code=None, task_id=envelope.task_id,
                       actor_ref=envelope.actor_ref)
# ... do work ...
emitter.emit("patch_apply", correlation_id=envelope.audit_correlation_id,
             reason_code=None, task_id=envelope.task_id,
             artifact_id=artifact.artifact_id)
events = emitter.flush()  # persist to sink
```

## Tracing

```python
from worker.core.trace_v2 import TraceBundleV2, ExecutionOutcome

trace = TraceBundleV2.from_envelope(
    envelope,
    goal_id="goal-abc",
    model_id="ollama/llama3",
)
# ... execution ...
trace.finish(ExecutionOutcome.success)
return WorkerResult(
    ...,
    trace_bundle=trace.as_dict(),
)
```

## Feature flags

Feature flags are Hub-controlled. Workers read but never set flags.

```python
from worker.core.feature_flags import build_default_registry

flags = build_default_registry()
flags.apply_hub_config(hub_flag_config)  # from envelope or config service

if flags.is_enabled("enable_context_scanner"):
    scan_result = scanner.scan(...)
```

## Security invariants (never violate)

1. **Fail-closed**: unknown capability, provider, or tool → deny. Never silently allow.
2. **No self-grant**: worker never creates or modifies its own `ExecutionEnvelope`.
3. **Cloud-blocked by default**: `ModelPolicy.cloud_allowed` defaults to `False`.
4. **Secrets never logged**: all output passes through `OutputSanitizer` before any log, audit, or artifact.
5. **Artifacts required**: capabilities in `CAPABILITY_ARTIFACT_MAP` must produce matching artifacts.
6. **Injection scanning**: all external context blocks scanned before use.
7. **Subworker scope containment**: child capabilities must be a strict subset of parent capabilities.
8. **Approval for sensitive ops**: `CONFIRM_REQUIRED_CAPABILITIES` always need an `ApprovalRef`.
