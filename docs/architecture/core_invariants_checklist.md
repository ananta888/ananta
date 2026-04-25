# Core Invariants Checklist (OSS)

Use this checklist before merge/release claims for core control-plane behavior.

## 1. State ownership

- [ ] Every core state type has exactly one declared owner.
- [ ] Server-owned states do not allow direct client writers.
- [ ] Audit state is append-oriented and not mutable business state.

## 2. Policy and execution gate

- [ ] Every execution-like action runs through one policy + approval gatekeeper path.
- [ ] Repair/retry paths are treated as execution-like paths.
- [ ] No trusted shortcut allows direct client-to-worker execution.

## 3. Transition traceability

- [ ] Core transitions emit correlation and causation IDs.
- [ ] Execution start/result linkage is present and queryable.
- [ ] Artifact references are traceable to task and execution IDs.

## 4. Thin clients

- [ ] Clients submit bounded requests and render hub state.
- [ ] Clients do not orchestrate, approve, or execute backend tools directly.
- [ ] Denied/approval-required/degraded states are rendered honestly.

## 5. Explicit context

- [ ] Model/worker/tool calls include explicit context metadata.
- [ ] RAG chunks include source references and retrieval reason.
- [ ] Oversized/malformed context is rejected or degraded explicitly.

## 6. Lightweight reproducibility

- [ ] Artifacts include stable provenance references and hashes.
- [ ] Trace metadata links goal/plan/task/execution/policy/approval decisions.
- [ ] Reproducibility remains lightweight (no mandatory KRITIS replay stack).

## OSS vs KRITIS/Enterprise boundary

- [ ] OSS scope keeps core invariants enforceable without SSO, tenant RBAC, SIEM, WORM, or regulated evidence packs.
- [ ] KRITIS/Enterprise requirements are documented as additive extensions, not OSS blockers.
