# Worker Capability Profiles

Worker capability profiles describe what a worker may execute after the hub delegates work. They do not allow workers to orchestrate other workers.

## Contract Version

- Version: `v1`
- Catalog builder: `WorkerCapabilityService.build_worker_capability_profiles`
- Boundary: hub-owned task queue
- Worker rule: execute delegated work only

## Profiles

### planner

- Roles: planner, hub-worker
- Scopes: goal planning, task breakdown, read context
- Tool classes: read, planning
- Limits: no direct worker delegation, no unreviewed mutation

### coder

- Roles: developer, worker
- Scopes: code change, test execution, artifact creation
- Tool classes: read, write, terminal
- Limits: hub-assigned tasks only, policy-gated terminal, review required for high risk

### reviewer

- Roles: reviewer, qa, security
- Scopes: review, verification, risk assessment
- Tool classes: read, analysis
- Limits: no direct mutation, evidence required

### operator

- Roles: ops, release
- Scopes: diagnostics, release readiness, runtime health
- Tool classes: read, terminal, admin
- Limits: admin required for mutation, audit required, least privilege

## Substitution Rule

Workers are substitutable only when their profile covers the required scopes, tool classes and governance fit. This keeps routing explicit and avoids hidden assumptions about a single-process environment.
