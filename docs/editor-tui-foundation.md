# Editor and TUI foundation (shared line)

This document captures the shared foundation for the editor plugin and terminal UI track.

## Scope split

- Neovim plugin: coding, review, and explicit goal submission in developer context.
- TUI: operations, task and artifact handling, approvals, and audit-oriented workflows.
- Browser UI stays the fallback for deep configuration and full detail views.

## Shared contract baseline

The common surface inventory covers: auth, goals, tasks, artifacts, approvals, logs, health,
runtime diagnostics, and KRITIS visibility endpoints.

## First release architecture decisions

- Editor strategy: Neovim first, Vim compatibility later.
- Shared backend contract model is mandatory for all frontend surfaces.
- Capability and degraded-state handling are explicit and non-silent.
- Trace and audit links are required across task, artifact, approval, and log flows.

## Neovim foundation

Primary command surface:

- `AnantaGoalSubmit`
- `AnantaAnalyze`
- `AnantaReview`
- `AnantaPatchPlan`
- `AnantaProjectNew`
- `AnantaProjectEvolve`

Context capture is bounded (selection and buffer limits) and goal submission is user-triggered.

## Neovim core workflow layer

The current contract layer now also includes:

- Quick action palette and command wrappers for common operations.
- Analyze flow for file/project scope with editor-native rendering.
- Review flow for selection-oriented bounded context.
- Patch-planning flow with explicit review-first and no silent auto-apply.
- Task context view, artifact preview, context inspection panel, diff render and navigation links.
- Optional browser handoff shortcuts for deeper task/artifact/goal views.

## TUI foundation and core operational flows

The TUI contract baseline now captures:

- Framework decision and information architecture.
- Auth/session model for long-running terminal usage.
- Global layout and navigation conventions.
- Runtime status header with profile, endpoint and health signal.
- Task board and task detail views.
- Artifact list/detail and goal list/submission entry.
- Task filtering and grouping model.

## Neovim advanced operational awareness

The contract model now includes:

- Blueprint-aware project start commands.
- Approval-awareness for risky/blocked actions.
- Compact trace and diagnostic summary view.
- Knowledge/context source summary.
- First-run guided setup without mandatory browser detour.

## TUI logs, approvals, audit, KRITIS and usability layer

Additional contract models now cover:

- Log stream with filter support.
- Approval queue, approval detail/action flow and policy-denial visibility.
- Audit summary and audit trace drill-down (RBAC/redaction aware).
- KRITIS dashboard summary and repair session review contracts.
- Health/runtime diagnostics and provider/backend visibility.
- Keyboard refinement, cross-view search/filtering, safe resume state and explicit empty/error UX states.
