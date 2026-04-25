# Ananta VS Code Extension (Runtime Bootstrap)

This extension is a thin client for the Ananta backend.  
It does not duplicate orchestration, approval, governance, or repair logic.

## Runtime commands

- `Ananta: Check Backend Health`
- `Ananta: Submit Goal` (quick mode picker for goal/patch/new/evolve)
- `Ananta: Analyze Selection`
- `Ananta: Review File`
- `Ananta: Patch Plan`
- `Ananta: Project New`
- `Ananta: Project Evolve`
- `Ananta: Refresh Sidebar Data`
- `Ananta: Filter Goals/Tasks by Status`
- `Ananta: Open Browser Fallback` (tasks/artifacts/audit/config/repair)
- `Ananta: Launch TUI in Terminal`

Commands are capability-gated using `/capabilities` handshake and rechecked at execution time.
Editor context payloads are bounded, previewed, and explicitly confirmed before send.

## Sidebar views

- **Status**: connection/capability diagnostics.
- **Goals & Tasks**: list + status filter + detail panel.
- **Artifacts**: list + read-only detail or browser fallback for binary/rich artifacts.
- **Approvals**: pending queue + explicit approve/reject actions (backend-permission aware).
- **Audit**: audit log summaries with related IDs and browser deep-analysis fallback.
- **Repair**: read-only repair visibility (diagnosis/steps/dry-run/approval/verification fields where provided).
- **Runtime**: summary counts, active profile/governance/provider/model diagnostics, refresh/TUI/fallback actions.

## Development

```bash
cd client_surfaces/vscode_extension
npm ci
npm run lint
npm run test
npm run compile
```

## Package (VSIX)

```bash
cd client_surfaces/vscode_extension
npm run package
```

The VSIX output path is:

`client_surfaces/vscode_extension/dist/ananta-vscode-extension.vsix`
