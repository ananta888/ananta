# ExecutionEnvelope Contract

**Track:** EW-T003  
**Date:** 2026-05-10

The `ExecutionEnvelope` is the **only** way the Hub communicates delegated work to a worker.  
No model call, tool call, shell call, file write, network access, MCP access, memory write, or subworker spawn may occur before the envelope is validated.

---

## Schema

```python
# Canonical Pydantic model — see worker/core/execution_envelope.py (EW-T007)

class ExecutionEnvelope(BaseModel):
    task_id: str                          # non-empty; uniquely identifies this execution
    goal_id: str | None                   # optional linkage to parent goal
    actor_ref: str                        # Hub identity that signed this envelope
    capability_grant: CapabilityGrant     # immutable list of allowed capability classes
    context_envelope_ref: str             # opaque ref to ContextEnvelope; must be non-empty
    allowed_operations: list[str]         # explicit whitelist of allowed operation ids
    denied_operations: list[str]          # explicit blacklist (takes precedence over allowed)
    model_policy: ModelPolicy             # allowed providers, models, cloud flag
    tool_policy: ToolPolicy               # allowed tool ids, approval overrides
    approval_refs: list[ApprovalRef]      # pre-obtained approvals for confirm_required ops
    audit_correlation_id: str             # links all trace events for this execution
    trace_parent_id: str | None           # W3C traceparent for distributed tracing
```

---

## Validation rules

| Field | Fail-closed rule |
|---|---|
| `task_id` | Empty → `invalid_request` |
| `capability_grant` | Missing or empty → `missing_capability`; execution denied |
| `context_envelope_ref` | Empty → `context_missing`; execution denied |
| `model_policy.allowed_providers` | Empty → use worker default only if `cloud_allowed=false` |
| `model_policy.cloud_allowed=false` | Any cloud provider call → `provider_blocked` |
| `tool_policy.allowed_tool_ids` | Tool not in list → `tool_unavailable` |
| `denied_operations` | Operation in list → `denied_operation` (overrides `allowed_operations`) |
| `approval_refs` | Operation marked `confirm_required` but no matching ref → `approval_missing` |
| unknown capability class | → `missing_capability` |

---

## Sub-schemas

### CapabilityGrant

```python
class CapabilityGrant(BaseModel):
    capabilities: list[str]    # from canonical vocabulary (EW-T005)
    snapshot_hash: str         # sha256 of sorted capabilities; used in TraceBundle
```

### ModelPolicy

```python
class ModelPolicy(BaseModel):
    allowed_providers: list[str]   # e.g. ["ollama", "lmstudio"]
    preferred_model: str | None
    cloud_allowed: bool = False    # if False, cloud providers are blocked
    max_tokens: int | None
```

### ToolPolicy

```python
class ToolPolicy(BaseModel):
    allowed_tool_ids: list[str]
    approval_overrides: dict[str, str]   # tool_id → "allow" | "confirm_required" | "deny"
```

### ApprovalRef

```python
class ApprovalRef(BaseModel):
    ref_id: str
    operation: str      # capability class or operation id this approval covers
    granted_at: float   # epoch seconds; stale check is enforcement responsibility of preflight
    granted_by: str
```

---

## Positive example

```json
{
  "task_id": "task-abc-123",
  "goal_id": "goal-xyz-456",
  "actor_ref": "hub:ananta-hub-v1",
  "capability_grant": {
    "capabilities": ["code_read", "patch_propose"],
    "snapshot_hash": "e3b0c44298fc..."
  },
  "context_envelope_ref": "ctx:abc123",
  "allowed_operations": ["read_file", "propose_patch"],
  "denied_operations": [],
  "model_policy": {"allowed_providers": ["ollama"], "cloud_allowed": false},
  "tool_policy": {"allowed_tool_ids": ["read_file", "propose_patch"], "approval_overrides": {}},
  "approval_refs": [],
  "audit_correlation_id": "audit:corr-001",
  "trace_parent_id": null
}
```

## Negative example (fails at preflight)

```json
{
  "task_id": "",
  "capability_grant": null,
  "context_envelope_ref": ""
}
```
→ `invalid_request`: empty `task_id`, `missing_capability`: null grant, `context_missing`: empty ref.
