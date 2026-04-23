# Generic Control Layer (Hub-governed)

## Purpose

This note defines a shared control layer for loop safety, tool routing, approvals and context management without changing the hub-worker architecture.

## Responsibility split

- **Hub (control plane):** owns routing policy, approval policy, loop policy, context policy, queue decisions and escalation.
- **Execution backends/workers:** execute delegated steps and emit structured signals and traces.
- **Optional specialized workers (for example ml-intern style):** bounded execution capabilities only, never orchestration ownership.

This keeps one orchestration system: the hub.

## Cross-cutting control modules

1. **Loop control:** detect repeated non-productive patterns and emit structured outcomes.
2. **Tool routing:** choose execution backend/tool by policy + capabilities + constraints.
3. **Approval governance:** classify actions into allow/confirm/block classes with explainable reasons.
4. **Context management:** assemble budgeted context and compaction decisions under one policy surface.

## Doom-loop target model

A doom loop is a repeated execution pattern with low or no task progress, detected from structured signals over a bounded lookback window.

Covered loop classes:

- repeated tool calls (same tool/signature repeatedly)
- repeated failures
- no-progress streaks
- oscillating retry patterns (A/B/A/B failure alternation)

Detector outputs are backend-agnostic and reusable across CLI, task execute, research/evolution and future workers.

## Loop signal contract

Each signal uses the same minimum fields:

- `task_id`
- `trace_id`
- `backend_name` (tool/backend identifier)
- `action_type`
- `failure_type`
- `iteration_count`

Optional enrichment:

- `action_signature`
- `progress_made`

Signals are persisted as structured execution history payloads so downstream diagnostics can evaluate runs consistently.

## Loop handling policy (conservative default)

Policy is configurable and normalized via `doom_loop_policy`:

- lookback window (`lookback_signals`)
- class thresholds (`repeated_tool_call_threshold`, `repeated_failure_threshold`, `no_progress_threshold`, `oscillation_threshold`)
- critical abort threshold (`critical_abort_threshold`)
- severity-to-action mapping (`severity_actions`)
- enforcement switch for terminal actions (`enforce_pause_abort`)

Default outcome ladder:

1. `warn`
2. `inject_correction`
3. `require_review`
4. `pause`
5. `abort`

By default, pause/abort are diagnostic recommendations unless explicit enforcement is enabled. Loop detections are emitted to execution history and audit logs.

## Companion design notes

- Loop correction injection: `docs/loop-correction-pattern.md`
- ToolRouter target architecture: `docs/tool-router-target-architecture.md`
- Unified approval model: `docs/unified-approval-model.md`
- ContextManager target model: `docs/context-manager-target-model.md`
- Optional ml-intern fit assessment: `docs/ml-intern-fit-assessment.md`
