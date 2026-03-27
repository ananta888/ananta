# Additional Tasks Discovered During Architecture Review

These tasks were identified during analysis of the `newway_backend` branch and are not yet listed in `todo.json`.

## Security

### SEC-AUDIT-901
Add cryptographic hash chaining to audit log records so tampering with historical audit events can be detected.

### SEC-TEAM-902
Enforce strict team isolation in goal, plan, artifact and trace endpoints.

### SEC-WORKER-903
Require worker registration validation before accepting worker URLs in delegation or team membership.

## Governance

### GOV-POLICY-910
Add centralized policy evaluation layer for worker routing decisions.

### GOV-TRACE-911
Ensure every goal workflow entity shares a consistent `trace_id` for cross-component observability.

## Architecture

### ARCH-PLAN-920
Add explicit limits for plan generation (max nodes, max depth) to prevent runaway planning loops.

### ARCH-EXEC-921
Introduce execution resource guardrails for workers (time, memory, workspace size).

### ARCH-HUB-922
Add hub fallback provenance metadata when the hub executes tasks normally handled by workers.

## Reliability

### REL-QUEUE-930
Introduce queue-based task scheduling between hub and workers to prevent race conditions and improve retry handling.

### REL-RETRY-931
Implement structured retry policy with exponential backoff for worker task execution.

---

Suggested labels:
- security
- governance
- architecture
- reliability

Suggested milestone:
- v0.8 Hardening and Observability
