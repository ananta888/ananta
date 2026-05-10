# WorkerResult Contract

**Track:** EW-T004  
**Date:** 2026-05-10

`WorkerResult` is the **only** return value from a worker execution.  
Plain text-only responses are not accepted for task execution endpoints.

---

## Schema

```python
# Canonical Pydantic model — see worker/core/execution_envelope.py (EW-T007)

class WorkerResult(BaseModel):
    task_id: str
    status: WorkerResultStatus          # see status vocabulary below
    summary: str                        # human-readable; max 500 chars
    artifacts: list[ArtifactRef]        # all meaningful outputs
    trace_bundle: TraceBundle           # always present, even on denial/error
    policy_observations: list[str]      # reason codes from preflight and execution
    follow_up_tasks: list[FollowUpTask] # proposed follow-ups (Hub decides whether to create)
    warnings: list[str]                 # non-fatal issues observed
    degraded_state: DegradedState | None
    no_side_effects_confirmed: bool     # True only when worker is certain no mutation occurred
```

---

## Status vocabulary

| Status | Meaning |
|---|---|
| `success` | All operations completed successfully; all artifacts published |
| `partial_success` | Some operations succeeded; see `warnings` and `policy_observations` |
| `denied` | Preflight or capability check rejected the request |
| `needs_approval` | Execution requires an `ApprovalRef` that is absent; no mutation occurred |
| `failed` | Execution failed after all retries; see `policy_observations` |
| `degraded` | Completed with reduced capability; see `degraded_state` |
| `invalid_request` | Envelope is malformed; execution never started |

---

## Sub-schemas

### ArtifactRef

```python
class ArtifactRef(BaseModel):
    artifact_id: str
    kind: str            # e.g. "patch", "plan", "command_output", "test_result"
    provenance: str      # task_id + step that produced it
    summary: str | None
```

### TraceBundle

```python
class TraceBundle(BaseModel):
    correlation_id: str
    capability_snapshot_hash: str   # must match CapabilityGrant.snapshot_hash
    events: list[TraceEvent]        # ordered; append-only during execution
```

### TraceEvent

```python
class TraceEvent(BaseModel):
    ts: float           # epoch seconds
    event_type: str     # e.g. "preflight_allow", "tool_call", "model_call", "denied"
    reason_code: str | None
    payload: dict       # tool_id / model / operation / etc.
```

### DegradedState

```python
class DegradedState(BaseModel):
    reason: str
    capabilities_unavailable: list[str]
    fallback_used: str | None
```

### FollowUpTask

```python
class FollowUpTask(BaseModel):
    title: str
    description: str
    capability_hint: str | None   # capability class the follow-up will need
```

---

## Invariants

- `TraceBundle` is always returned — even when `status = "denied"` or `"invalid_request"`.
- `no_side_effects_confirmed = True` only when the worker can prove no mutation occurred.
- All side effects must appear as `ArtifactRef` entries; hiding them in `summary` text is not allowed.
- `follow_up_tasks` are proposals only; the Hub decides whether to create them.
