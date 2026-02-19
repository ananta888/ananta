# Autonomous Platform Target Model

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
4. Execution with trace and guardrails.
5. Build/lint/test gates.
6. Completion or escalation.

## Control Plane Rules
- Delegation policy is validated server-side and versioned in code.
- Workers claim and complete tasks via orchestration API; queue ownership stays with hub.
- User and agent initiated tasks share the same ingestion contract.

## Safety Baseline
- Risky terminal actions require dry-run and policy check.
- Every execution has trace_id and provenance.
- Failed gates block completion and trigger repair or escalation.
