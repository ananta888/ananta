# Ananta Eclipse Plugin User Guide

## Installation

1. Open **Help -> Install New Software...**
2. Add the Ananta update site URL (or local `ci-artifacts/eclipse/ananta-eclipse-update-site`).
3. Select the Ananta feature and complete installation.
4. Restart Eclipse.

## Initial setup

1. Open **Window -> Preferences -> Ananta**.
2. Configure:
   - **Profile ID**
   - **Hub Base URL**
   - **Auth Mode**
   - **Environment**
   - **Token (secure storage)**
   - **Timeout**
3. Click **Test Connection**.
4. Save with **Apply and Close**.

## Main views

- **Ananta Runtime Status**: health/capabilities + active profile.
- **Ananta Chat**: runtime-connected message surface.
- **Ananta Goal Panel**: goal preview with bounded context.
- **Ananta Task List / Task Detail**: task runtime surfaces.
- **Ananta Artifact View**: artifact runtime surface.
- **Ananta Approval Queue**: approval/rejection runtime flow.
- **Ananta Audit Explorer**: audit events with redacted preview.
- **Ananta Repair Explorer**: failed-task repair runtime surface.
- **Ananta Policy and Browser Fallback**: policy-denied guidance.
- **Ananta TUI Status**: bridge/handoff status.

## Commands

From command palette / keybindings:

- Ananta Analyze
- Ananta Review
- Ananta Patch Plan
- Ananta Project New
- Ananta Project Evolve
- Ananta Chat

Commands are routed through Hub-governed APIs and open the corresponding runtime views.

## Security behavior

- Tokens are persisted via Eclipse secure storage.
- Redaction is applied for sensitive log/preview text.
- Write/apply/import flows stay review-first and confirmation-gated.
