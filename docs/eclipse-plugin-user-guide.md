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

## Ananta Snake quick start

1. Open **Window -> Show View -> Other... -> Ananta Snake**.
2. In **Window -> Preferences -> Ananta**, configure:
   - **Snake enabled by default**
   - **Snake FPS**
   - **Snake follow distance**
   - **Snake overlay opacity**
   - **Force Local-only Mode**
   - **Enable Do-Not-Disturb Mode** (reduces activity and proactive actions)
3. Use the view buttons:
   - **Enable/Disable** toggles runtime
   - **Hide (Temporary)** hides overlay without losing state
   - **Toggle Presentation Mode** pauses visual activity
   - **Ask Ananta Snake** sends a bounded request to Hub/local runtime
   - **Reset Context Grants** restores privacy-safe defaults

## Ask-Ananta-Snake workflow

1. Open **Ananta Snake** view and verify `hub_connection` state.
2. Click **Ask Ananta Snake** and enter a prompt.
3. The response appears in the view as `ask_result=...`.
4. Context stays default-deny: file content is not sent unless explicitly allowed in preferences.

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
- Snake context sharing is default-deny for sensitive content.
- External provider context is blocked unless explicitly allowed by privacy settings.
- Local-only mode keeps Snake requests on local/hub-safe paths.

## Snake troubleshooting

- **Hub offline/local fallback:** check `hub_connection=offline` or `local_only` in Snake view, then verify Hub URL/token in preferences.
- **Overlay not visible:** ensure Snake is enabled, `Hide (Temporary)` is off, presentation mode is off, and overlay opacity is above 10%.
- **Input feels blocked:** Snake overlay is input-passthrough by design; if behavior differs, restart the view/Eclipse and verify plugin version.
