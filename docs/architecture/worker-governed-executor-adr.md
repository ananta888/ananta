# ADR: Ananta Worker as Governed Agent Executor

**Status:** Accepted  
**Date:** 2026-05-10  
**Track:** EW-T001 — extend_worker_hermes_like_governed_executor

---

## Context

The Ananta worker must grow into a capable, Hermes-like agent executor: broad tool registry, multi-provider LLM routing, persistent memory, skills, context compression, subworker delegation, scheduled jobs, MCP integration, and OpenAI-compatible surfaces.

At the same time, Ananta's control plane is the **Hub**. The worker must not become an autonomous controller, invent its own policy, or bypass Hub governance.

This ADR locks the boundary between the two planes and maps every Hermes-like capability onto a Hub-owned equivalent.

---

## Decision

### 1. Hub is the sole control plane

The Hub owns the full lifecycle:

```
Goal → Plan → Task → Execution → Verification → Artifact
```

The worker is an **execution plane only**. It carries out delegated tasks within an explicit, Hub-signed `ExecutionEnvelope`. It cannot extend its own authority, re-plan at will, or write artifacts outside the envelope scope.

### 2. Capability model — Hub grants, Worker consumes

Every Hermes concept maps to a Hub-owned primitive:

| Hermes concept | Ananta-governed equivalent |
|---|---|
| Broad tool registry | `WorkerToolRegistry` — entries require `capability_class` + `risk_class` |
| Tool invocation | `ToolInvocationEnvelope` — validated before every call |
| Multi-provider LLM routing | `ModelPolicy` in `ExecutionEnvelope` — provider list is Hub-assigned |
| Persistent memory (read) | `memory_read` capability in `CapabilityGrant` |
| Persistent memory (write) | `memory_write` capability + approval ref when policy = `confirm_required` |
| Skills / self-improvement | Skill proposals go through Hub approval; no auto-install |
| Context compression | Worker may compress **within** context budget defined in `ContextEnvelope` |
| Subagent delegation | `subworker_spawn` capability required; Hub routes result back |
| Scheduled jobs | `cron_schedule` capability; Hub owns the job lifecycle |
| OpenAI-compatible API surface | Facade only; all requests map to Hub-approved task envelopes |
| MCP integration | `mcp_call` capability required per tool |

### 3. Stable vocabulary

| Term | Meaning |
|---|---|
| **Hub** | The Ananta control plane: owns policy, routing, approval, audit, artifact publication |
| **Worker** | Execution plane: runs tasks delegated by Hub inside an `ExecutionEnvelope` |
| **ExecutionEnvelope** | Single request object signed by Hub; defines every capability, scope, and policy for one task execution |
| **CapabilityGrant** | Immutable list of allowed capability classes for one execution; cannot be expanded at runtime |
| **ContextEnvelope** | Bounded, provenance-aware, sensitivity-filtered context block delivered by Hub |
| **TraceBundle** | Append-only audit record of all worker actions; returned in `WorkerResult` |
| **Artifact** | Structured, provenance-attached result of a meaningful side effect |
| **ToolRouterDecision** | Hub-side decision on which tool(s) the worker may invoke for a task |
| **ApprovalDecision** | Hub-side human or automated sign-off for approval-required operations |

### 4. Explicit rejections

The worker **must not**:
- Infer missing policy from prompt text.
- Encode capability checks only in natural-language system prompts.
- Route to cloud providers outside the `ModelPolicy` in the envelope.
- Auto-install or auto-activate skills without Hub approval.
- Create new authority for itself mid-execution (no capability escalation).
- Spawn subworkers or MCP tools without an explicit `subworker_spawn` / `mcp_call` capability.
- Write memory outside the scope defined in `ContextEnvelope`.

---

## Consequences

- Every worker execution starts with a preflight gate that validates the `ExecutionEnvelope`.
- Missing or ambiguous policy fails closed (deny by default).
- All side effects are emitted as `Artifact` entries in `WorkerResult`.
- `TraceBundle` is always returned, even on denial or error.
- Existing worker modes (`plan_only`, `patch_propose`, `patch_apply`, `command_plan`, `command_execute`, `test_run`, `verify`) are wrapped into `ExecutionEnvelope` during migration (see EW-T006).

---

## References

- `worker/core/execution_envelope.py` — canonical Pydantic models (EW-T007)
- `worker/core/preflight.py` — mandatory preflight gate (EW-T008)
- `docs/architecture/worker-responsibility-matrix.md` — EW-T002
- `docs/architecture/capability-vocabulary.md` — EW-T005
