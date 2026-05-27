# KRITIS MutationGate Integrations (T06-T10)

## Scope

This document records how MutationGate is integrated into execution, evolution, artifact, and task-state mutation paths.

## T06 — Audit integration

MutationGate decisions emit structured audit events (`operation_type=mutation_gate_decision`) with:

- decision outcome (`allow`, `confirm_required`, `blocked`)
- reason code
- mutation class
- normalized target
- approval scope
- trace/task/goal linkage where available

Decision events are emitted for:

- task execution preflight
- normalized tool-call phase
- command-chain segment preflight
- evolution proposal apply path
- artifact mutation routes
- critical task-state mutation checks

## T07 — Pipeline stage

Critical execution pipelines expose a `mutation_gate` stage before write-like execution in task execution traces.

Chain execution uses segment preflight so denied segments are rejected before any segment runs.

## T08 — Evolver integration

`apply_persisted_proposal` in `EvolutionService` now passes through MutationGate before provider `apply(...)`.

No evolver apply call can proceed when MutationGate returns `blocked`/`confirm_required`.

## T09 — Shell and tool write paths

MutationGate is enforced for:

- direct command/tool execution preflight
- normalized tool-call execution (after intent remap)
- segment preflight for chained shell commands

In strict/safe governance, unknown high-risk mutation classification fails closed.

## T10 — Artifact and task-state mutation integration

Artifact mutation endpoints (`upload`, `extract`, `rag-index`) now evaluate MutationGate before mutation.

Task management path now checks MutationGate for critical task-state transitions (`completed`, `failed`, `blocked`, `cancelled`) while avoiding unnecessary gating for non-critical bookkeeping states.

