# Worker Responsibility Matrix

**Track:** EW-T002  
**Date:** 2026-05-10

Defines what the worker may decide locally versus what must always originate from the Hub.  
Tests and code should reference this matrix to detect architecture drift.

---

## Guiding rule

> The worker may choose **how** to carry out an already-approved operation inside an `ExecutionEnvelope`.  
> The worker must **never** decide **what** is allowed, **who** approved it, or **where** results go.

---

## Decision matrix

| Decision | Owner | Worker behaviour if Hub input is missing |
|---|---|---|
| Goal decomposition into tasks | Hub | Fail closed — worker does not re-plan |
| Task assignment and routing | Hub | Fail closed — worker does not self-assign |
| Which tools are allowed | Hub (`ToolPolicy` in envelope) | Deny any tool not in policy |
| Which LLM provider to use | Hub (`ModelPolicy` in envelope) | Use first allowed provider; deny cloud if not listed |
| Approval for mutation-capable operations | Hub (`ApprovalRef` in envelope) | Return `needs_approval`; do not execute |
| Context content and sensitivity budget | Hub (`ContextEnvelope`) | Use only provided context; do not fetch outside scope |
| Filesystem scope (read / write paths) | Hub (`FilesystemScope`) | Deny any access outside declared scope |
| Network scope (allowed hosts) | Hub (`NetworkScope`) | Deny any outbound call to unlisted host |
| Artifact publication target | Hub | Emit artifact in `WorkerResult`; Hub decides where to store it |
| Audit log destination | Hub (TraceBundle forwarded) | Always emit TraceBundle; never suppress or filter it |
| Capability escalation mid-execution | Not allowed | Deny; capability snapshot is immutable per execution |
| Subworker spawn | Hub (`subworker_spawn` capability) | Deny if capability absent |
| Scheduled job creation | Hub (`cron_schedule` capability) | Deny if capability absent |
| Memory write | Hub (`memory_write` capability + optional approval) | Deny if capability absent or approval missing |
| MCP tool call | Hub (`mcp_call` capability per tool) | Deny if capability absent |
| Skill activation or installation | Hub approval required | Propose only; never auto-activate |

---

## What the worker **may** decide locally

Within an already-approved `ExecutionEnvelope`, the worker may:

- Choose which file to read first (within `FilesystemScope`).
- Choose prompt phrasing for the model (within context budget).
- Choose retry count for transient network errors (within `ModelPolicy` limits).
- Choose output format for internal steps (must still conform to expected artifact schema).
- Choose which allowed tool to call first (within `ToolPolicy`).
- Choose execution order of independent substeps.
- Compress context to stay within the token budget defined in `ContextEnvelope`.

---

## Drift detection

Any code path that grants a new capability, expands a scope, calls a tool, writes memory, or invokes a shell command must be traceable to a preflight check that validates the current `ExecutionEnvelope`.  
Tests for EW-T008 (preflight gate) and EW-T012 (bypass regression) enforce this invariant.
