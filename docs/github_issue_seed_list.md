# GitHub Issue Seed List (generated from todo.json)

This document mirrors the remaining tasks so they can be quickly converted into GitHub Issues or imported into a project board.

## Security

### BE-SEC-773
Apply artifact and trace access controls with least-privilege defaults.

### BE-SEC-774
Add tamper-evident audit coverage for goal, plan, policy and verification events.

## Backend Context / Migration

### BE-CTX-771
Separate task working context from project knowledge sources.

### BE-MIG-774
Add contract tests and migration docs for legacy task clients.

## Frontend Goal UX

### FE-GOAL-781
Add goal submission view with simple and advanced modes.

### FE-GOAL-782
Add goal detail page with linked plan, task, artifact and trace sections.

### FE-GOAL-783
Add plan inspection and adjustment UI for hub generated plans.

### FE-GOAL-785
Add artifact-first result summary with drill-down execution trace.

## Testing

### TEST-GOAL-794
Add execution isolation and workspace lifecycle tests.

### TEST-GOAL-795
Add verification and artifact traceability end-to-end tests.

### TEST-GOAL-796
Add frontend end-to-end tests for simple and advanced goal UX.

### TEST-GOAL-797
Add first-run happy path tests with default configuration only.

### TEST-GOAL-798
Add security regression tests for goal, plan, artifact and trace endpoints.

### TEST-HUB-799
Add orchestration tests for hub-as-worker fallback behaviour.

## Documentation

### DOC-GOAL-802
Document goal and plan APIs.

### DOC-GOAL-803
Document worker capability routing and policy explainability.

### DOC-GOAL-804
Document artifact result views and traceability model.

### DOC-GOAL-805
Document incremental migration from tasks to goals.

### DOC-GOAL-806
Document security defaults and governance visibility.

### DOC-HUB-807
Document hub-as-worker fallback semantics.

## Architecture

### ARCH-GOAL-812
Add sequence diagrams for goal ingestion, planning and verification.

### ARCH-GOAL-813
Document execution isolation and container boundary assumptions.

### ARCH-GOAL-814
Document separation between task context and project knowledge.

### ARCH-GOAL-815
Document observability model for traces, audits and policy records.

### ARCH-GOAL-816
Document default-first UX architecture with advanced-step disclosure.

### ARCH-HUB-817
Update architecture docs for delegate-first hub with worker fallback.

---

Suggested labels:
- backend
- frontend
- security
- testing
- docs
- architecture

Suggested milestone:
- v0.7 Goal Workflow
