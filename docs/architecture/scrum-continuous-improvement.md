# Scrum continuous improvement

Ananta models three related but separate Hub-owned feedback loops:

1. The Sprint control loop binds a distinct Sprint Goal, bounded task set,
   architecture handoff and accepted improvement commitments. It observes
   authoritative task state and may adjust only the Sprint Backlog. Changing
   the Sprint Goal or aborting the Sprint requires a separate, evidence-bound
   exception decision.
2. The architecture loop maintains immutable baselines across Sprints. An
   active revision projects only relevant guardrails into a Sprint. Delivery
   returns filtered architecture evidence and debt. Independently reviewed
   change proposals materialize a new draft revision; they never rewrite an
   active Sprint retroactively.
3. The retrospective loop binds process signals to Sprint snapshots, audit and
   artifact references. Product Owner, Scrum Master and Developer perspectives
   retain support, disagreement and alternative causes. Hypotheses do not claim
   causality. Reviewed improvements become measurable commitments in a later
   Sprint and regressions roll back automatically.

`ScrumStateStore` is an append-only SQLite revision store with optimistic
concurrency. `ScrumArchitectureLoopService`, `ScrumSprintControlService` and
`ScrumRetrospectiveService` each own one domain responsibility. The optional
`EvolutionRetrospectiveAnalysisAdapter` reuses the existing EvolutionEngine;
when a provider is unavailable, deterministic evidence analysis remains
available. No second evolution system is introduced.

The Hub owns all lifecycle, policy, backlog adjustment, baseline activation and
rollback decisions. Workers still execute only the normal tasks delegated by
the Hub. Architecture Governance provides constraints and reviewed revisions;
it cannot prioritize the Product Backlog. Protected Hub-core, queue ownership,
security-invariant and worker-orchestration targets are rejected as ordinary
process improvements and require a separate engineering change.

This separation protects SRP and DIP: persistence is replaceable, each control
loop depends on narrow services, and external analysis is behind a port. The
existing Hub task queue and EvolutionEngine remain the authoritative systems.
