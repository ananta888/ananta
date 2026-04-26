# Autonomous Platform Target Model

> Lifecycle status: **canonical target model**.
> Supporting architecture context: `architektur/README.md`.
> Historical roadmap snapshot: `docs/architecture_roadmap_v0.7_to_v1.0.md`.

## Core Principle
All work items (from users in Angular UI and from agents) enter one central task system.
Agents do not exchange tasks peer-to-peer outside this control plane.

## Roles
- Hub: planner, router, gatekeeper, queue owner.
- Worker agents (alpha/beta/...): execute delegated work and report results.
- Human operator: policy, approval thresholds, intervention.

## Mandatory Flow
1. Task ingest to central queue.
2. Decomposition into subtasks.
3. Explainable CLI routing (Aider/OpenCode/SGPT/etc.).
4. Execution with explicit pipeline stages and trace metadata.
5. Build/lint/test gates.
6. Completion or escalation.

## Goal-first UX Architecture
- Default entry is a single goal field with safe defaults.
- Advanced controls are additive: constraints, acceptance criteria, routing preference and stricter policy flags.
- The UI shows artifact-first summaries before detailed execution internals.
- Plan nodes remain editable only through hub-approved adjustments. Workers do not re-plan other workers.

## Execution Isolation
- Hub and worker run in separate containers and must not assume shared mutable state.
- Delegated execution uses explicit scope metadata and isolated workspace lifecycle records.
- Cleanup and retry are observable events, not hidden implementation details.

## Observability Model
- `trace_id` links goals, plans, tasks, verification records and artifact summaries.
- Policy decisions and verification transitions are audit events with tamper-evident hash chaining.
- Non-admin views default to summary-level governance data; detailed records remain privileged.

## Control Plane Rules
- Delegation policy is validated server-side and versioned in code.
- Workers claim and complete tasks via orchestration API; queue ownership stays with hub.
- User and agent initiated tasks share the same ingestion contract.
- Local research and local OpenAI-compatible runtimes remain capabilities behind the same control plane, not parallel orchestration stacks.

## Safety Baseline
- Risky terminal actions require dry-run and policy check.
- Every execution has trace_id and provenance.
- Research backends can require explicit review before completion.
- Failed gates block completion and trigger repair or escalation.
