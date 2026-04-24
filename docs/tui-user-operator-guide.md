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

## Core views

- Dashboard
- Goals
- Tasks
- Artifacts
- Knowledge
- Config
- System
- Teams
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

## Navigation

- Keyboard-first navigation model
- Cross-view search/filtering
- Resume state support for profile/last-view continuity
- Navigation shell always shows current section and selected object context.
