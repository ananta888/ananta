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
