# Future Specialized Worker Guidance

## Goal

Keep Ananta generic and worker-agnostic while allowing domain-specific workers to be added incrementally.

## Admission criteria for new workers

1. Clear specialization (for example research, security review, data analysis)
2. Capability profile that can be consumed by hub routing and approval
3. Bounded execution contract with explicit limits
4. Observable health/diagnostics and auditable outcomes

## Non-negotiable architectural rules

- hub remains the control plane
- workers execute delegated work only
- no worker-to-worker orchestration
- no implicit trust escalation between workers

## Onboarding pattern

1. Define capability profile and risk class.
2. Add adapter boundary and contract tests.
3. Start behind feature flags.
4. Roll out with conservative governance mode defaults.
5. Expand only after observability and incident feedback are stable.

## Decommission pattern

Specialized workers must be removable without core architecture changes:

- isolate adapter logic
- keep shared contracts stable
- avoid coupling control logic to worker internals
