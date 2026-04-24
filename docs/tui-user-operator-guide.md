# Ananta TUI User and Operator Guide

## Purpose

Use the TUI for operational workflows: task/artifact handling, approval/audit visibility, KRITIS-oriented monitoring, and repair review.

## Startup

1. Select a connection profile.
2. Authenticate and confirm runtime header context.
3. Open the relevant view (`tasks`, `approvals`, `logs`, `kritis`, `settings`).

## Core views

- Task board and task detail
- Artifact list/detail
- Goal list and goal submission entry
- Approval queue and approval action flow
- Audit summary and trace drill-down
- KRITIS dashboard and repair session views
- Health/runtime diagnostics and provider/backend visibility

## Operator safety rules

- Review context before approval actions.
- Prefer dry-run-first where supported for repair flows.
- Use denial/audit views for governance debugging instead of bypassing policy.
- Keep actions explicit and auditable.

## Navigation

- Keyboard-first navigation model
- Cross-view search/filtering
- Resume state support for profile/last-view continuity
