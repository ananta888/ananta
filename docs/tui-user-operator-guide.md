# Ananta TUI User and Operator Guide

## Purpose

Use the TUI for operational workflows: task/artifact handling, approval/audit visibility, KRITIS-oriented monitoring, and repair review.

## Startup

1. Select a connection profile.
2. Authenticate and confirm runtime header context.
3. Open the relevant view (`tasks`, `approvals`, `logs`, `kritis`, `settings`).

For local MVP runtime smoke:

`python -m client_surfaces.tui_runtime.ananta_tui --fixture`

For compact terminals and explicit section focus:

`python -m client_surfaces.tui_runtime.ananta_tui --fixture --section Tasks --terminal-width 80`

For selected-object drilldown and guarded actions:

`python -m client_surfaces.tui_runtime.ananta_tui --fixture --selected-goal-id G-1 --selected-task-id T-1 --selected-artifact-id A-1`

## Core views

- Dashboard
- Goals
- Tasks
- Artifacts
- Knowledge
- Config
- System
- Teams
- Instruction
- Automation
- Audit
- Repair
- Help

## Operator safety rules

- Review context before approval actions.
- Prefer dry-run-first where supported for repair flows.
- Use denial/audit views for governance debugging instead of bypassing policy.
- Keep actions explicit and auditable.
- Deep admin and high-risk operations stay browser-first via fallback links.
- Config edits from terminal are allowlisted and require explicit `--apply-safe-config`.
- Task patch/assign/propose/execute actions require explicit `--confirm-task-action`.
- Archived restore/cleanup/delete actions require explicit `--confirm-archived-action`.
- Artifact extract/index actions require explicit `--confirm-artifact-action`.
- Team activation requires explicit `--confirm-team-action`.
- Instruction profile/overlay selection and link/unlink actions require explicit `--confirm-instruction-action`.
- Automation start/stop/tick and planner/trigger configuration require explicit `--confirm-automation-action`.
- Artifact upload is intentionally deferred in terminal and handled via browser fallback.

## Navigation

- Keyboard-first navigation model
- Cross-view search/filtering
- Resume state support for profile/last-view continuity
- Navigation shell always shows current section and selected object context.
- Goal/task/artifact/knowledge/template selections are visible in the navigation header.

## Goal/task/artifact workflows

- Goal list/detail includes governance and plan tree context.
- Task workbench includes timeline and logs.
- Orchestration state is read-only in terminal (normal/blocked/failed/stale queues).
- Archived task actions are confirmation-gated.
- Artifact explorer includes detail, extract/index controls, RAG status, and RAG preview.

## Knowledge and templates

- Knowledge collections support inspect, explicit index action, and search (`query`, `top_k`).
- Templates support list/detail, variable registry, sample contexts, validation, diagnostics, and preview.
- Template writes remain browser-first unless a later guarded terminal flow is introduced.

## Teams, instruction layers, automation, audit

- Teams view includes blueprint catalog/detail, team types, role catalog, and role mapping for selected team types.
- Instruction view includes layer model, effective stack resolution, profile list, and overlay list.
- Automation view exposes autopilot/planner/trigger status plus explicit guarded actions (`autopilot_start|stop|tick`, `configure_planner`, `configure_triggers`).
- Audit view includes redacted message rendering, cross-entity references (task/goal/artifact/trace), and analyze summary.
