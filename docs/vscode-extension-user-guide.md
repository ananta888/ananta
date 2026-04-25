# Ananta VS Code Extension User Guide

## Purpose

The VS Code extension is a thin client for the Ananta backend. It exposes command workflows, runtime views, and safe fallbacks without duplicating backend orchestration, policy, or approval logic.

## Install and run (development)

1. Open `client_surfaces/vscode_extension`.
2. Install dependencies: `npm ci`.
3. Build and test: `npm run ci:verify`.
4. Run smoke: `npm run test:smoke`.
5. Package VSIX (optional): `npm run package`.

Expected VSIX artifact:

`client_surfaces/vscode_extension/dist/ananta-vscode-extension.vsix`

## Settings, auth, and connection status

Core settings:

- `ananta.baseUrl`
- `ananta.profileId`
- `ananta.runtimeTarget`
- `ananta.auth.mode`
- `ananta.auth.secretStorageKey`
- `ananta.timeoutMs`

Auth token handling:

1. Run `Ananta: Store Auth Token` to persist token in SecretStorage.
2. Run `Ananta: Clear Auth Token` to remove token.

Connection/runtime status:

1. `Ananta: Check Backend Health` refreshes health + capabilities.
2. Status bar and **Status**/**Runtime** views show healthy/degraded/denied/unreachable states.
3. Degraded capability states explicitly disable blocked commands.

## Commands

Core workflow commands:

- `Ananta: Submit Goal`
- `Ananta: Analyze Selection`
- `Ananta: Review File`
- `Ananta: Patch Plan`
- `Ananta: Project New`
- `Ananta: Project Evolve`

Runtime/support commands:

- `Ananta: Refresh Sidebar Data`
- `Ananta: Filter Goals/Tasks by Status`
- `Ananta: Open Browser Fallback`
- `Ananta: Launch TUI in Terminal`

Detail and review commands:

- `Ananta: Open Goal/Task Detail`
- `Ananta: Open Artifact Detail`
- `Ananta: Open Approval Detail`
- `Ananta: Open Audit Detail`
- `Ananta: Open Repair Detail`
- `Ananta: Approve Approval Item`
- `Ananta: Reject Approval Item`

## Views

The extension contributes these activity-bar views:

- **Status**
- **Goals & Tasks**
- **Artifacts**
- **Approvals**
- **Audit**
- **Repair**
- **Runtime**

## Safety behavior and fallbacks

- Context capture is bounded, redacted, and previewed before submission.
- High-risk secret patterns block submission; warnings require explicit user confirmation.
- Approval/repair actions remain backend-gated and explicit.
- Browser fallback is available for tasks, artifacts, audit, config, and repair.
- TUI launch is explicit and user-triggered; token values are not passed on command line.
